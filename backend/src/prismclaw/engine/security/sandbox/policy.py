from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mcp import StdioServerParameters

    from prismclaw.schema.content.workspace import SandboxTier

SandboxBackend = Literal["bwrap", "none"]


@runtime_checkable
class SandboxPolicy(Protocol):
    """Spawn-time wrapper that adds isolation around BashTool / MCP stdio.

    Implementations:
    - ``_PassthroughPolicy`` — passthrough (development only, enforce=False).
    - ``BwrapPolicy`` — wraps via the ``bwrap`` CLI (Linux namespace + seccomp).

    The set of supported backends is the single source of truth here and MUST
    stay in sync with ``SandboxSettings.enforce`` (the configured value) and the
    branches in ``resolve_sandbox_state`` (the resolver).
    """

    backend: SandboxBackend

    def wrap_bash_command(self, cmd: str, ctx: object) -> tuple[str, list[str]]:
        """Return ``(executable, args)`` for ``asyncio.create_subprocess_exec``.

        ``ctx`` is a ``ToolContext`` carrying ``workspace_root``, ``sandbox_tier``,
        ``user_id``, ``role``, and ``user_profile`` for per-user policy resolution.
        Implementations MUST preserve the original command's stdout/stderr/exit
        code semantics.
        """
        ...

    def wrap_mcp_stdio(
        self,
        params: StdioServerParameters,
        server_name: str,
        sandbox_config: Mapping[str, object],
    ) -> StdioServerParameters:
        """Return ``StdioServerParameters`` (possibly wrapped) for MCP spawn.

        ``BwrapPolicy`` rewrites ``params.command`` to ``bwrap``
        and prepends sandbox args + original command + args.
        ``_PassthroughPolicy`` returns ``params`` unchanged.
        """
        ...

    def check_network_access(self, tier: SandboxTier) -> str | None:
        """Return a deny reason when ``tier`` forbids network access, else ``None``."""
        ...
