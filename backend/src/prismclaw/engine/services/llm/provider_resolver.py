"""Provider routing, credential resolution, and error classification for the LLM layer."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.schema.llm.errors import LLMError, LLMErrorCode

if TYPE_CHECKING:
    from prismclaw.schema.llm.provider_types import ProviderConfig

logger = logging.getLogger(__name__)


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).replace("`", "").strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()

    if not s:
        return None
    if any(ch.isspace() for ch in s):
        raise LLMError(LLMErrorCode.UNKNOWN, f"base_url 无效：包含空白字符：{s!r}")

    from urllib.parse import urlparse

    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise LLMError(LLMErrorCode.UNKNOWN, f"base_url 无效：{s!r}")
    return s


def _should_retry_transient_provider_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    # 超时不重试：httpx.TransportError / APIConnectionError 都覆盖了超时子类。
    try:
        import httpx
        import openai

        if isinstance(exc, (httpx.TimeoutException, openai.APITimeoutError)):
            return False
    except Exception:
        pass
    # Transient network errors — the server may be temporarily overloaded
    # or the connection dropped mid-request. A quick retry often succeeds.
    if isinstance(exc, _connection_error_types()):
        return True
    if "expecting ',' delimiter" in msg:
        return True
    if "expecting property name enclosed in double quotes" in msg:
        return True
    if "jsondecodeerror" in msg and ("openaiexception" in msg or "openai" in msg):
        return True
    if "openaiexception" in msg and "expecting" in msg:
        return True
    return False


# 连接/传输层异常族：流式响应被截断（代理/上游切断 SSE 流）或连接建立失败时，
# aiohttp / httpx / litellm 抛出的底层异常。按异常类型（而非消息子串）判定，
# 因为 camelCase 的 "APIConnectionError" 与传输层包装器无法被子串启发式命中。
# classify_error 与 client 共用同一判定。
_CONNECTION_ERROR_TYPES: tuple[type, ...] = ()


def _connection_error_types() -> tuple[type, ...]:
    """Lazily resolve the connection/transport exception family (cached)."""
    global _CONNECTION_ERROR_TYPES
    if _CONNECTION_ERROR_TYPES:
        return _CONNECTION_ERROR_TYPES
    # builtin ConnectionError 覆盖 ConnectionRefusedError/ResetError。
    types: list[type] = [ConnectionError]
    try:
        from litellm.exceptions import (
            APIConnectionError as _LiteLLMAPIConnectionError,
            MidStreamFallbackError as _LiteLLMMidStream,
        )
        types.extend([_LiteLLMAPIConnectionError, _LiteLLMMidStream])
    except Exception:
        pass
    try:
        import httpx
        types.extend([httpx.ReadError, httpx.TransportError])
    except Exception:
        pass
    try:
        import aiohttp
        types.extend([aiohttp.ClientPayloadError, aiohttp.ClientConnectionError])
    except Exception:
        pass
    _CONNECTION_ERROR_TYPES = tuple(types)
    return _CONNECTION_ERROR_TYPES


def classify_error(exc: Exception) -> str:
    # Wrap-once invariant: an LLMError already carries an explicit code that was
    # set by whoever raised it (e.g. resolve_provider_for_model raising
    # PROVIDER_NOT_FOUND).  Never re-classify it from message text — the
    # keyword heuristic below is for raw upstream exceptions only.
    if isinstance(exc, LLMError):
        return exc.code
    # 连接/传输层异常族：类型优先于消息子串启发式（避免漏判 camelCase 的
    # APIConnectionError 与传输层包装器）。
    if isinstance(exc, _connection_error_types()):
        return LLMErrorCode.CONNECTION_ERROR
    message = str(exc).lower()
    if "context" in message and (
        "overflow" in message or "length" in message or "maximum" in message
    ):
        return LLMErrorCode.CONTEXT_OVERFLOW
    if "rate limit" in message or "too many requests" in message or "429" in message:
        return LLMErrorCode.RATE_LIMIT
    if "auth" in message or "api key" in message or "unauthorized" in message or "401" in message:
        return LLMErrorCode.AUTH_ERROR
    if "connection error" in message or "server disconnected" in message or "connection refused" in message or "connecterror" in message:
        return LLMErrorCode.CONNECTION_ERROR
    if "timeout" in message or "timed out" in message:
        return LLMErrorCode.TIMEOUT
    return LLMErrorCode.UNKNOWN


def resolve_provider_for_model(
    model: str,
    providers: Mapping[str, ProviderConfig],
    *,
    default_provider: str | None = None,
    unknown_prefix_policy: Literal["fail", "default"] = "fail",
) -> tuple[str, ProviderConfig]:
    for name, ps in providers.items():
        if model in (ps.models or {}):
            return name, ps

    for name, ps in providers.items():
        prefixes = ps.prefixes or [name]
        for prefix in prefixes:
            if model == prefix or model.startswith(prefix + "/"):
                return name, ps

    first_segment = model.split("/", 1)[0]
    if first_segment in providers:
        return first_segment, providers[first_segment]

    if len(providers) == 1:
        only_name, only_ps = next(iter(providers.items()))
        return only_name, only_ps

    if (
        unknown_prefix_policy == "default"
        and default_provider is not None
        and default_provider in providers
    ):
        return default_provider, providers[default_provider]

    raise LLMError(
        LLMErrorCode.PROVIDER_NOT_FOUND,
        f"没有匹配模型 '{model}' 的 provider。"
        f"已配置的 provider：{list(providers)}。"
        f"请在某个 provider 上声明 prefixes[]，或设置 agent.unknown_prefix_policy='default' "
        f"与 agent.default_provider='<name>'，以便为未知前缀选择默认路由。",
    )


def model_supports_input(
    model: str,
    providers: Mapping[str, ProviderConfig],
    modality: str,
    *,
    default_provider: str | None = None,
    unknown_prefix_policy: Literal["fail", "default"] = "fail",
) -> bool:
    _name, ps = resolve_provider_for_model(
        model,
        providers,
        default_provider=default_provider,
        unknown_prefix_policy=unknown_prefix_policy,
    )
    entry = (ps.models or {}).get(model)
    if entry is None:
        return False
    return modality in entry.modalities.input


def messages_have_user_image_content(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            btype = block.get("type")
            if btype == "image_url" or btype == "image":
                return True
    return False


def format_vision_capable_models(
    providers: Mapping[str, ProviderConfig],
) -> str:
    parts: list[str] = []
    for provider_name, ps in providers.items():
        models_dict = ps.models or {}
        vision_ids = [
            mid for mid, entry in models_dict.items() if "image" in entry.modalities.input
        ]
        if vision_ids:
            parts.append(f"\U0001f4e6 {provider_name}: {', '.join(vision_ids)}")
    return "; ".join(parts) if parts else "(none configured)"


def resolve_credentials(
    model: str,
    providers: dict[str, ProviderConfig],
    fallback_key: str | None,
    fallback_base: str | None,
    default_provider: str | None,
    unknown_prefix_policy: Literal["fail", "default"],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve API credentials for a given model.

    Standalone version of the original LLMClient._resolve_credentials so that
    LiteLLMClient can call it without a class instance.
    """
    if not providers:
        return fallback_key, fallback_base, None, None
    name, ps = resolve_provider_for_model(
        model,
        providers,
        default_provider=default_provider,
        unknown_prefix_policy=unknown_prefix_policy,
    )
    llm_provider = ps.llm_provider if ps.llm_provider is not None else name
    raw_base = ps.base_url
    api_base = _normalize_base_url(raw_base) if raw_base else None
    if raw_base and api_base and raw_base != api_base:
        logger.warning("sanitized base_url for provider=%r: %r -> %r", name, raw_base, api_base)
    return ps.api_key, api_base, name, llm_provider
