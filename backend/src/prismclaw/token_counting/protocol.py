"""Token counting plugin contract.

This module is intentionally dependency-free (stdlib only): both the
``schema`` layer and the ``engine`` layer may import it without creating a
layering inversion. Implementations are registered by name in
:mod:`prismclaw.token_counting.registry`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol


class TokenCounter(Protocol):
    """Count tokens for text, chat messages, tool schemas, or raw bytes.

    All methods are synchronous by design: callers run inside the request
    hot path and must never block on remote tokenizers. An implementation
    that talks to an endpoint is responsible for caching and for falling
    back locally when the endpoint is unavailable.
    """

    name: str

    def count_text(
        self,
        text: str,
        *,
        image_tokens_each: int = 0,
        image_placeholder: str = "",
    ) -> int:
        """Count tokens in ``text``.

        ``image_tokens_each`` > 0 replaces every ``image_placeholder``
        occurrence with that many tokens (image placeholders carry no text
        tokens themselves).
        """
        ...

    def count_message(
        self,
        msg: Mapping[str, Any],
        *,
        image_tokens_each: int = 0,
    ) -> int:
        """Count tokens for one OpenAI/Anthropic wire message."""
        ...

    def count_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        image_tokens_each: int = 0,
    ) -> int:
        """Count tokens for a message list."""
        ...

    def count_tool_schemas(self, schemas: Sequence[Mapping[str, Any]]) -> int:
        """Count tokens for the serialized ``tools`` wire block."""
        ...

    def count_bytes(
        self,
        n_bytes: int,
        *,
        kind: Literal["json", "wire"] = "json",
    ) -> int:
        """Convert a serialized payload size in UTF-8 bytes to tokens.

        ``kind="wire"`` is the tool-schema calibration; ``kind="json"`` is
        the generic request payload calibration.
        """
        ...
