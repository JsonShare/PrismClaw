# backend/src/prismclaw/engine/trajectory/__init__.py
"""
Trajectory audit public entry surface.

This module exposes ONLY the v2.0 surface:
  - OFF / DRY / FULL level constants
  - AuditLevel policy dataclass
  - resolve_policy(header, env) → AuditLevel     (pure function)
  - install_auditor_if_enabled(session_id, bus, *, persistence,
        header, policy, tool_registry)
        → TrajectoryAuditService | None            (factory used by runner)

Feature flag still OFF/DRY/FULL three tiers (env or per-request header):
  1. Per-request HTTP header X-PrismClaw-Trajectory: full|dry|tools=1|system=1
  2. ENV var PRISMCLAW_TRAJECTORY_AUDIT = OFF|DRY|FULL
  3. Default: OFF — zero cost, zero behaviour change.
"""
from __future__ import annotations

import os
from typing import Any


# ========================================================================
# Policy constants & class
# ========================================================================

OFF = "off"
DRY = "dry"
FULL = "full"
_VALID_LEVELS = {OFF, DRY, FULL}


class AuditLevel:
    """Parsed per-turn policy. All fields bool or known enum."""

    __slots__ = ("level", "store_system", "store_schemas")

    def __init__(self, level: str, store_system: bool, store_schemas: bool):
        if level not in _VALID_LEVELS:
            raise ValueError(f"invalid trajectory audit level: {level!r}")
        self.level = level
        self.store_system = store_system
        self.store_schemas = store_schemas

    @property
    def enabled(self) -> bool:
        return self.level != OFF

    @property
    def dry_run(self) -> bool:
        return self.level == DRY


# ========================================================================
# ENV + Header resolution (pure, testable)
# ========================================================================

def _env_level() -> str:
    # Default FULL — trajectory audit is a core observability feature.
    # Set PRISMCLAW_TRAJECTORY_AUDIT=off to disable (e.g. for load testing).
    raw = os.environ.get("PRISMCLAW_TRAJECTORY_AUDIT", "full").strip().upper()
    m = {"": FULL, "0": OFF, "OFF": OFF, "L0": OFF,
         "1": DRY, "DRY": DRY, "L1": DRY,
         "2": FULL, "FULL": FULL, "L2": FULL}
    return m.get(raw, FULL)


def _parse_header_flags(header: str | None) -> tuple[str | None, bool, bool]:
    """Parse 'full,tools=1,system=1' style. Returns (override_level_or_None, system, tools)."""
    if not header:
        return None, False, False
    level = None
    system = False
    tools = False
    for piece in header.lower().split(","):
        p = piece.strip()
        if not p:
            continue
        if "=" in p:
            k, v = p.split("=", 1)
            flag = v.strip().lower() in {"1", "true", "yes", "on"}
            if k.strip() == "system":
                system = flag
            elif k.strip() == "tools":
                tools = flag
        elif p in _VALID_LEVELS:
            level = p
    return level, system, tools


def resolve_policy(*, header: str | None = None, env: str | None = None) -> AuditLevel:
    """Pure function resolve effective AuditLevel from env + header."""
    env_level = env if env is not None else _env_level()
    override, header_system, header_tools = _parse_header_flags(header)
    eff_level = override if override is not None else env_level
    if eff_level == OFF:
        return AuditLevel(OFF, False, False)

    in_prod = os.environ.get("PRISMCLAW_ENV", "development").strip().lower() == "production"
    # Production default: NEVER store full system prompt text — only len+hash
    # unless explicit per-request override header flags it.
    store_system = (not in_prod) or header_system
    store_schemas = header_tools  # default OFF — tools schema bloats record size
    return AuditLevel(eff_level, store_system, store_schemas)


# ========================================================================
# Auditor Service factory — the ONLY shape runner.py imports.
#
# Runner does NOT new TrajectoryAuditService directly; it calls this
# function.  This gives trajectory the freedom to:
#   (a) honour env/header feature flag (OFF = return None = auditor skip).
#   (b) swap the backend implementation without touching runner code.
#   (c) raise cleanly and let runner NOP (it still binds a bus).
# ========================================================================

def install_auditor_if_enabled(
    session_id: str,
    bus: Any,
    *,
    persistence: Any = None,
    header: str | None = None,
    policy: "AuditLevel | None" = None,
    tool_registry: Any = None,
) -> Any:
    """Create + return a TrajectoryAuditService, or None if disabled.

    - If policy resolves to OFF → returns None (no subscribers registered,
      bus stays empty — publish = NOP).
    - Otherwise returns a **fresh TrajectoryAuditService instance**; the
      caller is responsible for calling `await svc.start()` to kick off the
      async restore-from-db step, and `await svc.close()` at teardown.

    ``persistence`` is a TrajectoryPersistence implementation (typically
    PgTrajectoryPersistence) injected by the app layer.
    ``tool_registry`` (the agent's ToolRegistry) enables tool-cell
    schema_detail resolution.
    """
    import logging
    _log = logging.getLogger(__name__)
    _log.info(
        "install_auditor_if_enabled: sid=%s header=%s persistence=%s",
        session_id, header, "YES" if persistence is not None else "NO",
    )
    from prismclaw.engine.trajectory.service import TrajectoryAuditService

    resolved = policy if policy is not None else resolve_policy(header=header)
    _log.info(
        "trajectory policy resolved: level=%s enabled=%s",
        resolved.level, resolved.enabled,
    )
    if not resolved.enabled:
        _log.info("trajectory audit DISABLED for sid=%s", session_id)
        return None
    svc = TrajectoryAuditService(
        session_id=session_id,
        bus=bus,
        persistence=persistence,
        tool_registry=tool_registry,
    )
    _log.info("trajectory audit ENABLED for sid=%s", session_id)
    return svc


__all__ = [
    # constants
    "OFF", "DRY", "FULL", "AuditLevel",
    # factory / resolver
    "resolve_policy", "install_auditor_if_enabled",
]
