from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.subagent.definition.manifest import AgentManifest
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext

from prismclaw.engine.subagent.shared_types import PreloadedSkillContext
from prismclaw.schema.services.skills import SkillManifest

logger = logging.getLogger(__name__)


def coerce_preloaded_skill_manifest(raw: object, *, skill_name: str) -> SkillManifest:
    if isinstance(raw, SkillManifest):
        return raw
    try:
        return SkillManifest.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"预加载技能 {skill_name!r} 生成了无效的清单数据") from exc


def render_preloaded_skill_catalog(skills: list[SkillManifest]) -> str:
    if not skills:
        return ""
    lines = [
        "已预加载技能目录：",
        "以下技能已作为配套工作流上下文提供。",
    ]
    for skill in skills:
        description = skill.description.strip()
        location = skill.file_path.strip()
        item = f"- {skill.name}"
        if description:
            item += f"：{description}"
        lines.append(item)
        if location:
            lines.append(f"  位置：{location}")
    return "\n".join(lines).strip()


def render_preloaded_skill_instructions(skills: list[SkillManifest]) -> str:
    if not skills:
        return ""
    rendered: list[str] = [
        "已预加载技能指令：",
        "以下技能指令作为额外的执行指导。",
    ]
    for skill in skills:
        description = skill.description.strip()
        instructions = skill.body.strip() if skill.body else ""
        rendered.extend(
            [
                "",
                f"[{skill.name}]",
                *(["描述：" + description] if description else []),
                "指令：",
                instructions,
            ]
        )
    return "\n".join(rendered).strip()


def compose_subagent_system_prompt(
    *,
    system_prompt: str,
    preload_catalog_block: str,
    preload_instructions_block: str,
    memory_block: str,
) -> str:
    parts = [system_prompt.strip()]
    if preload_catalog_block:
        parts.append(preload_catalog_block)
    if preload_instructions_block:
        parts.append(preload_instructions_block)
    if memory_block:
        parts.append(memory_block)
    return "\n\n".join(part for part in parts if part).strip()


def resolve_preloaded_skill_context(
    *,
    parent_ctx: ToolContext,
    manifest: AgentManifest,
    skill_runtime: object,
) -> PreloadedSkillContext:
    # AgentManifest 的运行时预加载字段是 preload_skills；
    # declared_invoke_sequence 仅用于 SkillManifest 的工作流遵从度审计。
    declared = getattr(manifest, "preload_skills", None)
    names: list[str] = []
    for raw_name in declared or []:
        if isinstance(raw_name, str):
            name = raw_name.strip()
            if name and name not in names:
                names.append(name)
    if not names:
        return PreloadedSkillContext(
            manifests=[],
            catalog_block="",
            instructions_block="",
        )
    if skill_runtime is None:
        raise RuntimeError("skill_runtime is required when preload_skills requests preloads")
    resolve_skill = getattr(skill_runtime, "resolve_skill", None)
    if not callable(resolve_skill):
        raise RuntimeError("skill_runtime does not expose resolve_skill() for preload_skills")
    workspace_path = Path(parent_ctx.workspace_path)
    scope = getattr(parent_ctx, "session_scope", None)
    active_expert = getattr(scope, "team_name", None) if scope is not None else None
    resolved_manifests: list[SkillManifest] = []
    for skill_name in names:
        resolved = None
        if active_expert:
            # 专家包声明的预加载技能优先取本包版本：裸名同时命中通用技能时，
            # 不能让通用技能顶替包内同名技能（同名解析必须确定）。
            resolved = resolve_skill(
                f"{active_expert}:{skill_name}", workspace_path=workspace_path
            )
        if resolved is None:
            resolved = resolve_skill(skill_name, workspace_path=workspace_path)
        if resolved is None:
            raise LookupError(f"Skill '{skill_name}' not found.")
        skill = coerce_preloaded_skill_manifest(resolved, skill_name=skill_name)
        # body 未加载（frontmatter-only）时按需从文件读取
        if skill.body is None and skill.file_path:
            ensure_loaded = getattr(skill_runtime, "_ensure_body_loaded", None)
            if callable(ensure_loaded):
                try:
                    skill = ensure_loaded(skill)
                except Exception as exc:
                    logger.warning("failed to load preloaded skill body: %s (%s)", skill.name, exc)
        instructions = skill.body.strip() if skill.body else ""
        if not instructions:
            raise ValueError(f"preloaded skill {skill_name!r} does not define any instructions")
        resolved_manifests.append(skill)
    return PreloadedSkillContext(
        manifests=resolved_manifests,
        catalog_block=render_preloaded_skill_catalog(resolved_manifests),
        instructions_block=render_preloaded_skill_instructions(resolved_manifests),
    )
