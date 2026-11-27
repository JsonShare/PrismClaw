from __future__ import annotations

import re

from prismclaw.engine.mcp.settings import (
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


# 连接器配置常带 "<在本地填入 EHR FHIR R4 基础 URL>" 这类模板占位符
# （尖括号包住的散文，区别于 ${VAR} 环境变量引用）。这种形状不可能是合法
# 凭据或 URL，因此在 spawn 前识别并跳过。
def _is_unfilled_placeholder(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(_UNFILLED_PLACEHOLDER_RE.match(text))


def _render_prompt_result_text(result: object) -> str:
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

    def register(self, adapter: object) -> None: ...
    def unregister(self, name: str) -> None: ...
