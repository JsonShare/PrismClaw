from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Protocol, TYPE_CHECKING

from prismclaw.schema.llm.cache_types import EPHEMERAL_CACHE_CONTROL
from prismclaw.engine.foundation.telemetry.observability import CacheBreakDetector
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.data_types import CacheStrategySnapshot
from prismclaw.schema.content.context import ContextBlock, ContextPackLayout

if TYPE_CHECKING:
    from prismclaw.engine.services.llm.stream_fn import LLMUsage
    # 仅类型注解所需，runtime 不依赖 execution。
    from prismclaw.engine.execution.checkpoint import PromptCacheRuntimeState

_ANTHROPIC_PREFIXES = (
    "anthropic/",
    "claude-",
    "bedrock/anthropic.",
    "vertex_ai/claude-",
)
MIN_CACHE_TOKENS = 1024

# Cache-reuse observability — pure metrics, no behavior change.
_METRIC_DESCRIBED = "cache.strategy.described"
_METRIC_REUSED = "cache.strategy.reused_prior"
_METRIC_REUSED_BY_MODEL = "cache.strategy.reused_prior_by_model"
_METRIC_BREACH = "cache.strategy.breach"
_METRIC_BREACH_BY_REASON = "cache.strategy.breach_by_reason"
_METRIC_RS_REUSED = "cache.replacement_state.reused"


@dataclass(frozen=True)
class CacheSegment:
    text: str
    cacheable: bool
    stability: str
    estimated_tokens: int
    sources: tuple[str, ...]


class CachePackingAdapter(Protocol):
    def pack(
        self,
        *,
        static_segments: list[CacheSegment],
        trailing_parts: list[str],
    ) -> str | list[dict[str, Any]]: ...


class FlatStringCacheAdapter:
    def pack(
        self,
        *,
        static_segments: list[CacheSegment],
        trailing_parts: list[str],
    ) -> str:
        parts = [segment.text for segment in static_segments if segment.text]
        parts.extend(part for part in trailing_parts if part)
        return "\n\n".join(parts)


class AnthropicCacheAdapter:
    def __init__(self, *, min_cache_tokens: int = MIN_CACHE_TOKENS) -> None:
        self._min_cache_tokens = min_cache_tokens
        self._fallback = FlatStringCacheAdapter()

    def pack(
        self,
        *,
        static_segments: list[CacheSegment],
        trailing_parts: list[str],
    ) -> str | list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cached_block_count = 0

        for segment in static_segments:
            if not segment.text:
                continue
            block: dict[str, Any] = {"type": "text", "text": segment.text}
            if segment.cacheable and segment.estimated_tokens >= self._min_cache_tokens:
                block["cache_control"] = EPHEMERAL_CACHE_CONTROL
                cached_block_count += 1
            blocks.append(block)

        trailing_text = "\n\n".join(part for part in trailing_parts if part)
        if trailing_text:
            blocks.append({"type": "text", "text": trailing_text})

        if cached_block_count == 0:
            return self._fallback.pack(
                static_segments=static_segments,
                trailing_parts=trailing_parts,
            )
        return blocks


def _breach_reason(
    *,
    request_mode: str | None,
    prior_adapter: str | None,
    adapter_name: str,
    prior_strategy_kind: str | None,
    strategy_kind: str,
    prior_static_digest: str | None,
    static_digest: str,
    prior_model_supports_cache_blocks: bool | None,
    current_model_supports_cache_blocks: bool,
    replacement_state_required: bool,
    replacement_state_reused: bool,
) -> str:
    """Return the first reason that prevented ``reused_prior_strategy``.

    Order matches the conjunction in :meth:`CacheBlockPacker.describe_strategy`
    so the most upstream cause is reported (request_mode → adapter → kind →
    digest → model → replacement_state).  ``"unknown"`` is the fallback when
    no specific check failed (should not happen in practice).
    """
    if request_mode != "follow_up":
        return "request_mode"
    if prior_adapter != adapter_name:
        return "adapter"
    if prior_strategy_kind != strategy_kind:
        return "strategy_kind"
    if prior_static_digest != static_digest:
        return "static_digest"
    if prior_model_supports_cache_blocks != current_model_supports_cache_blocks:
        return "model_supports_cache_blocks"
    if replacement_state_required and not replacement_state_reused:
        return "replacement_state"
    return "unknown"


class CacheBlockPacker:
    def __init__(self, *, min_cache_tokens: int = MIN_CACHE_TOKENS) -> None:
        self._min_cache_tokens = min_cache_tokens

    def pack(
        self,
        *,
        plan: ContextPackLayout,
        model: str,
        request_mode: str | None = None,
        previous_cache_strategy: CacheStrategySnapshot | None = None,
        replacement_state: ReplacementDecisionState | None = None,
        previous_replacement_state: ReplacementDecisionState | None = None,
    ) -> str | list[dict[str, Any]]:
        static_segments, trailing_parts, adapter_name = self._prepare(
            plan=plan,
            model=model,
            request_mode=request_mode,
            previous_cache_strategy=previous_cache_strategy,
            replacement_state=replacement_state,
            previous_replacement_state=previous_replacement_state,
        )
        adapter = self._adapter_for_name(adapter_name)
        return adapter.pack(
            static_segments=static_segments,
            trailing_parts=trailing_parts,
        )

    def describe_strategy(
        self,
        *,
        plan: ContextPackLayout,
        model: str,
        request_mode: str | None = None,
        previous_cache_strategy: CacheStrategySnapshot | None = None,
        replacement_state: ReplacementDecisionState | None = None,
        previous_replacement_state: ReplacementDecisionState | None = None,
    ) -> CacheStrategySnapshot:
        static_segments, trailing_parts, adapter_name = self._prepare(
            plan=plan,
            model=model,
            request_mode=request_mode,
            previous_cache_strategy=previous_cache_strategy,
            replacement_state=replacement_state,
            previous_replacement_state=previous_replacement_state,
        )
        current_model_supports_cache_blocks = is_anthropic_model(model)
        cacheable_segments = self._cacheable_segments(
            static_segments=static_segments,
            adapter_name=adapter_name,
        )
        strategy_kind = (
            "anthropic_cache_blocks"
            if adapter_name == "anthropic" and cacheable_segments > 0
            else "flat_string"
        )
        section_hashes = {
            segment.sources[0]: hashlib.sha1(segment.text.encode("utf-8")).hexdigest()[:16]
            for segment in static_segments if len(segment.sources) > 0
        }
        digest_input = [
            f"{segment.stability}:{int(segment.cacheable)}:{segment.text}"
            for segment in static_segments
        ]
        static_digest = hashlib.sha1("\n--SEG--\n".join(digest_input).encode("utf-8")).hexdigest()[:16]
        prior_adapter = _string_field(previous_cache_strategy, "provider_adapter")
        prior_strategy_kind = _string_field(previous_cache_strategy, "strategy_kind")
        prior_static_digest = _string_field(previous_cache_strategy, "static_digest")
        replacement_state_digest = (
            replacement_state.state_digest() if replacement_state is not None else None
        )
        prior_replacement_state_digest = (
            previous_replacement_state.state_digest()
            if previous_replacement_state is not None
            else _string_field(previous_cache_strategy, "replacement_state_digest")
        )
        prior_model_supports_cache_blocks = _bool_field(
            previous_cache_strategy,
            "model_supports_cache_blocks",
        )
        replacement_state_reused = (
            replacement_state_digest is not None
            and replacement_state_digest == prior_replacement_state_digest
        )
        replacement_state_required = (
            replacement_state_digest is not None
            or prior_replacement_state_digest is not None
            or (
                replacement_state is not None
                and replacement_state.decision_count() > 0
            )
        )
        reused_prior_strategy = (
            request_mode == "follow_up"
            and prior_adapter == adapter_name
            and prior_strategy_kind == strategy_kind
            and prior_static_digest == static_digest
            and prior_model_supports_cache_blocks == current_model_supports_cache_blocks
            and (not replacement_state_required or replacement_state_reused)
        )

        # Cache-reuse observability.  Pure metrics, never alters the
        # returned snapshot or the upstream cache decision.  Designed
        # to be safe to emit on the hot describe_strategy path
        # (target < 1ms).
        self._emit_cache_metrics(
            model=model,
            request_mode=request_mode,
            reused_prior_strategy=reused_prior_strategy,
            prior_strategy_found=previous_cache_strategy is not None,
            prior_adapter=prior_adapter,
            adapter_name=adapter_name,
            prior_strategy_kind=prior_strategy_kind,
            strategy_kind=strategy_kind,
            prior_static_digest=prior_static_digest,
            static_digest=static_digest,
            prior_model_supports_cache_blocks=prior_model_supports_cache_blocks,
            current_model_supports_cache_blocks=current_model_supports_cache_blocks,
            replacement_state_required=replacement_state_required,
            replacement_state_reused=replacement_state_reused,
        )

        return {
            "request_mode": request_mode,
            "strategy_kind": strategy_kind,
            "provider_adapter": adapter_name,
            "static_digest": static_digest,
            "static_segment_count": len(static_segments),
            "cacheable_segment_count": cacheable_segments,
            "trailing_part_count": len([part for part in trailing_parts if part]),
            "model_supports_cache_blocks": current_model_supports_cache_blocks,
            "prior_strategy_found": previous_cache_strategy is not None,
            "prior_static_digest": prior_static_digest,
            "reused_prior_strategy": reused_prior_strategy,
            "replacement_state_digest": replacement_state_digest,
            "replacement_decision_count": (
                replacement_state.decision_count() if replacement_state is not None else 0
            ),
            "prior_replacement_state_digest": prior_replacement_state_digest,
            "reused_replacement_state": (
                request_mode == "follow_up"
                and replacement_state_reused
            ),
            "section_hashes": section_hashes,
        }

    def _emit_cache_metrics(
        self,
        model: str,
        request_mode: str | None,
        reused_prior_strategy: bool,
        prior_strategy_found: bool,
        prior_adapter: str | None,
        adapter_name: str,
        prior_strategy_kind: str | None,
        strategy_kind: str,
        prior_static_digest: str | None,
        static_digest: str,
        prior_model_supports_cache_blocks: bool | None,
        current_model_supports_cache_blocks: bool,
        replacement_state_required: bool,
        replacement_state_reused: bool,
    ) -> None:
        """Emit cache-strategy observability counters.

        All emissions are fire-and-forget; metric failures must
        never propagate to the caller.
        """
        try:
            emit(_METRIC_DESCRIBED, 1.0, kind="counter")
            if reused_prior_strategy:
                emit(_METRIC_REUSED, 1.0, kind="counter")
                emit(
                    _METRIC_REUSED_BY_MODEL,
                    1.0,
                    kind="counter",
                    labels={"model": model, "request_mode": request_mode or "unknown"},
                )
                if request_mode == "follow_up" and replacement_state_required:
                    emit(
                        _METRIC_RS_REUSED,
                        1.0,
                        kind="counter",
                        labels={"model": model},
                    )
            elif prior_strategy_found:
                emit(_METRIC_BREACH, 1.0, kind="counter")
                reason = _breach_reason(
                    request_mode=request_mode,
                    prior_adapter=prior_adapter,
                    adapter_name=adapter_name,
                    prior_strategy_kind=prior_strategy_kind,
                    strategy_kind=strategy_kind,
                    prior_static_digest=prior_static_digest,
                    static_digest=static_digest,
                    prior_model_supports_cache_blocks=prior_model_supports_cache_blocks,
                    current_model_supports_cache_blocks=current_model_supports_cache_blocks,
                    replacement_state_required=replacement_state_required,
                    replacement_state_reused=replacement_state_reused,
                )
                emit(
                    _METRIC_BREACH_BY_REASON,
                    1.0,
                    kind="counter",
                    labels={"reason": reason, "model": model},
                )
        except Exception:  # pragma: no cover - observability must never crash callers
            # Observability is best-effort; failures are silenced to keep the
            # hot path deterministic.  Tests can opt in via ``strict_metrics``.
            return

    def _prepare(
        self,
        *,
        plan: ContextPackLayout,
        model: str,
        request_mode: str | None = None,
        previous_cache_strategy: CacheStrategySnapshot | None = None,
        replacement_state: ReplacementDecisionState | None = None,
        previous_replacement_state: ReplacementDecisionState | None = None,
    ) -> tuple[list[CacheSegment], list[str], str]:
        del replacement_state, previous_replacement_state
        static_segments = self._build_static_segments(plan.static_blocks)
        trailing_parts = [
            block.content
            for block in [*plan.dynamic_blocks, *plan.history_blocks, *plan.result_blocks]
            if block.content
        ]
        current_static_digest = self._static_digest(static_segments)
        adapter_name = self._resolve_adapter_name(
            model=model,
            request_mode=request_mode,
            previous_cache_strategy=previous_cache_strategy,
            current_static_digest=current_static_digest,
        )
        return static_segments, trailing_parts, adapter_name

    @staticmethod
    def _static_digest(static_segments: list[CacheSegment]) -> str:
        digest_input = [
            f"{segment.stability}:{int(segment.cacheable)}:{segment.text}"
            for segment in static_segments
        ]
        return hashlib.sha1("\n--SEG--\n".join(digest_input).encode("utf-8")).hexdigest()[:16]

    def _resolve_adapter_name(
        self,
        *,
        model: str,
        request_mode: str | None,
        previous_cache_strategy: CacheStrategySnapshot | None,
        current_static_digest: str,
    ) -> str:
        current_model_supports_cache_blocks = is_anthropic_model(model)
        default_adapter = "anthropic" if current_model_supports_cache_blocks else "flat"
        if request_mode != "follow_up" or previous_cache_strategy is None:
            return default_adapter
        if previous_cache_strategy.get("static_digest") != current_static_digest:
            return default_adapter
        if previous_cache_strategy.get("model_supports_cache_blocks") != (
            current_model_supports_cache_blocks
        ):
            return default_adapter
        prior_adapter = previous_cache_strategy.get("provider_adapter")
        if prior_adapter == "flat":
            return "flat"
        if prior_adapter == "anthropic" and current_model_supports_cache_blocks:
            return "anthropic"
        return default_adapter

    def _adapter_for_name(self, adapter_name: str) -> CachePackingAdapter:
        if adapter_name == "anthropic":
            return AnthropicCacheAdapter(min_cache_tokens=self._min_cache_tokens)
        return FlatStringCacheAdapter()

    def _cacheable_segments(
        self,
        *,
        static_segments: list[CacheSegment],
        adapter_name: str,
    ) -> int:
        if adapter_name != "anthropic":
            return 0
        return sum(
            1
            for segment in static_segments
            if segment.cacheable and segment.estimated_tokens >= self._min_cache_tokens
        )

    def _build_static_segments(self, blocks: list[ContextBlock]) -> list[CacheSegment]:
        segments: list[CacheSegment] = []
        current: CacheSegment | None = None

        for block in blocks:
            if not block.content:
                continue
            effective_stability = (
                "stable"
                if block.zone == "static" and block.stability == "volatile"
                else block.stability
            )
            cache_candidate = (
                effective_stability in {"stable", "semi_stable"}
                and (block.cacheable or block.zone == "static")
            )
            segment = CacheSegment(
                text=block.content,
                cacheable=cache_candidate,
                stability=effective_stability,
                estimated_tokens=block.estimated_tokens,
                sources=(block.source,),
            )
            if current is None:
                current = segment
                continue
            if self._can_merge_segments(current, segment):
                current = CacheSegment(
                    text="\n\n".join([current.text, segment.text]),
                    cacheable=current.cacheable,
                    stability=current.stability,
                    estimated_tokens=current.estimated_tokens + segment.estimated_tokens,
                    sources=(*current.sources, *segment.sources),
                )
                continue
            segments.append(current)
            current = segment

        if current is not None:
            segments.append(current)
        return segments

    @staticmethod
    def _can_merge_segments(left: CacheSegment, right: CacheSegment) -> bool:
        return (
            left.cacheable == right.cacheable
            and left.stability == right.stability
        )


def is_anthropic_model(model: str) -> bool:
    lowered = model.lower()
    return any(lowered.startswith(prefix) for prefix in _ANTHROPIC_PREFIXES)


def _string_field(
    strategy: CacheStrategySnapshot | None,
    key: str,
) -> str | None:
    if strategy is None:
        return None
    value = strategy.get(key)
    return value if isinstance(value, str) else None


def _bool_field(
    strategy: CacheStrategySnapshot | None,
    key: str,
) -> bool | None:
    if strategy is None:
        return None
    value = strategy.get(key)
    return value if isinstance(value, bool) else None


class PromptCacheRuntimeContract(Protocol):
    def notify_cache_deletions_pending(self, runtime_state: PromptCacheRuntimeState) -> None: ...

    def notify_post_compaction(self, runtime_state: PromptCacheRuntimeState) -> None: ...

    def record_request_usage(
        self,
        *,
        runtime_state: PromptCacheRuntimeState,
        usage: LLMUsage,
        session_id: str,
    ) -> bool: ...


@dataclass(slots=True)
class DefaultPromptCacheRuntimeContract:
    detector: CacheBreakDetector = field(default_factory=CacheBreakDetector)

    def notify_cache_deletions_pending(self, runtime_state: PromptCacheRuntimeState) -> None:
        runtime_state.cache_deletions_pending = True

    def notify_post_compaction(self, runtime_state: PromptCacheRuntimeState) -> None:
        runtime_state.pending_post_compaction = True

    def record_request_usage(
        self,
        *,
        runtime_state: PromptCacheRuntimeState,
        usage: LLMUsage,
        session_id: str,
    ) -> bool:
        detected = self.detector.record_request(
            runtime_state=runtime_state,
            cache_read_tokens=usage.cache_read_input_tokens,
            input_tokens=usage.input_tokens,
            session_id=session_id,
        )
        runtime_state.cache_deletions_pending = False
        runtime_state.pending_post_compaction = False
        return detected
