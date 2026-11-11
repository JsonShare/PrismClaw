"""Feature flag resolution for staged rollouts.

Provides a single entry point ``is_enabled(name, default)`` that checks
(in order):

1. The exact env-var ``PRISMCLAW_FLAG_<UPPER_NAME_WITH_UNDERSCORE>``
   (``true`` / ``1`` / ``yes`` ⇒ True; ``false`` / ``0`` / ``no`` ⇒ False).
2. The namespaced env-var ``PRISMCLAW_<UPPER_NAME_WITH_UNDERSCORE>`` (same
   truth values).
3. The static ``_STATIC_FLAGS`` table, set by tests or runtime bootstrap
   when ``Settings`` is loaded.
4. The ``default`` argument.

The flag name convention (v3 §11.2) is::

    phase_<n>.<feature_name>[_v<m>]
    e.g. phase_0.streaming_v2
         phase_0.scheduler_v2

The lookup is fully case-insensitive on the input name (so callers may
pass ``phase_0.STREAMING_V2`` if they prefer).

By design this module has **no** import on ``prismclaw.infra.settings``
to avoid a circular import and to keep the test surface small. Settings
loading (if you want flags in ``server.yaml``) calls
``set_static_flag(name, value)`` after the JSON file is parsed.
"""

from __future__ import annotations

import os
import threading
from typing import Mapping

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "disabled"})

# Module-level static table. ``Settings`` loader is expected to call
# ``set_static_flags_from_mapping`` after parsing ``server.yaml`` so
# that runtime checks can stay synchronous and side-effect free.
_STATIC_FLAGS: dict[str, bool] = {}
_STATIC_LOCK = threading.Lock()


def _normalize(name: str) -> str:
    return name.strip().lower()


def _to_env_suffix(name: str) -> str:
    return _normalize(name).replace(".", "_").replace("-", "_")


def _env_var_names(name: str) -> tuple[str, ...]:
    suffix = _to_env_suffix(name)
    return (
        f"PRISMCLAW_FLAG_{suffix.upper()}",
        f"PRISMCLAW_{suffix.upper()}",
    )


def _parse_env_value(raw: str | None) -> bool | None:
    if raw is None:
        return None
    s = raw.strip().lower()
    if s in _TRUE_TOKENS:
        return True
    if s in _FALSE_TOKENS:
        return False
    # Reject ambiguous values — never silently default
    raise ValueError(
        f"unrecognized truth value {raw!r}; "
        f"expected one of {sorted(_TRUE_TOKENS | _FALSE_TOKENS)}"
    )


def is_enabled(name: str, default: bool = False) -> bool:
    """Resolve a feature flag.

    Resolution order:
        1. env var ``PRISMCLAW_FLAG_<UPPER>``
        2. env var ``PRISMCLAW_<UPPER>``
        3. static table entry (set by ``set_static_flag``)
        4. ``default`` argument

    Returns the resolved boolean. Never raises for a missing flag.
    """
    key = _normalize(name)
    for var in _env_var_names(key):
        parsed = _parse_env_value(os.environ.get(var))
        if parsed is not None:
            return parsed
    with _STATIC_LOCK:
        if key in _STATIC_FLAGS:
            return _STATIC_FLAGS[key]
    return default


def set_static_flag(name: str, value: bool) -> None:
    """Override a flag value from settings/tests.

    Last write wins. Pass the dot-separated name (``phase_0.streaming_v2``).
    """
    key = _normalize(name)
    with _STATIC_LOCK:
        _STATIC_FLAGS[key] = bool(value)


def set_static_flags_from_mapping(mapping: Mapping[str, bool]) -> None:
    """Bulk-set static flags. Used by ``Settings`` post-load hooks."""
    with _STATIC_LOCK:
        for k, v in mapping.items():
            _STATIC_FLAGS[_normalize(k)] = bool(v)


def clear_static_flags() -> None:
    """Drop every static flag. Test-only convenience."""
    with _STATIC_LOCK:
        _STATIC_FLAGS.clear()


def snapshot_static_flags() -> dict[str, bool]:
    """Return a shallow copy of the static flag table. Test/debug only."""
    with _STATIC_LOCK:
        return dict(_STATIC_FLAGS)


# --- Public flag name constants ---------------------------------------
# These constants exist so callers don't accidentally misspell flag names.
# The list mirrors the WP-0.2a / 0.2c / 0.3 plans exactly.

FLAG_STREAMING_V2 = "phase_0.streaming_v2"
FLAG_SCHEDULER_V2 = "phase_0.scheduler_v2"
# 工具面收敛：开启后仅核心工具默认可见，其余工具视同 deferred（tool_search 发现后解锁）。
FLAG_CORE_VISIBLE_TOOLS = "tools.core_visible_only"

__all__ = [
    "FLAG_CORE_VISIBLE_TOOLS",
    "FLAG_SCHEDULER_V2",
    "FLAG_STREAMING_V2",
    "clear_static_flags",
    "is_enabled",
    "set_static_flag",
    "set_static_flags_from_mapping",
    "snapshot_static_flags",
]
