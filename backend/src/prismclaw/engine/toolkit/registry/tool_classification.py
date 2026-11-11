"""Tool semantic classification — the single authority for ``tool_class``.

Two layers live here:

1. :func:`resolve_tool_class` — the **only** place that decides whether a tool
   is ``read`` or ``write``. Every governance consumer (evidence recording,
   intent evolution, delivery ledger, completion fact evidence) must call it
   instead of inspecting tool attributes or matching names on its own.
2. Web-retrieval semantics (:func:`is_search_intent_tool`,
   :func:`is_legacy_web_tool`, :func:`is_network_read_tool`) — an *input* to
   that decision, not a parallel classifier.

Web search/fetch is provided by a standalone MCP service wired through
``backend/configs/mcp.yaml`` — the service (and therefore the tool name) is a
deployment decision, not a code constant. Engine code must never hard-code
that name; instead it asks this module semantic questions:

- :func:`is_search_intent_tool` — tools configured as search/fetch intent
  (``RunLoopTuningConfig.search_intent_tools``, mirrored in agent.yaml);
- :func:`is_legacy_web_tool` — the retired builtin names kept only so
  historical sessions keep rendering/compacting correctly;
- :func:`is_network_read_tool` — public-network read-only tools (union of the
  two above).

Both name layers are understood: MCP canonical ``{server}:{tool}`` and the
LLM-facing rewrite ``{server}__{tool}``.
"""

from __future__ import annotations

from typing import Any, Literal

# Retired builtins — recognized purely for historical-session compatibility.
_LEGACY_WEB_TOOLS = frozenset({"web_search", "web_fetch", "web_extract"})


def _canonical(name: str) -> str:
    """Normalize an LLM-facing ``{server}__{tool}`` name to ``{server}:{tool}``."""
    return name.replace("__", ":", 1) if "__" in name else name


def _configured_names(config: Any) -> list[str] | None:
    names = getattr(config, "search_intent_tools", None) if config is not None else None
    return list(names) if names else None


def is_search_intent_tool(tool_name: str, *, config: Any = None) -> bool:
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


def is_legacy_web_tool(tool_name: str) -> bool:
    """True for the retired builtin web tools (historical sessions only)."""
    return tool_name.lower() in _LEGACY_WEB_TOOLS


def is_network_read_tool(tool_name: str, *, config: Any = None) -> bool:
    """True for public-network read-only tools (search intent or legacy web)."""
    return is_search_intent_tool(tool_name, config=config) or is_legacy_web_tool(tool_name)


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


def _declared_tool_class(spec: Any) -> str | None:
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
    tool_registry: Any = None,
    *,
    config: Any = None,
) -> ResolvedToolClass:
    """Resolve the governance class of *tool_name*: ``read`` or ``write``.

    Resolution order (first match wins):

    1. tool-declared class from the registry (``read`` and
       ``memory-write-safe`` → ``read``; anything else, including ``write``
       and unknown values → ``write``);
    2. network-read semantics (:func:`is_network_read_tool`) for tools the
       registry cannot resolve — e.g. legacy ``web_search`` names in
       historical sessions, or MCP tools outside the current registry;
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
