"""Independent token-counting plugin.

Extracted from schema/engine call sites so token accounting has a single
authority and can be swapped (heuristic → endpoint/anchored) without touching
callers. This package depends only on the standard library and is therefore
safe to import from the ``schema`` layer.
"""

from __future__ import annotations

from prismclaw.token_counting.heuristic import (
    CJK_TOKENS_PER_CHAR,
    JSON_BYTES_PER_TOKEN,
    MESSAGE_ENVELOPE_TOKENS,
    OTHER_CHARS_PER_TOKEN,
    TOOL_CALL_ENVELOPE_TOKENS,
    WIRE_BYTES_PER_TOKEN,
    HeuristicTokenCounter,
    _estimator_v2_enabled,
)
from prismclaw.token_counting.protocol import TokenCounter
from prismclaw.token_counting.registry import (
    available_token_providers,
    build_token_counter,
    count_bytes,
    count_message,
    count_messages,
    count_text,
    count_tool_schemas,
    get_token_counter,
    register_token_provider,
    reset_token_counter,
    set_token_counter,
)

__all__ = [
    "CJK_TOKENS_PER_CHAR",
    "JSON_BYTES_PER_TOKEN",
    "MESSAGE_ENVELOPE_TOKENS",
    "OTHER_CHARS_PER_TOKEN",
    "TOOL_CALL_ENVELOPE_TOKENS",
    "WIRE_BYTES_PER_TOKEN",
    "HeuristicTokenCounter",
    "TokenCounter",
    "_estimator_v2_enabled",
    "available_token_providers",
    "build_token_counter",
    "count_bytes",
    "count_message",
    "count_messages",
    "count_text",
    "count_tool_schemas",
    "get_token_counter",
    "register_token_provider",
    "reset_token_counter",
    "set_token_counter",
]
