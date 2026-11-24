from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from prismclaw.engine.foundation.telemetry.observability import CacheBreakDetector
from prismclaw.schema.agent.data_types import CacheStrategySnapshot
from prismclaw.schema.content.context import ContextBlock, ContextPackLayout
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.schema.llm.cache_types import EPHEMERAL_CACHE_CONTROL

if TYPE_CHECKING:
    # 仅类型注解所需，runtime 不依赖 execution。
    from prismclaw.engine.execution.checkpoint import PromptCacheRuntimeState
    from prismclaw.schema.llm.types import LLMUsage

_ANTHROPIC_PREFIXES = (
    "anthropic/",
    "claude-",
    "bedrock/anthropic.",
    "vertex_ai/claude-",
)
MIN_CACHE_TOKENS = 1024


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


class CacheBlockPacker:
    def __init__(self, *, min_cache_tokens: int = MIN_CACHE_TOKENS) -> None:
        self._min_cache_tokens = min_cache_tokens

    def pack(
        self,
        *,
        plan: ContextPackLayout,
        model: str,
    ) -> str | list[dict[str, Any]]:
        static_segments, trailing_parts, adapter_name = self._prepare(
            plan=plan,
            model=model,
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
        replacement_state: ReplacementDecisionState | None = None,
    ) -> CacheStrategySnapshot:
        static_segments, trailing_parts, adapter_name = self._prepare(
            plan=plan,
            model=model,
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
        digest_input = [
            f"{segment.stability}:{int(segment.cacheable)}:{segment.text}"
            for segment in static_segments
        ]
        static_digest = hashlib.sha1("\n--SEG--\n".join(digest_input).encode("utf-8")).hexdigest()[
            :16
        ]
        replacement_state_digest = (
            replacement_state.state_digest() if replacement_state is not None else None
        )

        return {
            "strategy_kind": strategy_kind,
            "provider_adapter": adapter_name,
            "static_digest": static_digest,
            "static_segment_count": len(static_segments),
            "cacheable_segment_count": cacheable_segments,
            "trailing_part_count": len([part for part in trailing_parts if part]),
            "model_supports_cache_blocks": current_model_supports_cache_blocks,
            "replacement_state_digest": replacement_state_digest,
            "replacement_decision_count": (
                replacement_state.decision_count() if replacement_state is not None else 0
            ),
        }

    def _prepare(
        self,
        *,
        plan: ContextPackLayout,
        model: str,
    ) -> tuple[list[CacheSegment], list[str], str]:
        static_segments = self._build_static_segments(plan.static_blocks)
        trailing_parts = [
            block.content
            for block in [*plan.dynamic_blocks, *plan.history_blocks, *plan.result_blocks]
            if block.content
        ]
        adapter_name = "anthropic" if is_anthropic_model(model) else "flat"
        return static_segments, trailing_parts, adapter_name

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
            cache_candidate = effective_stability in {"stable", "semi_stable"} and (
                block.cacheable or block.zone == "static"
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
        return left.cacheable == right.cacheable and left.stability == right.stability


def is_anthropic_model(model: str) -> bool:
    lowered = model.lower()
    return any(lowered.startswith(prefix) for prefix in _ANTHROPIC_PREFIXES)


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
