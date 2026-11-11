from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from prismclaw.schema.config.config import SandboxConfig
from prismclaw.engine.security.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)


class _PassthroughPolicy:
    """Internal passthrough sandbox policy.

    Only used when ``enforce=False`` (development mode).
    In production (``enforce=True``), BwrapPolicy is required.
    """

    backend: str = "none"

    def wrap_bash_command(self, cmd: str, ctx: Any) -> tuple[str, list[str]]:
        return ("/bin/sh", ["-c", cmd])

    def wrap_mcp_stdio(self, params: Any, server_name: str, sandbox_config: Any) -> Any:
        return params


@dataclass
class SandboxState:
    policy: SandboxPolicy
    backend: str
    bwrap_version: str | None
    warning: str | None
    runtime_warning: str | None = None

    def set_runtime_warning(self, msg: str) -> None:
        if self.runtime_warning != msg:
            self.runtime_warning = msg
            logger.critical("sandbox runtime warning: %s", msg)


def _detect_bwrap_version() -> str | None:
    path = shutil.which("bwrap")
    if path is None:
        return None
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0] if result.stdout else None
    except Exception:
        logger.warning("failed to detect bwrap --version", exc_info=True)
    return None


class SandboxStartupError(RuntimeError):
    """Raised when ``enforce=True`` but bwrap is missing."""


def resolve_sandbox_state(settings: SandboxConfig) -> SandboxState:
    """Resolve the runtime SandboxPolicy from the configured sandbox settings."""
    if not settings.enforce:
        return SandboxState(
            policy=_PassthroughPolicy(),
            backend="none",
            bwrap_version=None,
            warning="sandbox.enforce=False; running WITHOUT process isolation",
        )

    from prismclaw.engine.security.sandbox.bwrap import BwrapBinaryNotFound, BwrapPolicy

    bwrap_version = _detect_bwrap_version()

    try:
        policy = BwrapPolicy(settings=settings)
    except BwrapBinaryNotFound as exc:
        raise SandboxStartupError(str(exc)) from exc

    state = SandboxState(
        policy=policy,
        backend="bwrap",
        bwrap_version=bwrap_version,
        warning=None,
    )
    return state


def health_advisory(state: SandboxState) -> dict[str, Any]:
    advisory: dict[str, Any] = {
        "ready": True,
        "backend": state.backend,
        "bwrap_version": state.bwrap_version,
        "warning": state.warning,
    }
    if state.runtime_warning is not None:
        advisory["runtime_warning"] = state.runtime_warning
        advisory["ready"] = False
    return advisory
