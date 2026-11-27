from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.execution.capabilities.catalog import (
    AgentCatalogEntry,
    RuntimeCatalog,
    SkillCatalogEntry,
)
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.agent.capability import ExpertCommandCapability

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
    from prismclaw.engine.hooks import SkillProvider
    from prismclaw.engine.subagent.definition.registry import SubAgentRegistry
    from prismclaw.schema.services.skills import SkillManifest


def assemble_capability_catalog(
    *,
    workspace_path: str | Path | None = None,
    skill_provider: SkillProvider | None = None,
    extension_manager: ExtensionManager | None = None,
    subagent_registry: SubAgentRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
    mcp_skill_manifests_builder: Callable[[], list[SkillManifest]] | None = None,
) -> RuntimeCatalog:
    return RuntimeCatalog(
        tool_registry=tool_registry,
        skill_entries=tuple(
            _build_skill_entries(
                workspace_path=workspace_path,
                skill_provider=skill_provider,
                extension_manager=extension_manager,
                mcp_skill_manifests_builder=mcp_skill_manifests_builder,
            )
        ),
        agent_entries=tuple(
            _build_agent_entries(
                subagent_registry=subagent_registry,
                extension_manager=extension_manager,
            )
        ),
    )


def _load_provider_skill_manifests(
    skill_provider: SkillProvider | None,
    *,
    workspace_path: str | Path | None,
) -> list[Any]:
    if skill_provider is None:
        logger.debug("skill_provider is None, skipping provider skill manifests")
        return []
    list_skills = getattr(skill_provider, "list_skills", None)
    if not callable(list_skills):
        logger.warning(
            "skill_provider has no callable list_skills (type=%s)", type(skill_provider).__name__
        )
        return []
    try:
        if workspace_path is not None:
            skills = list(list_skills(str(workspace_path)))
        else:
            skills = list(list_skills())
        logger.info(
            "skill_provider.list_skills returned %d skills (workspace_path=%s)",
            len(skills),
            workspace_path,
        )
        return skills
    except TypeError:
        skills = list(list_skills())
        logger.info("skill_provider.list_skills (no-arg) returned %d skills", len(skills))
        return skills
    except Exception:
        logger.warning("skill_provider.list_skills failed", exc_info=True)
        return []


def _dedupe_by_name(items: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in items:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        deduped.append(item)
    return deduped


def _skill_entry(
    manifest: object,
    *,
    name: str | None = None,
    expert_name: str | None = None,
    origin: str | None = None,
    expert_description: str | None = None,
) -> SkillCatalogEntry:
    return SkillCatalogEntry(
        name=name or getattr(manifest, "name", "") or "",
        description=getattr(manifest, "description", "") or "",
        location=getattr(manifest, "file_path", "") or "",
        instructions=getattr(manifest, "body", "") or "",
        manifest=manifest,
        expert_name=expert_name,
        origin=origin,
        expert_description=expert_description,
    )


def _pseudo_skill_manifest_for_command(
    command: ExpertCommandCapability,
    *,
    expert_name: str | None,
) -> SkillManifest:
    """把 ExpertCommandCapability 包装为伪 SkillManifest。

    commands 是原子技能（Phase 3 迁移产物），不编排子技能，
    因此 declared_invoke_sequence / preload_skills 为空是语义正确的。
    frontmatter 字段从 capability.metadata（command dict 完整拷贝）提取。
    """
    from prismclaw.schema.agent.domains import (
        SkillSource,
        resolve_invocation_policy,
    )
    from prismclaw.schema.services.skills import SkillManifest

    meta = command.metadata or {}
    allowed_tools = meta.get("allowed_tools")
    if not isinstance(allowed_tools, list):
        allowed_tools = None
    invocation_policy = resolve_invocation_policy(meta)
    category = meta.get("category")
    if not isinstance(category, str):
        category = None
    return SkillManifest(
        name=command.name,
        description=command.description,
        body=command.body,
        source=SkillSource.EXPERT,
        loaded_from=expert_name or "",
        preload_skills=[],
        declared_invoke_sequence=[],
        invocation_policy=invocation_policy,
        allowed_tools=allowed_tools,
        category=category,
    )


def _build_skill_entries(
    *,
    workspace_path: str | Path | None,
    skill_provider: SkillProvider | None,
    extension_manager: ExtensionManager | None,
    mcp_skill_manifests_builder: Callable[[], list[SkillManifest]] | None,
) -> list[SkillCatalogEntry]:
    entries: list[SkillCatalogEntry] = []
    seen_names: set[str] = set()

    provider_manifests = _dedupe_by_name(
        _load_provider_skill_manifests(skill_provider, workspace_path=workspace_path)
    )
    logger.info(
        "capability catalog: %d provider skills, extension_manager=%s, workspace_path=%s",
        len(provider_manifests),
        type(extension_manager).__name__ if extension_manager is not None else "None",
        workspace_path,
    )
    for manifest in provider_manifests:
        name = getattr(manifest, "name", None)
        if not isinstance(name, str) or not name:
            continue
        seen_names.add(name)
        entries.append(_skill_entry(manifest))

    if extension_manager is not None:
        for descriptor in extension_manager.iter_skill_descriptors():
            manifest = descriptor.manifest
            name = getattr(manifest, "name", None)
            if not isinstance(name, str) or not name or name in seen_names:
                continue
            seen_names.add(name)
            entries.append(
                _skill_entry(
                    manifest,
                    expert_name=descriptor.expert_name,
                    origin=descriptor.origin,
                    expert_description=descriptor.expert_description,
                )
            )

        # 跨 expert 重名 commands(Cold-start-interview/matter-workspace 等)允许分别注册,
        # seen_expert_cmds 仅防止同一 expert 下重名(正常不该发生)。
        seen_expert_cmds: set[tuple[str | None, str]] = set()
        for cmd_descriptor in extension_manager.iter_expert_command_descriptors():
            capability = cmd_descriptor.capability
            if capability is None or not isinstance(capability.name, str) or not capability.name:
                continue
            expert_name = cmd_descriptor.expert_name
            dedup_key = (expert_name, capability.name)
            if dedup_key in seen_expert_cmds:
                continue
            seen_expert_cmds.add(dedup_key)
            entries.append(
                _skill_entry(
                    _pseudo_skill_manifest_for_command(capability, expert_name=expert_name),
                    name=capability.name,
                    expert_name=expert_name,
                    origin="expert_command",
                    expert_description=cmd_descriptor.expert_description,
                )
            )

    if mcp_skill_manifests_builder is not None:
        try:
            mcp_manifests = list(mcp_skill_manifests_builder())
        except Exception:
            mcp_manifests = []
        for manifest in mcp_manifests:
            name = getattr(manifest, "name", None)
            if not isinstance(name, str) or not name or name in seen_names:
                continue
            seen_names.add(name)
            entries.append(
                _skill_entry(
                    manifest,
                    origin="mcp",
                )
            )

    entries.sort(key=lambda item: (item.name, item.expert_name or ""))
    return entries


def _build_agent_entries(
    *,
    subagent_registry: SubAgentRegistry | None,
    extension_manager: ExtensionManager | None,
) -> list[AgentCatalogEntry]:
    entries: list[AgentCatalogEntry] = []
    seen_names: set[str] = set()

    manifests: list[Any] = []
    if subagent_registry is not None and hasattr(subagent_registry, "list_all"):
        manifests = list(subagent_registry.list_all())
    for manifest in _dedupe_by_name(manifests):
        name = getattr(manifest, "name", None)
        if not isinstance(name, str) or not name:
            continue
        seen_names.add(name)
        entries.append(
            AgentCatalogEntry(
                name=name,
                description=getattr(manifest, "description", "") or "",
                manifest=manifest,
            )
        )

    if extension_manager is not None:
        for descriptor in extension_manager.iter_agent_descriptors():
            name = descriptor.canonical_id
            if not isinstance(name, str) or not name or name in seen_names:
                continue
            seen_names.add(name)
            manifest = descriptor.manifest
            entries.append(
                AgentCatalogEntry(
                    name=name,
                    description=getattr(manifest, "description", "") or "",
                    manifest=manifest,
                    expert_name=descriptor.expert_name,
                    origin=descriptor.origin,
                    expert_description=descriptor.expert_description,
                )
            )

    entries.sort(key=lambda item: item.name)
    return entries
