"""Summary engine — fact sheet, reason codes, and summarization."""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from prismclaw.engine.compression.hardening import (
    HARDENED_SUMMARIZER_SYSTEM_PROMPT,
    split_into_chunks,
    summarize_in_stages,
)
from prismclaw.engine.compression.compaction import (
    build_summarizer_payload,
)
from prismclaw.engine.compression.history_compaction import (
    _resolve_no_tools_preamble_enabled,
    inject_no_tools_preamble,
    resolve_compact_prompt,
    resolve_strip_analysis,
    strip_analysis_block,
)
from prismclaw.schema.content.compaction import CompactionReasonCode


class SummarizerCallable(Protocol):
    async def __call__(
        self,
        payload: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> str: ...


class CompactionTimeout(Exception):
    pass


class CompactionAborted(Exception):
    pass


class SummaryEngine:
    def __init__(
        self,
        *,
        summarize: SummarizerCallable | None = None,
        compaction_timeout_s: float,
        chunk_token_budget: int,
        compaction_config: Any | None = None,
    ) -> None:
        self._summarize = summarize
        self._compaction_timeout_s = compaction_timeout_s
        self._chunk_token_budget = chunk_token_budget
        self._compaction_config = compaction_config

    async def summarize_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        abort_event: asyncio.Event | None,
        model: str | None,
    ) -> str:
        if self._summarize is None:
            return fallback_summary(messages)
        return await self._run_summarizer_guarded(
            messages,
            abort_event=abort_event,
            model=model,
        )

    async def _run_summarizer_guarded(
        self,
        messages: list[dict[str, Any]],
        *,
        abort_event: asyncio.Event | None,
        model: str | None,
    ) -> str:
        from prismclaw.engine.foundation.runtime_util import (
            AgentAbortedError,
            AgentTimeoutError,
            with_safety_timeout,
        )

        summarize_fn = self._summarize

        async def _one_stage(payload: list[dict[str, Any]]) -> str:
            assert summarize_fn is not None
            effective_payload = build_summarizer_payload(payload)
            compact_prompt = resolve_compact_prompt(self._compaction_config)
            effective_payload[0] = {
                "role": "system",
                "content": compact_prompt or HARDENED_SUMMARIZER_SYSTEM_PROMPT,
            }
            enabled = _resolve_no_tools_preamble_enabled(self._compaction_config)
            if enabled:
                effective_payload = inject_no_tools_preamble(
                    effective_payload,
                    enabled=True,
                )
            try:
                from prismclaw.engine.compression.history_compaction import (
                    _METRIC_COMPACT_OUTPUT_TOKENS,
                    _METRIC_COMPACT_PROMPT_TOKENS,
                )
                from prismclaw.engine.foundation.metrics import emit

                try:
                    from prismclaw.engine.foundation.telemetry.token_metrics import estimate_message_tokens

                    prompt_tokens_est = max(
                        1,
                        estimate_message_tokens(list(effective_payload)),
                    )
                    emit(
                        _METRIC_COMPACT_PROMPT_TOKENS,
                        float(prompt_tokens_est),
                        kind="histogram",
                    )
                except Exception:
                    pass
                result = await summarize_fn(effective_payload, model=model)
                try:
                    from prismclaw.schema.shared import estimate_tokens

                    output_tokens_est = estimate_tokens(str(result))
                    emit(
                        _METRIC_COMPACT_OUTPUT_TOKENS,
                        float(output_tokens_est),
                        kind="histogram",
                    )
                except Exception:
                    pass
                return result
            except TypeError:
                return await summarize_fn(effective_payload)  # type: ignore[call-arg]

        chunks = split_into_chunks(messages, chunk_token_budget=self._chunk_token_budget)

        async def _run() -> str:
            if len(chunks) <= 1:
                return await _one_stage(messages)
            return await summarize_in_stages(
                messages,
                summarizer=_one_stage,
                chunk_token_budget=self._chunk_token_budget,
            )

        try:
            summary = await with_safety_timeout(
                _run,
                timeout_s=self._compaction_timeout_s,
                abort_event=abort_event,
                kind="compaction",
            )
        except AgentTimeoutError as te:
            raise CompactionTimeout() from te
        except AgentAbortedError as ae:
            raise CompactionAborted() from ae
        if resolve_strip_analysis(self._compaction_config):
            return strip_analysis_block(summary)
        return summary


def fallback_summary(messages: list[dict[str, Any]]) -> str:
    from prismclaw.schema.shared import extract_text_from_content

    lines = [f"[summary of {len(messages)} prior messages]"]

    user_msgs: list[dict[str, Any]] = []
    assistant_msgs: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for m in messages:
        role = m.get("role", "")
        if role == "user":
            user_msgs.append(m)
        elif role == "assistant":
            assistant_msgs.append(m)
        elif role == "tool":
            tool_results.append(m)

    if user_msgs:
        lines.append(f"\n### User messages ({len(user_msgs)} total):")
        for i, m in enumerate(user_msgs[-5:], max(1, len(user_msgs) - 4)):
            text = extract_text_from_content(m.get("content"))
            if text:
                snippet = text[:300].replace("\n", " ")
                if len(text) > 300:
                    snippet += f"... ({len(text)} chars total)"
                lines.append(f"  {i}. {snippet}")
        if len(user_msgs) > 5:
            lines.append(f"  ... ({len(user_msgs) - 5} earlier user messages)")

    if assistant_msgs:
        lines.append(f"\n### Assistant responses ({len(assistant_msgs)} total):")
        for i, m in enumerate(assistant_msgs[-3:], max(1, len(assistant_msgs) - 2)):
            text = extract_text_from_content(m.get("content"))
            tool_calls = m.get("tool_calls", [])
            parts = []
            if text:
                snippet = text[:200].replace("\n", " ")
                if len(text) > 200:
                    snippet += f"... ({len(text)} chars)"
                parts.append(snippet)
            if tool_calls:
                call_names = []
                for tc in tool_calls:
                    fn = ((tc or {}).get("function") or {}).get("name", "?")
                    call_names.append(fn)
                parts.append(f"[calls: {', '.join(call_names)}]")
            if parts:
                lines.append(f"  {i}. {' | '.join(parts)}")
        if len(assistant_msgs) > 3:
            lines.append(f"  ……（更早的 {len(assistant_msgs) - 3} 条助手回复已省略）")

    if tool_results:
        lines.append(f"\n### 工具结果（共 {len(tool_results)} 条）")
        lines.append("  [工具结果已被压缩——如需细节，请用特定工具重新查询]")

    lines.append("\n### 完整压缩产物中保留的关键标识符：")
    lines.append("  （路径、哈希与文件引用已单独保留）")

    return "\n".join(lines)


ALL_REASON_CODES: tuple[CompactionReasonCode, ...] = (
    "compacted",
    "no_compactable_entries",
    "below_threshold",
    "already_compacted_recently",
    "live_context_still_exceeds_target",
    "summary_failed",
    "timeout",
    "aborted",
    "provider_error_4xx",
    "provider_error_5xx",
    "unknown",
)


def classify_compaction_reason(text: str | None) -> CompactionReasonCode:
    if not text:
        return "unknown"

    lowered = text.lower()

    if "already compacted" in lowered or "recent compaction" in lowered:
        return "already_compacted_recently"

    if "below threshold" in lowered or "within-budget" in lowered or "within budget" in lowered:
        return "below_threshold"

    if "no compactable" in lowered or "no-safe-cut" in lowered or "nothing to compact" in lowered:
        return "no_compactable_entries"

    if "summary" in lowered and ("fail" in lowered or "error" in lowered):
        return "summary_failed"

    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"

    if "abort" in lowered or "cancel" in lowered:
        return "aborted"

    if ("still exceeds" in lowered or "over budget" in lowered) and "context" in lowered:
        return "live_context_still_exceeds_target"

    if "4" in lowered and ("provider" in lowered or "api" in lowered) and "error" in lowered:
        return "provider_error_4xx"

    if "5" in lowered and ("provider" in lowered or "api" in lowered) and "error" in lowered:
        return "provider_error_5xx"

    if lowered.startswith("compacted-at-") or lowered == "compacted":
        return "compacted"

    return "unknown"
