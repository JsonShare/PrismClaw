"""Security helpers for environment variable allowlist validation.

Moved from ``prismclaw.infra.security`` to the schema layer because these
are pure functions and constants with no external dependencies.
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

HARDCODED_DENY_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "LITELLM_",
)

# Secret-like name substrings. Used to harden the ``"*"`` allowlist wildcard so
# that it can no longer admit credentials such as GOOGLE_API_KEY / STRIPE_*/etc.
HARDCODED_DENY_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "API_KEY",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
        "PRIVATE_KEY",
        "ACCESS_KEY",
    }
)

HARDCODED_DENY_NAMES: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "PRISMCLAW_SECRET",
        "PRISMCLAW_LLM_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "OPENAI_API_KEY",
        "LITELLM_PROXY",
        "LITELLM_LOG",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "KUBECONFIG",
        "KUBE_TOKEN",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DATABASE_URL",
        "REDIS_URL",
        "DOCKER_HOST",
        "VAULT_TOKEN",
        "VAULT_ADDR",
    }
)

_GLOB_DENY_PREFIX_REJECTIONS: tuple[str, ...] = (
    "ANTHROPIC_",
    "AWS_",
    "OPENAI_",
    "LITELLM_",
    "SSH_",
    "KUBE_",
    "GH_",
    "GITHUB_",
)


def _is_hardcoded_deny(name: str) -> bool:
    if name in HARDCODED_DENY_NAMES:
        return True
    upper = name.upper()
    if any(upper.startswith(prefix) for prefix in HARDCODED_DENY_PREFIXES):
        return True
    if any(sub in upper for sub in HARDCODED_DENY_SUBSTRINGS):
        return True
    return False


def validate_env_allowlist(allowlist: Iterable[str]) -> None:
    """Raise ``ValueError`` for entries that would defeat the hardcoded deny floor.

    Glob patterns matching a deny prefix (e.g. ``"AWS_*"``) are rejected at
    config-load time per Sprint 3 4-slot review F10. The bare wildcard ``"*"``
    is permitted because the deny floor still strips dangerous names.
    """
    for entry in allowlist:
        if not isinstance(entry, str):
            logger.warning("env_allowlist rejected: non-str entry type=%s", type(entry).__name__)
            raise ValueError(f"env_allowlist entry must be str, got {type(entry).__name__}")
        if entry == "*":
            continue
        if "*" in entry or "?" in entry:
            for prefix in _GLOB_DENY_PREFIX_REJECTIONS:
                if entry.startswith(prefix):
                    logger.warning(
                        "env_allowlist rejected: glob %s matches hardcoded deny prefix %s",
                        entry, prefix,
                    )
                    raise ValueError(
                        f"env_allowlist glob pattern {entry!r} is not allowed because it "
                        f"matches the hardcoded deny prefix {prefix!r}; use a specific "
                        f"variable name instead (e.g. 'AWS_REGION')"
                    )
            logger.warning("env_allowlist rejected: unsupported glob %s", entry)
            raise ValueError(
                f"env_allowlist entries must be specific names or '*'; glob {entry!r} not supported"
            )
