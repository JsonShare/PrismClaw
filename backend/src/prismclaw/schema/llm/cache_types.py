"""WP-2.2 — centralised prompt-cache markers.

Before WP-2.2, the literal ``{"type": "ephemeral"}`` was duplicated in
4 places (one in ``llm.py``, two in ``request_packing.py``, one in
``cache_block_packing.py``). The Anthropic prompt-cache spec also
defines ``5m`` / ``1h`` TTLs we may want later, so a single source of
truth keeps the markers auditable and the litmus test for "where do we
mark a block as cacheable" becomes one file.

Usage:

    from prismclaw.schema.llm.cache_types import (
        EPHEMERAL_CACHE_CONTROL,
        mark_cacheable,
        with_cache_control,
    )

    # 1. Mutate a block in place (last-block pattern in
    #    request_packing.py:181).
    last_block["cache_control"] = EPHEMERAL_CACHE_CONTROL

    # 2. Build a new block with the marker attached.
    block = with_cache_control({"type": "text", "text": "..."})
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Final


# The single source of truth for the Anthropic ephemeral cache marker.
# Frozen dict semantics are emulated via Final + a helper for the few
# places that need to copy. Never mutate this object.
EPHEMERAL_CACHE_CONTROL: Final[dict[str, str]] = {"type": "ephemeral"}


def with_cache_control(
    block: dict[str, Any],
    *,
    ephemeral: bool = True,
) -> dict[str, Any]:
    """Return a copy of ``block`` with the cache marker attached.

    The original ``block`` is left untouched (callers sometimes hold
    references to the same dict for many turns).

    When ``ephemeral`` is False the block is returned as a plain copy
    — no marker is attached. This keeps the helper useful for tests
    that want to verify the *absence* of a marker.
    """
    out = deepcopy(block) if block is not EPHEMERAL_CACHE_CONTROL else dict(block)
    if ephemeral:
        out["cache_control"] = EPHEMERAL_CACHE_CONTROL
    return out


def mark_cacheable(
    block: dict[str, Any],
    *,
    ephemeral: bool = True,
) -> dict[str, Any]:
    """In-place variant of :func:`with_cache_control` for hot paths
    where the block is single-use (last-block pattern in
    ``request_packing.py:181``)."""
    if ephemeral:
        block["cache_control"] = EPHEMERAL_CACHE_CONTROL
    return block