"""LiteLLMStreamFn — LLMStreamFn implementation backed by litellm."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.schema.llm.errors import LLMError, LLMErrorCode
from prismclaw.schema.llm.types import LLMResponse, LLMStreamChunk, LLMUsage
from prismclaw.engine.foundation.metrics import emit
from prismclaw.engine.services.llm.provider_resolver import (
    _connection_error_types,
    _should_retry_transient_provider_error,
    classify_error,
    format_vision_capable_models,
    messages_have_user_image_content,
    model_supports_input,
    resolve_credentials,
)

if TYPE_CHECKING:
    from prismclaw.schema.llm.provider_types import ProviderConfig

logger = logging.getLogger(__name__)

_CONTEXT_PAYLOAD_ROLE_TEXT = (
    "结构化运行时上下文载荷。请将其视为权威的机器生成上下文元数据，而不是用户散文。\n"
)
_CONTEXT_PAYLOAD_EXTENSION_KEY = "prism_context_payload"
_CONTEXT_PAYLOAD_TRANSPORT_KEY = "prism_context_transport"


# ---------------------------------------------------------------------------
# Request adapter helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderPreparedRequest:
    messages: list[dict[str, Any]]
    extensions: dict[str, Any]
    transport_mode: Literal["none", "structured_extension", "legacy_bridge"]


# vLLM / OpenAI 兼容端点下默认开启思考（reasoning）的模型前缀。这类模型在
# function-calling 结构化输出时若不关闭思考，会陷入长 reasoning 循环——持续吐
# reasoning_content 而不产出 content/tool_calls/stop，最终耗尽阶段预算
# （2026-10 glm-53 实证：divergent_directions 300s、mining_candidates_v3 720s
#  两阶段预算先后耗尽，日志 chunks 数万但 text_chars=0、finish_reason 空）。
# 统一经 chat_template_kwargs.enable_thinking=False 关闭（vLLM 通用约定，
# Qwen / GLM 系列均遵循）。
_THINKING_CONTROLLED_MODEL_PREFIXES = ("qwen", "glm")


def _should_disable_thinking(
    *,
    model: str,
    llm_provider: str | None,
    api_base: str | None,
) -> bool:
    """判断是否需要关闭模型的思考模式（/no_think）。

    覆盖场景：
    - llm_provider="openai" 且非阿里云 DashScope → 关闭（本地 vLLM / OpenAI 兼容端点）
    - llm_provider=None（fallback 模式，无 providers dict）→ 关闭
    - DashScope 官方端点 → 不关闭（原生支持思考模式控制）
    """
    if not model.lower().startswith(_THINKING_CONTROLLED_MODEL_PREFIXES):
        return False
    normalized_base = (api_base or "").lower()
    if "dashscope" in normalized_base:
        return False
    if (llm_provider or "").lower() == "openai":
        return True
    if llm_provider is None:
        # fallback 模式：无 providers dict，通常是本地 vLLM 等兼容端点
        return True
    return False


# llm_request_start 的输入规模估算：中英混合文本约 1 token / 3 字节
# （工具 schema 亦计入——tools 是每轮固定开销的大头，需要可见）。
def _estimate_input_tokens(messages: list[Any], tools: list[Any]) -> int:
    import json

    def _size(obj: Any) -> list[int]:
        try:
            payload = json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            payload = str(obj)
        return len(payload.encode("utf-8"))

    total = sum(_size(m) for m in messages) + sum(_size(t) for t in tools)
    return total // 3


class ProviderRequestAdapter:
    def adapt(
        self,
        *,
        request: Any,
        llm_provider: str | None,
        prompt_cache_runtime: Any | None,
    ) -> ProviderPreparedRequest:
        transport_mode = self._select_transport_mode(
            request=request,
            llm_provider=llm_provider,
        )
        if transport_mode == "none":
            prepared_messages = _prepend_system(
                request.messages,
                request.system,
                provider_name=llm_provider,
            )
            return ProviderPreparedRequest(
                messages=prepared_messages,
                extensions={},
                transport_mode=transport_mode,
            )

        context_payload = request.context_payload
        assert context_payload is not None

        if transport_mode == "structured_extension":
            block_count = len(context_payload.get("blocks") or [])
            prepared_messages = _prepend_system(
                request.messages,
                request.system,
                provider_name=llm_provider,
            )
            return ProviderPreparedRequest(
                messages=prepared_messages,
                extensions={
                    "extra_body": {
                        _CONTEXT_PAYLOAD_EXTENSION_KEY: context_payload,
                    },
                    "metadata": {
                        _CONTEXT_PAYLOAD_TRANSPORT_KEY: "extra_body",
                        "prism_context_schema": str(context_payload.get("schema_version") or ""),
                        "prism_context_block_count": block_count,
                    },
                },
                transport_mode=transport_mode,
            )

        prepared_messages = self._adapt_with_legacy_context_bridge(
            request=request,
            llm_provider=llm_provider,
            prompt_cache_runtime=prompt_cache_runtime,
        )
        return ProviderPreparedRequest(
            messages=prepared_messages,
            extensions={},
            transport_mode=transport_mode,
        )

    def _select_transport_mode(
        self,
        *,
        request: Any,
        llm_provider: str | None,
    ) -> Literal["none", "structured_extension", "legacy_bridge"]:
        if not request.context_payload:
            return "none"
        if _provider_supports_structured_context_extension(llm_provider):
            return "structured_extension"
        return "legacy_bridge"

    def _adapt_with_legacy_context_bridge(
        self,
        *,
        request: Any,
        llm_provider: str | None,
        prompt_cache_runtime: Any | None,
    ) -> list[dict[str, Any]]:
        from prismclaw.schema.llm.cache_types import EPHEMERAL_CACHE_CONTROL
        from prismclaw.schema.llm.request_format import (
            inject_cache_control_breakpoints,
            render_context_payload_text,
        )

        assert request.context_payload is not None
        payload_text = _CONTEXT_PAYLOAD_ROLE_TEXT + render_context_payload_text(
            request.context_payload
        )

        reminder_msg = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<system-context>\n{payload_text}\n</system-context>",
                    "cache_control": EPHEMERAL_CACHE_CONTROL,
                }
            ],
        }

        injected_messages = list(request.messages)
        # F3（D2，sdd-convergence-efficiency）：reminder 恒尾部追加——位置
        # 永久稳定，整段历史前缀逐字节可缓存；insert(-1) 会把 reminder 插在
        # 当前 user 消息之前，逐轮漂移并使其后内容失去可缓存性。reminder
        # 位于生成点最近处，上下文提醒语义等价。
        injected_messages.append(reminder_msg)

        if _should_apply_cache_breakpoints(prompt_cache_runtime):
            injected_messages = inject_cache_control_breakpoints(injected_messages)

        prepared_messages = _prepend_system(
            injected_messages,
            request.system,
            provider_name=llm_provider,
        )
        return prepared_messages


_PROVIDER_REQUEST_ADAPTER = ProviderRequestAdapter()


def _prepend_system(
    messages: list[dict[str, Any]],
    system: str | list[dict[str, Any]] | None,
    *,
    provider_name: str | None = None,
) -> list[dict[str, Any]]:
    if not system:
        return messages
    return [
        {
            "role": "system",
            "content": _normalize_system_content_for_provider(
                system,
                provider_name=provider_name,
            ),
        },
        *messages,
    ]


def _normalize_system_content_for_provider(
    system: str | list[dict[str, Any]],
    *,
    provider_name: str | None,
) -> str | list[dict[str, Any]]:
    if not _provider_prefers_block_system_messages(provider_name):
        return system
    if isinstance(system, list):
        return list(system)
    return [{"type": "text", "text": system}]


def _provider_supports_structured_context_extension(provider_name: str | None) -> bool:
    import os

    mode = str(os.environ.get("PRISMCLAW_LLM_CONTEXT_TRANSPORT", "legacy")).strip().lower()
    if mode in {"structured", "extension", "structured_extension"}:
        return True
    if mode in {"legacy", "bridge", "text"}:
        return False
    normalized = str(provider_name or "").strip().lower()
    return mode == "auto" and normalized in {"openai", "anthropic"}


def _provider_prefers_block_system_messages(provider_name: str | None) -> bool:
    normalized = str(provider_name or "").strip().lower()
    return normalized == "openai"


def _should_apply_cache_breakpoints(prompt_cache_runtime: Any | None) -> bool:
    if prompt_cache_runtime is None:
        return False
    if isinstance(prompt_cache_runtime, Mapping):
        return bool(
            prompt_cache_runtime.get("pending_post_compaction")
            or prompt_cache_runtime.get("cache_deletions_pending")
            or prompt_cache_runtime.get("cache_editing_header_latched")
            or prompt_cache_runtime.get("thinking_clear_latched")
        )
    return bool(
        getattr(prompt_cache_runtime, "pending_post_compaction", False)
        or getattr(prompt_cache_runtime, "cache_deletions_pending", False)
        or getattr(prompt_cache_runtime, "cache_editing_header_latched", False)
        or getattr(prompt_cache_runtime, "thinking_clear_latched", False)
    )


def _sanitize_tool_call_arguments_json(tool_calls: Any) -> Any:
    if not isinstance(tool_calls, list):
        return tool_calls
    out: list[dict[str, Any]] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            out.append(tc)
            continue
        args = fn.get("arguments")
        if isinstance(args, dict):
            args_str = json.dumps(args, ensure_ascii=False)
        elif isinstance(args, str):
            raw = args.strip() or "{}"
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "sanitize_tool_args_json_decode_failed bytes=%d",
                    len(raw),
                )
                parsed = {}
            args_str = json.dumps(parsed, ensure_ascii=False)
        else:
            args_str = "{}"

        tc2 = dict(tc)
        fn2 = dict(fn)
        fn2["arguments"] = args_str
        tc2["function"] = fn2
        out.append(tc2)
    return out


# ---------------------------------------------------------------------------
# Chunk / usage conversion helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_usage(raw: Any) -> LLMUsage | None:
    usage = _get(raw, "usage")
    if usage is None:
        return None
    prompt = _get(usage, "prompt_tokens") or _get(usage, "input_tokens") or 0
    completion = _get(usage, "completion_tokens") or _get(usage, "output_tokens") or 0
    total = _get(usage, "total_tokens") or (prompt + completion)

    cache_creation = 0
    cache_read = 0
    details = _get(usage, "prompt_tokens_details")
    if details is not None:
        cache_read = int(_get(details, "cached_tokens") or 0)
        cache_creation = int(_get(details, "cache_creation_tokens") or 0)
    if not cache_read:
        cache_read = int(_get(usage, "cache_read_input_tokens") or 0)
    if not cache_creation:
        cache_creation = int(_get(usage, "cache_creation_input_tokens") or 0)

    reasoning = 0
    completion_details = _get(usage, "completion_tokens_details")
    if completion_details is not None:
        reasoning = int(_get(completion_details, "reasoning_tokens") or 0)
    if not reasoning:
        reasoning = int(_get(usage, "reasoning_tokens") or 0)

    return LLMUsage(
        input_tokens=int(prompt),
        output_tokens=int(completion),
        total_tokens=int(total),
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        reasoning_tokens=reasoning,
    )


def _normalize_tool_call_delta(delta: Any) -> dict[str, Any]:
    return {
        "index": _get(delta, "index") or 0,
        "id": _get(delta, "id"),
        "type": _get(delta, "type") or "function",
        "function": {
            "name": _get(_get(delta, "function"), "name"),
            "arguments": _get(_get(delta, "function"), "arguments") or "",
        },
    }


def _convert_chunk(raw: Any) -> LLMStreamChunk | None:
    try:
        choices = getattr(raw, "choices", None) or raw.get("choices")
    except Exception:
        return None
    if not choices:
        usage = _extract_usage(raw)
        if usage is not None:
            return LLMStreamChunk(usage=usage)
        return None

    choice = choices[0]
    delta = _get(choice, "delta") or {}
    text_delta = _get(delta, "content") or ""
    reasoning_delta = (
        _get(delta, "reasoning_content")
        or _get(delta, "reasoning")
        or _get(delta, "thinking")
        or ""
    )
    tool_call_deltas = _get(delta, "tool_calls") or []
    finish_reason = _get(choice, "finish_reason")

    normalized_deltas = [_normalize_tool_call_delta(d) for d in tool_call_deltas]
    return LLMStreamChunk(
        text_delta=text_delta or "",
        reasoning_delta=reasoning_delta or "",
        tool_call_deltas=normalized_deltas,
        finish_reason=finish_reason,
        usage=_extract_usage(raw),
    )


def _convert_nonstream_response(raw: Any) -> LLMStreamChunk | None:
    try:
        choices = getattr(raw, "choices", None) or raw.get("choices")
    except Exception:
        return None
    if not choices:
        usage = _extract_usage(raw)
        if usage is not None:
            return LLMStreamChunk(usage=usage, finish_reason="stop")
        return None

    choice = choices[0]
    msg = _get(choice, "message") or {}
    text = _get(msg, "content") or ""
    reasoning = (
        _get(msg, "reasoning_content")
        or _get(msg, "reasoning")
        or _get(msg, "thinking")
        or ""
    )
    tool_calls = _get(msg, "tool_calls") or []
    finish_reason = _get(choice, "finish_reason") or _get(raw, "finish_reason") or "stop"

    normalized_calls: list[dict[str, Any]] = []
    if isinstance(tool_calls, list):
        for i, tc in enumerate(tool_calls):
            if not isinstance(tc, Mapping):
                continue
            normalized_calls.append(
                _normalize_tool_call_delta(
                    {
                        "index": i,
                        "id": _get(tc, "id"),
                        "type": _get(tc, "type") or "function",
                        "function": {
                            "name": _get(_get(tc, "function"), "name"),
                            "arguments": _get(_get(tc, "function"), "arguments") or "",
                        },
                    }
                )
            )

    return LLMStreamChunk(
        text_delta=text or "",
        reasoning_delta=reasoning or "",
        tool_call_deltas=normalized_calls,
        finish_reason=finish_reason,
        usage=_extract_usage(raw),
    )


# ---------------------------------------------------------------------------
# LiteLLMStreamFn
# ---------------------------------------------------------------------------

def compute_max_tokens_clamp(
    *,
    model_max_tokens: int | None,
    context_window: int | None,
    estimated_input_tokens: int | None = None,
    headroom: int = 1024,
) -> tuple[int | None, int | None]:
    """计算 max_tokens 硬钳制值及其依据的输入估算。

    不变量：``max_tokens ≤ context_window − 估算输入 − headroom``，
    保证单次请求 input + output 不越窗。注意 input_budget 是预算判定量
    （驱动压缩触发/压力埋点），不是窗口几何量，不参与本钳制。
    仅向下钳制（safe_max < model_max 时生效），返回 (clamped, est_input)。
    """
    if not model_max_tokens:
        return model_max_tokens, estimated_input_tokens
    if not context_window:
        return int(model_max_tokens), estimated_input_tokens
    safe_max = max(0, int(context_window) - int(estimated_input_tokens or 0) - headroom)
    if safe_max < int(model_max_tokens):
        return int(safe_max), estimated_input_tokens
    return int(model_max_tokens), estimated_input_tokens


def _report_context_window_pressure(
    *,
    est_input: int,
    input_budget: int | None,
    context_window: int | None,
    model_max_tokens: int | None,
) -> bool:
    """超窗计数/预警埋点（G4）。

    当估算输入超过 input_budget，或估算输入 + max_output 超过窗口时，
    打 warning 并计数。纯函数，便于单测断言日志字段。
    """
    over_budget = bool(
        input_budget is not None
        and int(input_budget) > 0
        and est_input > int(input_budget)
    )
    over_window = bool(
        context_window is not None
        and model_max_tokens is not None
        and int(context_window) > 0
        and (est_input + int(model_max_tokens)) > int(context_window)
    )
    if over_budget or over_window:
        logger.warning(
            "context_window_pressure est_input=%d input_budget=%s window=%s "
            "max_output=%s over_budget=%s over_window=%s",
            est_input,
            input_budget,
            context_window,
            model_max_tokens,
            "yes" if over_budget else "no",
            "yes" if over_window else "no",
        )
        emit(
            "streaming.llm.context_window_pressure",
            1.0,
            kind="counter",
            labels={"condition": "over_budget" if over_budget else "over_window"},
        )
    return over_budget or over_window


class LiteLLMStreamFn:
    """Concrete LLMStreamFn implementation backed by litellm."""

    def __init__(
        self,
        default_model: str = "",
        api_key: str | None = None,
        api_base: str | None = None,
        *,
        providers: Mapping[str, ProviderConfig] | None = None,
        default_provider: str | None = None,
        unknown_prefix_policy: Literal["fail", "default"] = "fail",
    ) -> None:
        from prismclaw.engine.services.llm.provider_resolver import _normalize_base_url

        self.default_model = default_model
        if providers:
            self._providers: dict[str, ProviderConfig] = dict(providers)
            self._fallback_key: str | None = None
            self._fallback_base: str | None = None
        else:
            self._providers = {}
            self._fallback_key = api_key
            self._fallback_base = _normalize_base_url(api_base) if api_base else None
        self._default_provider = default_provider
        self._unknown_prefix_policy: Literal["fail", "default"] = unknown_prefix_policy

    async def stream(
        self,
        *,
        request: Any,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        prompt_cache_runtime: Any | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        from litellm import acompletion

        from prismclaw.schema.shared import AgentAbortedError, AgentTimeoutError
        from prismclaw.schema.shared import iterate_with_idle_timeout

        effective_model = model or self.default_model
        api_key, api_base, _provider_name, llm_provider = resolve_credentials(
            effective_model,
            self._providers,
            self._fallback_key,
            self._fallback_base,
            self._default_provider,
            self._unknown_prefix_policy,
        )
        prepared_request = _PROVIDER_REQUEST_ADAPTER.adapt(
            request=request,
            prompt_cache_runtime=prompt_cache_runtime,
            llm_provider=llm_provider,
        )
        payload_messages = prepared_request.messages
        request_extensions = prepared_request.extensions

        # We assume messages are clean by the time they reach LiteLLMStreamFn.
        # RequestPackager is responsible for stripping internal metadata.
        for m in payload_messages:
            if isinstance(m, dict) and m.get("tool_calls") is not None:
                m["tool_calls"] = _sanitize_tool_call_arguments_json(m.get("tool_calls"))

        if api_base:
            cleaned = api_base.replace("`", "").strip()
            cleaned = "".join(cleaned.split())
            if cleaned != api_base:
                logger.warning("sanitized api_base at call-time: %r -> %r", api_base, cleaned)
                api_base = cleaned or None

        if self._providers and messages_have_user_image_content(payload_messages):
            if not model_supports_input(
                effective_model,
                self._providers,
                "image",
                default_provider=self._default_provider,
                unknown_prefix_policy=self._unknown_prefix_policy,
            ):
                vision_models_str = format_vision_capable_models(self._providers)
                raise LLMError(
                    LLMErrorCode.VISION_NOT_SUPPORTED,
                    f"模型 '{effective_model}' 不具备图片输入能力。"
                    f"支持视觉输入的模型：{vision_models_str}。"
                    f"可使用 /model <model_id> 切换模型。",
                )

        extra: dict[str, Any] = {}
        if api_key:
            extra["api_key"] = api_key
        if api_base:
            extra["api_base"] = api_base
        if llm_provider:
            extra["custom_llm_provider"] = llm_provider
        if temperature is not None:
            extra["temperature"] = temperature
        extra.update(request_extensions)
        _thinking_disabled = _should_disable_thinking(
            model=effective_model,
            llm_provider=llm_provider,
            api_base=api_base,
        )
        if _thinking_disabled:
            # litellm 自动将非白名单参数透传至 extra_body，无需手动嵌套合并
            extra["chat_template_kwargs"] = {"enable_thinking": False}
            logger.info(
                "thinking_disable model=%s provider=%s api_base=%s — "
                "下发了 enable_thinking=False；若请求仍产 reasoning token，"
                "说明端点/litellm 未透传或该模型版本强制思考",
                effective_model,
                llm_provider or "",
                api_base or "",
            )
        # effort → 推理强度：litellm 统一参数 reasoning_effort（low/medium/high）。
        # 与强制关思考互斥——关思考时下发会与 enable_thinking=False 冲突，故跳过。
        if reasoning_effort and not _thinking_disabled:
            extra["reasoning_effort"] = reasoning_effort

        # max_tokens 硬上限：按模型配置下发，并钳制到「窗口 − 估算输入 − 余量」。
        # 旧实现直接下发 max_output_tokens=131072（=整窗），prompt 50k + 131k > 窗口
        # 会触发服务端校验失败或 KV 预留排队。钳制后单次请求不再越过窗口。
        try:
            from prismclaw.engine.execution.request.model_fallback import get_model_spec

            _spec = get_model_spec(effective_model)
            _model_max_tokens = getattr(_spec, "max_output_tokens", None)
            _context_window = getattr(_spec, "context_window", None)
        except Exception:
            _model_max_tokens = None
            _context_window = None
        if _model_max_tokens:
            _input_budget = getattr(request, "input_budget", None)
            # 估算一次，钳制与压力埋点共用（压力埋点原本重复估算一次）。
            _est_in = _estimate_input_tokens(payload_messages, tools or [])
            _clamped, _est_in = compute_max_tokens_clamp(
                model_max_tokens=_model_max_tokens,
                context_window=_context_window,
                estimated_input_tokens=_est_in,
            )
            if _clamped != _model_max_tokens:
                logger.info(
                    "max_tokens clamp: model_max=%d est_input=%s input_budget=%s window=%s "
                    "→ capped=%d",
                    _model_max_tokens, _est_in, _input_budget, _context_window, _clamped,
                )
            # G4：超窗计数/预警（估算输入超 input_budget 或 +max_output 超窗口）。
            try:
                _report_context_window_pressure(
                    est_input=_est_in,
                    input_budget=_input_budget,
                    context_window=_context_window,
                    model_max_tokens=_model_max_tokens if _model_max_tokens else None,
                )
            except Exception:
                pass
            _model_max_tokens = _clamped
            extra["max_tokens"] = _model_max_tokens

        # Apply fallback timeout explicitly, 120s by default.
        # 若请求显式携带 timeout_s（如科研挖掘大上下文），以之为准；否则维持 120s 兜底。
        fallback_timeout_s = 120.0
        _req_timeout = getattr(request, "timeout_s", None)
        request_timeout_s = float(_req_timeout) if _req_timeout else fallback_timeout_s

        import secrets

        request_id = secrets.token_hex(4)

        # D2 前缀指纹诊断（sdd-convergence-efficiency §4.2）：逐请求记录
        # tools 载荷与 system 文本的 sha256 摘要。跨轮 digest 不变而
        # cache_read 仍为 0 → 服务端 APC 问题；digest 漂移 → 客户端前缀
        # 不稳定。canonical 序列化（sort_keys + 紧凑分隔符）即 wire 纪律。
        import hashlib as _hashlib
        import json as _json

        _tools_digest = _hashlib.sha256(
            _json.dumps(
                tools or [], sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), default=str,
            ).encode()
        ).hexdigest()[:16]
        _system_payload = request.system
        _system_text = (
            _system_payload
            if isinstance(_system_payload, str)
            else _json.dumps(
                _system_payload, sort_keys=True, ensure_ascii=False, default=str,
            )
        )
        _system_digest = _hashlib.sha256(_system_text.encode()).hexdigest()[:16]

        # F5：请求级缓存路由键（OpenAI 兼容字段 prompt_cache_key；vLLM 忽略
        # 无害，对多副本/网关部署是路由粘性前提）。仅主链路 packing 透传
        # session_id；评审/摘要类辅助请求为 None——不与主缓存前缀粘连（P-7）。
        _session_id = getattr(request, "session_id", None)
        if _session_id:
            extra["prompt_cache_key"] = str(_session_id)[:64]
        call_started: float | None = None
        stream = None
        last_exc: Exception | None = None
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                if call_started is None:
                    call_started = time.monotonic()
                    roles: list[str] = []
                    for m in payload_messages:
                        if isinstance(m, dict):
                            roles.append(str(m.get("role") or ""))
                        else:
                            roles.append(type(m).__name__)
                    role_counts = Counter(r for r in roles if r)
                    role_counts_str = ",".join(
                        f"{k}={v}" for k, v in sorted(role_counts.items(), key=lambda kv: kv[0])
                    )
                    last_role = roles[-1] if roles else "none"
                    any_user = any(r == "user" for r in roles)
                    last_user = next(
                        (m for m in reversed(payload_messages) if isinstance(m, dict) and m.get("role") == "user"),
                        None,
                    )
                    last_user_content = (
                        last_user.get("content") if isinstance(last_user, dict) else None
                    )
                    if isinstance(last_user_content, str):
                        last_user_kind = "text"
                        last_user_chars = len(last_user_content.strip())
                    elif isinstance(last_user_content, list):
                        last_user_kind = "blocks"
                        last_user_chars = len(last_user_content)
                    else:
                        last_user_kind = "none"
                        last_user_chars = 0
                    logger.info(
                        "llm_request_start request_id=%s model=%s provider=%s messages=%d tools=%d est_in_tok=%d request_timeout_s=%.1f roles=%s last_role=%s any_user=%s last_user_kind=%s last_user_size=%d prefix_digest=%s/%s",
                        request_id,
                        effective_model,
                        llm_provider or "",
                        len(payload_messages),
                        len(tools or []),
                        _estimate_input_tokens(payload_messages, tools or []),
                        request_timeout_s,
                        role_counts_str,
                        last_role,
                        "yes" if any_user else "no",
                        last_user_kind,
                        last_user_chars,
                        _tools_digest,
                        _system_digest,
                    )
                # WP-3.1 — race against abort_event so an in-flight
                # cancel is honoured before ``request_timeout_s``.
                from prismclaw.schema.shared import await_or_abort

                stream = await await_or_abort(
                    acompletion(
                        model=effective_model,
                        messages=payload_messages,
                        tools=tools or None,
                        stream=True,
                        stream_options={"include_usage": True},
                        **extra,
                    ),
                    abort_event=abort_event,
                    timeout=request_timeout_s,
                )
                last_exc = None
                break
            except asyncio.TimeoutError as exc:
                last_exc = exc
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts - 1 and _should_retry_transient_provider_error(exc):
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                break

        if stream is None:
            if isinstance(last_exc, asyncio.TimeoutError):
                logger.warning(
                    "llm_request_timeout request_id=%s model=%s provider=%s request_timeout_s=%.1f",
                    request_id,
                    effective_model,
                    llm_provider or "",
                    request_timeout_s,
                )
                raise LLMError(
                    LLMErrorCode.TIMEOUT,
                    f"LLM 请求在 {request_timeout_s}s 后超时"
                    f"（model='{effective_model}'，base_url='{api_base or ''}'）",
                    original=last_exc,
                ) from last_exc
            if last_exc is not None and _should_retry_transient_provider_error(last_exc):
                from prismclaw.schema.shared import await_or_abort

                try:
                    raw = await await_or_abort(
                        acompletion(
                            model=effective_model,
                            messages=payload_messages,
                            tools=tools or None,
                            stream=False,
                            **extra,
                        ),
                        abort_event=abort_event,
                        timeout=request_timeout_s,
                    )
                    chunk = _convert_nonstream_response(raw)
                    if chunk is None:
                        raise last_exc
                    logger.info(
                        "llm_request_end request_id=%s model=%s provider=%s mode=nonstream",
                        request_id,
                        effective_model,
                        llm_provider or "",
                    )
                    yield chunk
                    return
                except Exception:
                    pass
            raise LLMError(
                classify_error(last_exc or Exception("unknown")),
                f"{str(last_exc or '')} (model='{effective_model}', base_url='{api_base or ''}', provider='{llm_provider or ''}')",
                original=last_exc,
            ) from last_exc

        first_text_at: float | None = None
        # 首个 chunk 到达（含 reasoning / tool_call delta）——这才是"排队+prefill"
        # 的可观测代理；first_text_at 只在正文 delta 上打点，会把 reasoning 生成
        # 时间一并算进"首字"，导致 prefill 被高估（实测日志 ttfb 占时长 88% 即此故）。
        first_any_at: float | None = None
        last_chunk_at: float | None = None
        finish_reason: str | None = None
        chunk_count = 0
        text_chars = 0
        reasoning_chars = 0
        last_usage: Any = None

        async def _raw_iter() -> AsyncIterator[LLMStreamChunk]:
            nonlocal first_text_at, first_any_at, last_chunk_at, finish_reason
            nonlocal chunk_count, text_chars, reasoning_chars, last_usage
            async for raw_chunk in stream:
                chunk = _convert_chunk(raw_chunk)
                if chunk is not None:
                    now = time.monotonic()
                    last_chunk_at = now
                    chunk_count += 1
                    if first_any_at is None:
                        first_any_at = now
                    if chunk.reasoning_delta:
                        reasoning_chars += len(chunk.reasoning_delta)
                    if chunk.usage is not None:
                        last_usage = chunk.usage
                    if chunk.text_delta:
                        text_chars += len(chunk.text_delta)
                    if first_text_at is None and chunk.text_delta:
                        first_text_at = now
                        started = call_started or first_text_at
                        logger.info(
                            "llm_ttfb_ms=%.0f model=%s provider=%s",
                            (first_text_at - started) * 1000.0,
                            effective_model,
                            llm_provider or "",
                        )
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    yield chunk

        started = call_started or time.monotonic()
        logged_end = False
        cancelled = False
        try:
            if idle_seconds <= 0 and abort_event is None:
                async for chunk in _raw_iter():
                    yield chunk
            else:
                async for chunk in iterate_with_idle_timeout(
                    _raw_iter(),
                    idle_seconds=idle_seconds,
                    abort_event=abort_event,
                    kind="idle",
                ):
                    yield chunk
        except AgentTimeoutError as te:
            now = time.monotonic()
            since_start_s = max(0.0, now - started)
            since_last_chunk_s = (
                max(0.0, now - last_chunk_at) if last_chunk_at is not None else None
            )
            logger.warning(
                "llm_stream_idle_timeout request_id=%s idle_seconds=%.1f since_start_s=%.1f since_last_chunk_s=%s model=%s provider=%s got_first_text=%s chunks=%d text_chars=%d finish_reason=%s",
                request_id,
                te.limit_seconds,
                since_start_s,
                f"{since_last_chunk_s:.1f}" if since_last_chunk_s is not None else "none",
                effective_model,
                llm_provider or "",
                "yes" if first_text_at is not None else "no",
                chunk_count,
                text_chars,
                finish_reason or "",
            )
            logged_end = True
            raise LLMError(LLMErrorCode.TIMEOUT, f"idle timeout after {te.limit_seconds}s") from te
        except AgentAbortedError as ae:
            logged_end = True
            logger.info(
                "llm_request_end request_id=%s model=%s provider=%s status=aborted chunks=%d text_chars=%d finish_reason=%s",
                request_id,
                effective_model,
                llm_provider or "",
                chunk_count,
                text_chars,
                finish_reason or "",
            )
            raise LLMError("aborted", "llm stream aborted") from ae
        except _connection_error_types() as ce:
            # 流式响应在传输层被截断（代理/上游切断 SSE 流）：aiohttp
            # TransferEncodingError → httpx.ReadError → litellm
            # APIConnectionError → MidStreamFallbackError。这是 transient 的
            # external 连接错误，包装成 LLMError(CONNECTION_ERROR) 交给 pipeline
            # 的 connection_error 重试回路接管，而非裸抛成 Internal error。
            logger.warning(
                "llm_stream_connection_error request_id=%s model=%s provider=%s chunks=%d text_chars=%d: %s",
                request_id,
                effective_model,
                llm_provider or "",
                chunk_count,
                text_chars,
                str(ce)[:300],
            )
            raise LLMError(
                LLMErrorCode.CONNECTION_ERROR,
                f"流式传输连接错误：{str(ce)[:300]} "
                f"（model='{effective_model}'，provider='{llm_provider or ''}'）",
                original=ce,
            ) from ce
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not logged_end:
                ended_at = time.monotonic()
                duration_s = max(0.0, ended_at - started)
                ttfb_ms = (
                    max(0.0, (first_text_at - started) * 1000.0) if first_text_at else None
                )
                logger.info(
                    "llm_request_end request_id=%s model=%s provider=%s status=%s duration_s=%.1f "
                    "first_chunk_ms=%s ttfb_ms=%s reasoning_chars=%d chunks=%d text_chars=%d "
                    "in_tok=%s out_tok=%s reason_tok=%s finish_reason=%s",
                    request_id,
                    effective_model,
                    llm_provider or "",
                    "cancelled" if cancelled else "ok",
                    duration_s,
                    f"{max(0.0, (first_any_at - started) * 1000.0):.0f}" if first_any_at else "none",
                    f"{ttfb_ms:.0f}" if ttfb_ms is not None else "none",
                    reasoning_chars,
                    chunk_count,
                    text_chars,
                    getattr(last_usage, "input_tokens", "-") if last_usage is not None else "-",
                    getattr(last_usage, "output_tokens", "-") if last_usage is not None else "-",
                    getattr(last_usage, "reasoning_tokens", "-") if last_usage is not None else "-",
                    finish_reason or "",
                )

    async def complete(
        self,
        *,
        request: Any,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        prompt_cache_runtime: Any | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        from prismclaw.schema.llm.tool_call_types import ToolCallBuffer

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_buffer = ToolCallBuffer()
        finish_reason: str | None = None
        usage = LLMUsage()

        async for chunk in self.stream(
            request=request,
            model=model,
            tools=tools,
            prompt_cache_runtime=prompt_cache_runtime,
            idle_seconds=idle_seconds,
            abort_event=abort_event,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        ):
            if chunk.reasoning_delta:
                reasoning_parts.append(chunk.reasoning_delta)
            if chunk.text_delta:
                text_parts.append(chunk.text_delta)
            if chunk.tool_call_deltas:
                tool_calls_buffer.absorb(chunk.tool_call_deltas)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage:
                usage = chunk.usage

        tool_calls = tool_calls_buffer.finalize()
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            reasoning="".join(reasoning_parts),
        )
