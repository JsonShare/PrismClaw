from __future__ import annotations

import asyncio
import logging
import re

from typing import Any, TYPE_CHECKING

from prismclaw.engine.compression.compaction import (
    estimate_message_tokens,
)
from prismclaw.schema.content.compaction import CompactResult

from prismclaw.engine.compression._autocompact_breaker import (
    AutocompactBreaker,
    _MAX_PTL_RETRIES,
    _METRIC_AUTOCOMPACT_FAILURE,
    _PROMPT_TOO_LONG_REASON,
    _emit_breaker_metric,
)

if TYPE_CHECKING:
    # 仅类型注解所需，runtime 不依赖 hooks。
    from prismclaw.engine.hooks import CompactionContext

logger = logging.getLogger(__name__)


def group_messages_by_api_round(messages: list[Any]) -> list[list[Any]]:
    """Group messages by API round: one assistant message plus the
    tool/user messages that follow it form a group. Leading messages
    without an assistant anchor join the first group.

    Dropping whole groups preserves the tool_use/tool_result pair
    invariant — a raw character/percentage cut could split a pair and
    make the request permanently unfixable.
    """
    groups: list[list[Any]] = []
    current: list[Any] = []
    for msg in messages:
        is_assistant = isinstance(msg, dict) and msg.get("role") == "assistant"
        if is_assistant and any(
            isinstance(m, dict) and m.get("role") == "assistant" for m in current
        ):
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        groups.append(current)
    return groups


def _parse_ptl_token_gap(error_message: str) -> int | None:
    """Parse the token overflow gap from a PTL error message.

    Supports both Anthropic-style ("X tokens ... exceeds ... maximum Y
    tokens") and OpenAI-style ("maximum context length is X ... requested
    Y") formats. Returns ``None`` when neither pattern matches.
    """
    m = re.search(
        r"(\d+)\s+tokens.*exceeds.*?(\d+)\s*tokens", error_message, re.DOTALL
    )
    if m:
        return max(0, int(m.group(1)) - int(m.group(2)))
    m = re.search(
        r"maximum context length (?:is|of) (\d+).*?requested (\d+)",
        error_message,
        re.DOTALL,
    )
    if m:
        return max(0, int(m.group(2)) - int(m.group(1)))
    return None


def _resolve_min_keep_tokens() -> int:
    """PTL 重试后至少保留的 token 数——唯一来源：compaction.ptl_min_keep_tokens。"""
    from prismclaw.infra.settings import load_settings

    return int(load_settings().agent.context.compaction.ptl_min_keep_tokens)


def truncate_head_for_ptl_retry(
    messages: list[Any],
    token_gap: int | None,
) -> list[Any]:
    """PTL fallback: drop the oldest API round groups.

    When ``token_gap`` is known (parsed from the provider error), drop
    just enough groups to cover the gap; otherwise drop ~20% of groups.
    Always keep at least one group and at least
    ``compaction.ptl_min_keep_tokens`` tokens of context.
    """
    groups = group_messages_by_api_round(messages)
    if len(groups) <= 1:
        return messages

    min_keep = _resolve_min_keep_tokens()

    if token_gap is None or token_gap <= 0:
        drop_count = max(1, len(groups) // 5)
    else:
        drop_count = 0
        covered = 0
        for group in groups:
            if covered >= token_gap or drop_count >= len(groups) - 1:
                break
            covered += sum(estimate_message_tokens(m) for m in group)
            drop_count += 1

    if drop_count == 0:
        return messages

    # Guarantee the remainder stays above compaction.ptl_min_keep_tokens.
    kept_messages = messages[sum(len(g) for g in groups[:drop_count]) :]
    kept_tokens = sum(estimate_message_tokens(m) for m in kept_messages)
    while drop_count > 1 and kept_tokens < min_keep:
        drop_count -= 1
        kept_messages = messages[sum(len(g) for g in groups[:drop_count]) :]
        kept_tokens = sum(estimate_message_tokens(m) for m in kept_messages)

    # Some providers reject a conversation that starts with an assistant
    # message; re-anchor with a synthetic user marker.
    if kept_messages and isinstance(kept_messages[0], dict) and kept_messages[0].get("role") == "assistant":
        kept_messages = [
            {"role": "user", "content": "[早前对话已截断，以便进行压缩重试]"},
            *kept_messages,
        ]
    return kept_messages


async def _compact_with_ptl_retry(
    *,
    deps: Any,
    request: Any,
    base_messages: list[Any],
    history_budget: int,
    force: bool,
    abort_event: asyncio.Event,
    model: str | None,
    threshold_override: float | None,
    keep_recent_tokens_override: int | None,
    breaker: AutocompactBreaker,
    checkpoint: Any,
    compaction_ctx: CompactionContext,
) -> CompactResult:
    """Call the context compactor with Claude-style PTL retry loop.

    If the compact request itself returns ``prompt_too_long``,
    ``truncate_head_for_ptl_retry`` drops the oldest API round groups
    (one assistant turn plus its trailing tool/user messages), sized by
    the parsed token overflow gap. Grouping preserves the
    tool_use/tool_result pairing invariant. Retries up to 3 times;
    any non-PTL failure aborts the loop immediately.
    """
    api_messages = list(base_messages)
    last_result: CompactResult | None = None
    for attempt in range(_MAX_PTL_RETRIES + 1):
        try:
            result = await deps.context_compactor.compact(
                session_id=request.session_id,
                messages=api_messages,
                token_budget=history_budget,
                force=force,
                abort_event=abort_event,
                model=model,
                threshold_override=threshold_override,
                keep_recent_tokens_override=keep_recent_tokens_override,
            )
        except Exception as compact_exc:
            # Emit the failure metric here but let the caller update the
            # breaker once via its single failure path and restore the
            # checkpoint (line 746 does checkpoint.restore_into(tree)).
            _emit_breaker_metric(
                _METRIC_AUTOCOMPACT_FAILURE,
                error_type=type(compact_exc).__name__,
                breaker=breaker,
            )
            return CompactResult(
                ok=False,
                compacted=False,
                reason_code="compaction_failed",
                reason=f"compaction raised {type(compact_exc).__name__}: {compact_exc}",
            )
        last_result = result
        if result.ok or result.reason_code != _PROMPT_TOO_LONG_REASON:
            return result
        # PTL hit: shrink the prompt and retry (Claude compact.ts:464).
        logger.warning(
            "prompt_too_long on compact attempt=%d/%d; truncating head",
            attempt,
            _MAX_PTL_RETRIES,
        )
        _emit_breaker_metric(
            _METRIC_AUTOCOMPACT_FAILURE,
            error_type="ptl_retry",
            breaker=breaker,
        )
        token_gap = _parse_ptl_token_gap(str(getattr(result, "reason", "") or ""))
        api_messages = truncate_head_for_ptl_retry(api_messages, token_gap)
    # All retries exhausted.  Return the last PTL result so the caller
    # treats it as a normal compaction failure and updates the breaker.
    assert last_result is not None
    return last_result
