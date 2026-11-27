"""Tool semantic classification — the single authority for ``tool_class``.

Two layers live here:

1. :func:`resolve_tool_class` — the **only** place that decides whether a tool
   is ``read`` or ``write``. Every governance consumer (evidence recording,
   intent evolution, delivery ledger, completion fact evidence) must call it
   instead of inspecting tool attributes or matching names on its own.
2. Web-retrieval semantics (:func:`is_search_intent_tool`,
   :func:`is_network_read_tool`) — an *input* to that decision, not a
   parallel classifier.

Web search/fetch is provided by a standalone MCP service wired through
``backend/configs/mcp.json`` — the service (and therefore the tool name) is a
deployment decision, not a code constant. Engine code must never hard-code
that name; instead it asks this module semantic questions:

- :func:`is_search_intent_tool` — tools configured as search/fetch intent
  (``RunLoopTuningConfig.search_intent_tools``, mirrored in agent.yaml);
- :func:`is_network_read_tool` — public-network read-only tools.

Both name layers are understood: MCP canonical ``{server}:{tool}`` and the
LLM-facing rewrite ``{server}__{tool}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry
    from prismclaw.engine.toolkit.registry.specs import ToolSpec
    from prismclaw.engine.toolkit.registry.tool_runtime import Tool
    from prismclaw.schema.config.config import RunLoopTuningConfig


def _canonical(name: str) -> str:
    """Normalize an LLM-facing ``{server}__{tool}`` name to ``{server}:{tool}``."""
    return name.replace("__", ":", 1) if "__" in name else name


def _configured_names(config: RunLoopTuningConfig | None) -> list[str] | None:
    names = getattr(config, "search_intent_tools", None) if config is not None else None
    return list(names) if names else None


def is_search_intent_tool(tool_name: str, *, config: RunLoopTuningConfig | None = None) -> bool:
    """True when *tool_name* is configured as a search/fetch intent tool.

    Without an explicit *config* the schema default is used, which mirrors the
    shipped agent.yaml — renaming the MCP service only touches configuration.
    """
    names = _configured_names(config)
    if names is None:
        from prismclaw.schema.config.config import RunLoopTuningConfig

        names = RunLoopTuningConfig().search_intent_tools
    canonical = _canonical(tool_name)
    return any(_canonical(name) == canonical for name in names)


def is_network_read_tool(tool_name: str, *, config: RunLoopTuningConfig | None = None) -> bool:
    """True for public-network read-only tools (search intent)."""
    return is_search_intent_tool(tool_name, config=config)


def denied_owner_reason(
    tool: object | None,
    allowed_owners: frozenset[str] | None,
) -> str | None:
    """工具归属判定唯一实现：``None`` 放行，否则返回拒绝原因。

    ``allowed_owners`` 为 None 表示无作用域上下文（headless / 装配未就绪），
    不做归属限制。主循环 ``before_tool_call`` 与子代理 dispatch **都必须**
    走这里——两处一旦各自实现，就会出现「主循环拦住、子代理放行」。
    """
    if allowed_owners is None or tool is None:
        return None
    owner = getattr(tool, "expert_name", None)
    if owner is None or owner in allowed_owners:
        return None
    return (
        f"allowed_tool_owners_denied:{owner}"
        f"（该工具属于未启用的连接器或专家包「{owner}」）"
    )


# ---------------------------------------------------------------------------
# tool_class resolution — single authority
# ---------------------------------------------------------------------------

ResolvedToolClass = Literal["read", "write"]
"""Governance view of a tool call. Distinct from the declared ``ToolClass``
(``specs.ToolClass``), which also carries ``memory-write-safe``: the declaration
domain describes the tool, this domain answers the only question governance
asks — may this call produce a task deliverable?"""

# Tool-declared classes that must be folded into ``read``. ``memory-write-safe``
# writes agent memory only; it never produces a task deliverable, so governance
# must not treat it as productive write evidence.
_READ_CLASSES = frozenset({"read", "memory-write-safe"})


def _declared_tool_class(spec: Tool | ToolSpec | None) -> str | None:
    """Read the tool-declared class from either spec shape.

    - ``Tool`` protocol: direct ``tool_class`` attribute
    - ``ToolSpec``: nested ``permission_traits.tool_class``
    """
    if spec is None:
        return None
    declared = getattr(spec, "tool_class", None)
    if isinstance(declared, str) and declared:
        return declared
    traits = getattr(spec, "permission_traits", None)
    if traits is None:
        return None
    declared = getattr(traits, "tool_class", None)
    return declared if isinstance(declared, str) and declared else None


def resolve_tool_class(
    tool_name: str,
    tool_registry: ToolRegistry | None = None,
    *,
    config: RunLoopTuningConfig | None = None,
) -> ResolvedToolClass:
    """Resolve the governance class of *tool_name*: ``read`` or ``write``.

    Resolution order (first match wins):

    1. tool-declared class from the registry (``read`` and
       ``memory-write-safe`` → ``read``; anything else, including ``write``
       and unknown values → ``write``);
    2. network-read semantics (:func:`is_network_read_tool`) for tools the
       registry cannot resolve — e.g. MCP tools outside the current registry;
    3. ``write`` — conservative default: an unresolvable tool may have
       side effects, so it must never be silently promoted to ``read``.
    """
    spec = (
        tool_registry.get(tool_name)
        if tool_registry is not None and hasattr(tool_registry, "get")
        else None
    )
    declared = _declared_tool_class(spec)
    if declared is not None:
        return "read" if declared in _READ_CLASSES else "write"
    if is_network_read_tool(tool_name, config=config):
        return "read"
    return "write"
