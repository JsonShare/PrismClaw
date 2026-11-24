from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prismclaw.engine.security.sandbox.policy import SandboxBackend, SandboxPolicy
from prismclaw.schema.config.config import SandboxConfig

if TYPE_CHECKING:
    from mcp import StdioServerParameters

    from prismclaw.schema.content.workspace import SandboxTier

logger = logging.getLogger(__name__)


class _PassthroughPolicy:
    """Internal passthrough sandbox policy.

    Only used when ``enforce=False`` (development mode).
    In production (``enforce=True``), BwrapPolicy is required.
    """

    backend: SandboxBackend = "none"

    def wrap_bash_command(self, cmd: str, ctx: object) -> tuple[str, list[str]]:
        return ("/bin/sh", ["-c", cmd])

    def wrap_mcp_stdio(
        self,
        params: StdioServerParameters,
        server_name: str,
        sandbox_config: Mapping[str, object],
    ) -> StdioServerParameters:
        return params

    def check_network_access(self, tier: SandboxTier) -> str | None:
        return None


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
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
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

    from prismclaw.engine.security.sandbox.bwrap import BwrapBinaryNotFoundError, BwrapPolicy

    bwrap_version = _detect_bwrap_version()

    try:
        policy = BwrapPolicy(settings=settings)
    except BwrapBinaryNotFoundError as exc:
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
