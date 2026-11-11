"""Pydantic settings for the MCP integration.

Loaded as part of the top-level :class:`prismclaw.infra.settings.Settings`
(under the ``mcp`` field). Defaults to disabled so existing deployments
without MCP configured see no behavior change.
"""

from __future__ import annotations

import logging
import os
import re

from prismclaw.schema.services.mcp_settings import McpSettings, McpServerConfig, McpSandboxConfig, _command_auto_exempts_sandbox

logger = logging.getLogger(__name__)

_ENV_PLACEHOLDER_RE = re.compile(r"^\{env:([A-Z_][A-Z0-9_]*)\}$")


def _substitute_env_placeholder(value: str) -> tuple[str | None, str]:
    """Resolve a single ``env`` dict value.

    Returns a ``(resolved, status)`` pair where ``status`` is one of:

    * ``"resolved"`` — value matched the placeholder regex AND the env var
      is set; ``resolved`` is the env var's value.
    * ``"literal"`` — value did not match the placeholder regex; ``resolved``
      is the original value verbatim. Partial matches like
      ``"prefix-{env:VAR}"`` and bare strings both fall here (we explicitly
      do NOT do partial substitution — too magical, error-prone).
    * ``"missing-env-var"`` — value matched the placeholder regex but the
      env var is not set in :data:`os.environ`; ``resolved`` is ``None`` so
      the caller can mark the server ``failed`` with the missing var name.
    """

    match = _ENV_PLACEHOLDER_RE.match(value)
    if match is None:
        return value, "literal"
    var_name = match.group(1)
    env_value = os.environ.get(var_name)
    if env_value is None:
        # MCP server 启动失败的常见根因：占位符引用的环境变量未设置。
        logger.warning("mcp env placeholder unresolved: var=%s is not set", var_name)
        return None, "missing-env-var"
    return env_value, "resolved"


__all__ = [
    "McpSandboxConfig",
    "McpServerConfig",
    "McpSettings",
    "_command_auto_exempts_sandbox",
    "_substitute_env_placeholder",
]
