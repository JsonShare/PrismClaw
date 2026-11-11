"""Precise token metrics extracted from API response usage fields.

Replaces character-based estimation with exact token counts from
the LLM provider's usage response. Supports cache hit/miss tracking,
per-turn attribution, and cumulative session metrics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TokenSnapshot:
    """Token usage snapshot from a single API call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_input(self) -> int:
        """Total input tokens including cache."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens

    @property
    def total_context(self) -> int:
        """Full context window size: input + cache + output."""
        return self.total_input + self.output_tokens

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0)."""
        ti = self.total_input
        if ti == 0:
            return 0.0
        return self.cache_read_tokens / ti

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_input": self.total_input,
            "total_context": self.total_context,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
        }


@dataclass
class CumulativeTokenMetrics:
    """Cumulative token metrics across multiple API calls."""
    total_input: int = 0
    total_output: int = 0
    total_cache_creation: int = 0
    total_cache_read: int = 0
    call_count: int = 0

    def record(self, snapshot: TokenSnapshot) -> None:
        """Record a single API call's token usage."""
        self.total_input += snapshot.input_tokens
        self.total_output += snapshot.output_tokens
        self.total_cache_creation += snapshot.cache_creation_tokens
        self.total_cache_read += snapshot.cache_read_tokens
        self.call_count += 1

    @property
    def avg_cache_hit_rate(self) -> float:
        """Average cache hit rate across all calls."""
        total = self.total_input + self.total_cache_creation + self.total_cache_read
        if total == 0:
            return 0.0
        return self.total_cache_read / total

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed."""
        return self.total_input + self.total_output + self.total_cache_creation + self.total_cache_read

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input": self.total_input,
            "total_output": self.total_output,
            "total_cache_creation": self.total_cache_creation,
            "total_cache_read": self.total_cache_read,
            "call_count": self.call_count,
            "avg_cache_hit_rate": round(self.avg_cache_hit_rate, 4),
            "total_tokens": self.total_tokens,
        }


def extract_token_snapshot(usage: Any) -> TokenSnapshot:
    """Extract a TokenSnapshot from an API response usage object.

    Supports:
    - Standard usage dict with input_tokens/output_tokens
    - Cache fields: cache_creation_input_tokens, cache_read_input_tokens
    - Iterations field (server-side tool loops)
    - LLMUsage dataclass from prismclaw.engine.agent.llm

    Args:
        usage: Usage object (dict, dataclass, or any object with the right attributes).

    Returns:
        TokenSnapshot with extracted values.
    """
    if usage is None:
        return TokenSnapshot()

    # Handle dict
    if isinstance(usage, dict):
        return _extract_from_dict(usage)

    # Handle dataclass / object with attributes
    return _extract_from_object(usage)


def _extract_from_dict(usage: dict[str, Any]) -> TokenSnapshot:
    """Extract from a usage dict."""
    input_tokens = _coerce_int(usage.get("input_tokens", 0))
    output_tokens = _coerce_int(usage.get("output_tokens", 0))
    cache_creation = _coerce_int(usage.get("cache_creation_input_tokens", 0))
    cache_read = _coerce_int(usage.get("cache_read_input_tokens", 0))

    # Check for iterations (server-side tool loops)
    iterations = usage.get("iterations")
    if isinstance(iterations, list) and iterations:
        last = iterations[-1]
        if isinstance(last, dict):
            input_tokens = _coerce_int(last.get("input_tokens", input_tokens))
            output_tokens = _coerce_int(last.get("output_tokens", output_tokens))

    return TokenSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
    )


def _extract_from_object(usage: Any) -> TokenSnapshot:
    """Extract from a dataclass or object with attributes."""
    return TokenSnapshot(
        input_tokens=_coerce_int(getattr(usage, "input_tokens", 0)),
        output_tokens=_coerce_int(getattr(usage, "output_tokens", 0)),
        cache_creation_tokens=_coerce_int(getattr(usage, "cache_creation_input_tokens", 0)),
        cache_read_tokens=_coerce_int(getattr(usage, "cache_read_input_tokens", 0)),
    )


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count for a message list.

    This is a fallback for when API usage data is not available. Text
    estimation delegates to the calibrated CJK-aware estimator
    (models.utils.estimate_tokens, R²=0.9978 vs the production
    tokenizer) so Chinese-heavy content is not under-counted.
    """
    from prismclaw.schema.shared import estimate_tokens

    total = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        total += estimate_tokens(text)
                    # Tool use input JSON
                    inp = block.get("input")
                    if isinstance(inp, dict):
                        total += estimate_tokens(json.dumps(inp, ensure_ascii=False))
    return max(1, total)


def _coerce_int(value: Any) -> int:
    """Safely coerce to non-negative int."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
