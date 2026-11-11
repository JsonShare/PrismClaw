"""Strip & validate environment variables passed to the sandbox container.

Two layers:
  1. ``validate_env_allowlist`` — called at Settings parse time. Raises
     ``ValueError`` if the operator-configured allowlist contains entries
     that match the hardcoded deny floor (e.g. ``*PATH*``, shell prompt
     vars). Never skips.
  2. ``build_clean_env`` — called before each container spawn. Applies the
     hardcoded deny floor *plus* the operator-configured allowlist.

The hardcoded deny set is intentionally conservative. Operators can *narrow*
it through allowlist but never *widen* it. Any allowlist entry that would
widen the set is rejected at validation time.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from prismclaw.schema.security.deny_names import (
    HARDCODED_DENY_NAMES,
    _is_hardcoded_deny,
    validate_env_allowlist,
)

logger = logging.getLogger(__name__)

DEFAULT_ALLOWLIST: frozenset[str] = frozenset({"PATH", "HOME", "LANG", "TERM"})


def build_clean_env(
    base_env: dict[str, str] | None = None,
    *,
    allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
) -> dict[str, str]:
    """Construct a container-safe environment dict.

    Always applies the hardcoded deny set; additionally only allows variables
    named in ``allowlist`` (plus whatever ``base_env`` explicitly supplies if
    its name passes the deny check).

    Parameters
    ----------
    base_env:
        Optional explicit key/value overrides (e.g. secrets set during
        process startup after the allowlist was fixed). Keys here are still
        checked against the hardcoded deny set — a request to pass
        ``LD_PRELOAD`` through ``base_env`` is silently dropped.
    allowlist:
        Names allowed from ``os.environ``. Defaults to a minimal POSIX set.
    """

    clean: dict[str, str] = {}
    dropped: list[str] = []
    names = list(allowlist)
    for name in names:
        if _is_hardcoded_deny(name):
            dropped.append(name)
            continue
        value = os.environ.get(name)
        if value is not None:
            clean[name] = value
    if base_env:
        for name, value in base_env.items():
            if _is_hardcoded_deny(name):
                dropped.append(name)
                continue
            clean[name] = value
    if dropped:
        # 只记变量名（不含值）——deny-floor 生效即配置层有泄漏意图，属安全事件。
        logger.warning(
            "sandbox env: denied by hardcoded deny floor (names only): %s",
            sorted(set(dropped)),
        )
    logger.debug(
        "sandbox env built: allowed=%d dropped=%d allowlist_size=%d base_env_size=%d",
        len(clean), len(dropped), len(names), len(base_env or {}),
    )
    return clean
