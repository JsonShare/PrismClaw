"""Pydantic data models for MCP settings — pure data classes, no behavior."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

_FORBIDDEN_KEY_CHARS = (":", "__")

_AUTO_EXEMPT_BASENAMES: frozenset[str] = frozenset({"npx", "uvx"})


def _command_auto_exempts_sandbox(command: str) -> bool:
    """Whether ``command``'s basename matches the npx/uvx auto-exempt list."""
    if not command:
        return False
    import os as _os

    normalized = command.rstrip("/").rstrip("\\")
    last = _os.path.basename(normalized.replace("\\", "/"))
    stem, _ext = _os.path.splitext(last)
    return stem in _AUTO_EXEMPT_BASENAMES


class McpSandboxConfig(BaseModel):
    """Per-server sandbox config. Default ``enabled`` is conditional on command.

    See ``McpServerConfig.model_validator`` for the conditional-default rule:
    npx/uvx → ``enabled=False`` (4-slot F1 auto-exempt); else
    ``enabled=True``. Operators can always force either value explicitly.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool | None = Field(
        default=None,
        description=(
            "When None, the McpServerConfig validator picks a default based on "
            "the command basename (npx/uvx → False, else True). Set explicitly "
            "to override."
        ),
    )
    filesystem: dict = Field(default_factory=dict)
    network: dict = Field(default_factory=dict)
    env_allowlist: list[str] | None = Field(default=None)


class McpServerConfig(BaseModel):
    """Per-server MCP configuration.

    Field types and defaults mirror the ``McpServerConfig`` table in the
    ``mcp-integration`` capability spec.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    command: str | None = Field(
        default=None,
        description="Executable to spawn for stdio transport (e.g. the appropriate runtime for the server package).",
    )
    args: list[str] = Field(default_factory=list, description="Arguments passed to command.")
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment variables for the spawned process. Values may use "
            "{env:VAR_NAME} placeholders, resolved at server-startup time."
        ),
    )
    url: str | None = Field(
        default=None,
        description="Endpoint URL for HTTP/SSE based MCP transports.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional HTTP headers for HTTP/SSE based MCP transports.",
    )
    transport: Literal["stdio", "sse", "streamable-http", "http"] = Field(
        default="stdio",
        alias="type",
        description="Transport type for the MCP server connection.",
    )
    enabled: bool = Field(default=True, description="Per-server kill switch.")
    trust_annotations: bool = Field(
        default=True,
        description=(
            "When True, derive tool_class from MCP ToolAnnotations.readOnlyHint. "
            "When False, force tool_class='write' for safety."
        ),
    )
    forced_tool_class: Literal["read", "write"] | None = Field(
        default=None,
        description="Override the derived tool_class for every tool from this server.",
    )
    server_read_only: bool = Field(
        default=False,
        description=(
            "Per-server read-only policy. When True, write-class tools from "
            "this server are not visible to the LLM."
        ),
    )
    deferred: bool = Field(
        default=True,
        description=(
            "When True (default), tools from this server are deferred and "
            "hidden from the LLM until discovered via tool_search. Set to "
            "False for core capability servers (e.g. web search) whose tools "
            "should always be visible without an explicit discovery step."
        ),
    )
    connect_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Maximum seconds to wait for server connection during startup.",
    )
    call_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Maximum seconds to wait for a single tool invocation.",
    )
    sandbox: McpSandboxConfig = Field(
        default_factory=McpSandboxConfig,
        description=(
            "Per-server sandbox configuration. When ``sandbox.enabled`` is "
            "None (default), it resolves to False for npx/uvx commands and "
            "True otherwise (4-slot review F1)."
        ),
    )

    @model_validator(mode="after")
    def _resolve_sandbox_default(self) -> "McpServerConfig":
        if self.sandbox.enabled is None:
            auto_exempt = _command_auto_exempts_sandbox(self.command)
            resolved = (not auto_exempt) if self.transport == "stdio" else False
            object.__setattr__(self.sandbox, "enabled", resolved)
            logger.debug(
                "mcp sandbox default resolved: command=%s transport=%s auto_exempt=%s enabled=%s",
                self.command, self.transport, auto_exempt, resolved,
            )
        return self

    @model_validator(mode="after")
    def _validate_transport_shape(self) -> "McpServerConfig":
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio transport requires 'command'")
        else:
            if not self.url:
                raise ValueError(f"{self.transport} transport requires 'url'")
        return self


class McpPluginOverride(BaseModel):
    """插件 MCP server 的连接参数补丁。

    插件自带的 ``mcp.json`` 位于仓库权威目录（容器内只读），OAuth token、
    API key 这类凭据不能写在那里。运维在 ``mcp.plugin_overrides`` 下用
    ``plugin.server`` 精确键或裸 ``server`` 共享键声明同一条目即可补上凭据。

    刻意不含 ``url`` / ``command`` / ``transport``：连接目标由插件声明，
    补丁层只补凭据与开关，避免两处各自维护 endpoint 造成漂移。
    """

    model_config = ConfigDict(extra="forbid")

    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "HTTP 头补丁，值支持 {env:VAR_NAME} 占位符（启动时解析）。"
            "与插件声明同名时以插件为准，此处只补空缺键。"
        ),
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="stdio server 的环境变量补丁，合并规则同 headers。",
    )
    enabled: bool | None = Field(
        default=None,
        description="设为 False 可停用该插件的这个 MCP server；None 表示不干预。",
    )


class McpSettings(BaseModel):
    """Top-level MCP settings, loaded as ``Settings.mcp``.

    Validators reject server config keys containing ``:`` or ``__`` because
    those characters are reserved for the canonical ``{server}:{tool}``
    namespace and the LLM-API ``__`` rewrite respectively. See spec
    ``tool-registry`` requirement "Name validation enforces unambiguous
    round-trip".
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch. When False, no MCP servers connect and no MCP code paths run.",
    )
    servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    plugin_overrides: dict[str, McpPluginOverride] = Field(
        default_factory=dict,
        description=(
            "插件声明的 MCP server 的凭据/开关补丁，键为 'plugin.server' 或裸 'server'。"
        ),
    )

    @field_validator("servers")
    @classmethod
    def _validate_server_keys(cls, servers: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
        for key in servers:
            for forbidden in _FORBIDDEN_KEY_CHARS:
                if forbidden in key:
                    raise ValueError(
                        f"MCP server config key {key!r} must not contain {forbidden!r} "
                        f"(reserved for the canonical '{{server}}:{{tool}}' namespace and the "
                        f"LLM-API '__' rewrite)."
                    )
        return servers

    @model_validator(mode="after")
    def _validate_transports(self) -> McpSettings:
        for name, server in self.servers.items():
            if server.transport not in {"stdio", "sse", "streamable-http", "http"}:
                raise ValueError(
                    f"MCP server {name!r} has unsupported transport={server.transport!r}"
                )
        return self
