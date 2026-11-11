from __future__ import annotations

import re
from typing import Any

from prismclaw.engine.integrations.mcp.settings import (
    _substitute_env_placeholder,
)

_UNFILLED_PLACEHOLDER_RE = re.compile(r"^<[^<>\n]{1,120}>$")


def _redact_config_value(text: str, raw_value: str) -> str:
    resolved, status = _substitute_env_placeholder(raw_value)
    if status == "resolved" and resolved:
        return text.replace(resolved, "<REDACTED>")
    if status == "literal" and raw_value:
        return text.replace(raw_value, "<REDACTED>")
    return text


# SDD context-governance W7-4: marketplace plugin configs ship template
# placeholders like "<在本地填入 EHR FHIR R4 基础 URL>" (angle-bracketed
# prose, distinct from ${VAR} env references). Values of this shape can
# never be valid credentials/URLs — detect and skip before spawning.
def _is_unfilled_placeholder(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(_UNFILLED_PLACEHOLDER_RE.match(text))


def _render_prompt_result_text(result: Any) -> str:
    """Render an MCP ``GetPromptResult`` into plain instruction text.

    Concatenates the text content of every prompt message; non-text content
    blocks (images, embedded resources) are skipped. Assistant/system roles
    are prefixed so multi-turn prompt templates stay readable.
    """
    parts: list[str] = []
    for message in getattr(result, "messages", None) or []:
        content = getattr(message, "content", None)
        text = getattr(content, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        role = getattr(message, "role", None)
        if role and role != "user":
            parts.append(f"[{role}]\n{text.strip()}")
        else:
            parts.append(text.strip())
    return "\n\n".join(parts)


class _ToolRegistryProto:
    """Minimal protocol for ToolRegistry — decouples MCP from core.

    Only requires ``register`` / ``unregister``, which are the two
    methods MCPClientManager actually calls on the registry.
    """

    def register(self, adapter: Any) -> None: ...
    def unregister(self, name: str) -> None: ...
