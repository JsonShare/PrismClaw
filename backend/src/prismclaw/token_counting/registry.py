"""Active token counter + provider registry.

The active counter is process-wide so that low-level helpers (schema
utilities, compaction, telemetry, tool registry) can share it without threading
a dependency through every call. Providers are registered by name; the
``token_counting.provider`` setting selects one at assembly time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from prismclaw.token_counting.heuristic import HeuristicTokenCounter
from prismclaw.token_counting.protocol import TokenCounter

TokenCounterFactory = Callable[..., TokenCounter]

_PROVIDERS: dict[str, TokenCounterFactory] = {
    "heuristic": HeuristicTokenCounter,
}
_DEFAULT_PROVIDER = "heuristic"
_active: TokenCounter = HeuristicTokenCounter()


def get_token_counter() -> TokenCounter:
    """Return the process-wide active counter."""
    return _active


def set_token_counter(counter: TokenCounter) -> None:
    """Install a counter implementation (called once during assembly)."""
    global _active
    _active = counter


def reset_token_counter() -> None:
    """Restore the default heuristic counter (tests / config rollback)."""
    global _active
    _active = HeuristicTokenCounter()


def register_token_provider(name: str, factory: TokenCounterFactory) -> None:
    """Register an alternative provider by name (plugin extension point)."""
    _PROVIDERS[name] = factory


def available_token_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDERS))


def build_token_counter(provider: str = _DEFAULT_PROVIDER, **options: Any) -> TokenCounter:
    """Instantiate a registered provider.

    Raises:
        ValueError: if ``provider`` is unknown.
    """
    factory = _PROVIDERS.get(provider)
    if factory is None:
        raise ValueError(
            f"unknown token_counting.provider {provider!r}; "
            f"registered providers: {', '.join(available_token_providers())}"
        )
    return factory(**options)


# ── Facade helpers so call sites never import the active instance directly ──


def count_text(
    text: str,
    *,
    image_tokens_each: int = 0,
    image_placeholder: str = "",
) -> int:
    return get_token_counter().count_text(
        text,
        image_tokens_each=image_tokens_each,
        image_placeholder=image_placeholder,
    )


def count_message(
    msg: Mapping[str, Any],
    *,
    image_tokens_each: int = 0,
) -> int:
    return get_token_counter().count_message(msg, image_tokens_each=image_tokens_each)


def count_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    image_tokens_each: int = 0,
) -> int:
    return get_token_counter().count_messages(
        messages,
        image_tokens_each=image_tokens_each,
    )


def count_tool_schemas(schemas: Sequence[Mapping[str, Any]]) -> int:
    return get_token_counter().count_tool_schemas(schemas)


def count_bytes(n_bytes: int, *, kind: Literal["json", "wire"] = "json") -> int:
    return get_token_counter().count_bytes(n_bytes, kind=kind)
