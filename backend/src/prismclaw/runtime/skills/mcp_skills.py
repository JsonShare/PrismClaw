"""MCP prompt-type command → skill manifest bridge.

MCP servers may expose *prompts* (reusable prompt templates / slash-style
commands). PrismClaw surfaces these as first-class skills so the model can
discover and invoke them through the same skill discovery path as filesystem
skills.

This module deliberately lives in ``prismclaw.runtime.skills`` (NOT ``prismclaw.engine``
and NOT ``prismclaw.integrations.mcp``) so both the assembly
layer and the MCP integration can depend on it without creating a reverse
dependency (``engine`` must never import ``integrations.mcp``).

The bridge is protocol-based: it accepts any object exposing
``list_mcp_prompts() -> list[tuple[str, Prompt]]`` (the public API added to
``MCPClientManager`` / ``CompositeMCPManager``), so no ``integrations.mcp``
type is imported here.
"""

from __future__ import annotations

from typing import Any, Callable

from prismclaw.schema.services.skills import (
    McpPromptResolver,
    SkillManifest,
    mcp_skill_name,
)


def _render_mcp_skill_body(server_name: str, prompt: Any) -> str:
    """Build a human/LLM-readable instruction body for an MCP prompt skill."""
    name = getattr(prompt, "name", None) or "<unknown>"
    description = getattr(prompt, "description", None) or ""
    header = (
        f"Invoke the MCP prompt '{name}' exposed by server '{server_name}'.\n\n"
        f"{description}"
    ).strip()

    arguments = getattr(prompt, "arguments", None) or []
    arg_lines: list[str] = []
    for arg in arguments:
        arg_name = getattr(arg, "name", None)
        if not arg_name:
            continue
        required = bool(getattr(arg, "required", False))
        arg_desc = getattr(arg, "description", None) or ""
        flag = "" if required else " (optional)"
        line = f"- {arg_name}{flag}: {arg_desc}".strip()
        arg_lines.append(line)

    if arg_lines:
        header += "\n\nArguments:\n" + "\n".join(arg_lines)
    return header.strip()


def mcp_skill_manifests_from_manager(manager: Any) -> list[SkillManifest]:
    """Build ``SkillManifest`` objects for every MCP prompt command.

    Args:
        manager: Any object exposing ``list_mcp_prompts()``. ``None`` is
            accepted and returns an empty list.

    Returns:
        SkillManifests named ``{server}:{prompt}`` (see ``mcp_skill_name``)
        with ``source="mcp"`` and ``loaded_from`` set to the originating MCP
        server name.
    """
    if manager is None:
        return []
    list_prompts = getattr(manager, "list_mcp_prompts", None)
    if not callable(list_prompts):
        return []

    manifests: list[SkillManifest] = []
    for server_name, prompt in list_prompts():
        name = getattr(prompt, "name", None)
        if not isinstance(name, str) or not name:
            continue
        description = getattr(prompt, "description", None) or ""
        from prismclaw.schema.agent.domains import SkillSource
        manifests.append(
            SkillManifest(
                name=mcp_skill_name(server_name, name),
                description=description,
                body=_render_mcp_skill_body(server_name, prompt),
                source=SkillSource.MCP,
                loaded_from=server_name,
            )
        )
    return manifests


def mcp_skill_manifests_builder(
    manager: Any,
) -> Callable[[], list[SkillManifest]] | None:
    """Return a zero-arg callable for the capability assembly layer.

    Returns ``None`` when no MCP manager is available so callers can pass the
    result straight through without a separate ``None`` check.
    """
    if manager is None:
        return None

    def _build() -> list[SkillManifest]:
        return mcp_skill_manifests_from_manager(manager)

    return _build


# Must stay below the skill invocation timeout so an MCP
# prompt fetch fails with a precise error before the generic timeout.
MCP_PROMPT_FETCH_TIMEOUT_S = 8.0


def mcp_prompt_resolver(manager: Any) -> McpPromptResolver | None:
    """Build the invoke-time resolver that fetches *real* MCP prompt content.

    The catalog manifest body of an MCP skill is discovery metadata only;
    actual instructions come from ``prompts/get`` at invocation time (prompt
    templates may take arguments). The resolver is blocking and designed for
    executor threads: it delegates to
    ``manager.get_prompt_text_threadsafe`` which schedules the async MCP call
    onto the manager's event loop.
    """
    if manager is None:
        return None
    fetch: McpPromptResolver | None = getattr(manager, "get_prompt_text_threadsafe", None)
    if not callable(fetch):
        return None

    def _resolve(
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str] | None,
    ) -> str:
        try:
            return fetch(
                server_name,
                prompt_name,
                arguments,
                timeout_s=MCP_PROMPT_FETCH_TIMEOUT_S,
            )
        except TimeoutError as exc:
            # concurrent.futures.TimeoutError aliases the builtin in 3.11+;
            # re-raise as RuntimeError so the generic asyncio
            # timeout branch cannot swallow it into a misleading message.
            raise RuntimeError(
                f"MCP prompt fetch timed out after {MCP_PROMPT_FETCH_TIMEOUT_S}s "
                f"(server={server_name!r}, prompt={prompt_name!r})"
            ) from exc

    return _resolve
