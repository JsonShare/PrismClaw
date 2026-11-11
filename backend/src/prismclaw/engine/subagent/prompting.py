from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext

from prismclaw.engine.subagent.shared_types import PreloadedSkillContext
from prismclaw.schema.services.skills import SkillManifest

logger = logging.getLogger(__name__)


def coerce_allowed_tool_names(raw: Any) -> set[str] | None:
    if not isinstance(raw, list):
        return None
    names = {item.strip() for item in raw if isinstance(item, str) and item.strip()}
    return names or set()


def coerce_preloaded_skill_manifest(raw: Any, *, skill_name: str) -> SkillManifest:
    if isinstance(raw, SkillManifest):
        return raw
    try:
        return SkillManifest.model_validate(raw)
    except Exception as exc:
        raise ValueError(
            f"预加载技能 {skill_name!r} 生成了无效的清单数据"
        ) from exc


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


def resolve_preloaded_allowed_tools_limit(
    skills: list[SkillManifest],
) -> set[str] | None:
    limited: set[str] | None = None
    union: set[str] | None = None
    for skill in skills:
        allowed = coerce_allowed_tool_names(skill.allowed_tools)
        if allowed is None:
            continue
        union = set(allowed) if union is None else union | allowed
        limited = set(allowed) if limited is None else limited & allowed
    if limited is not None and not limited and union is not None:
        logger.warning(
            "preloaded_skill_empty_intersection union_fallback=%s",
            sorted(union),
        )
        limited = union
    return limited


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
    manifest: Any,
    skill_runtime: Any,
) -> PreloadedSkillContext:
    # 方案 §0.1 #2: preload_skills(LLM 注入用) 与 declared_invoke_sequence(审计埋点用)
    # 值同步,但语义分工不同。这里从 declared_invoke_sequence 读即可(与 preload_skills 同源)。
    declared = getattr(manifest, "declared_invoke_sequence", None)
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
            allowed_tools_limit=None,
        )
    if skill_runtime is None:
        raise RuntimeError("skill_runtime is required when declared_invoke_sequence requests preloads")
    resolve_skill = getattr(skill_runtime, "resolve_skill", None)
    if not callable(resolve_skill):
        raise RuntimeError("skill_runtime does not expose resolve_skill() for declared_invoke_sequence")
    workspace_path = Path(parent_ctx.workspace_path)
    resolved_manifests: list[SkillManifest] = []
    for skill_name in names:
        resolved = resolve_skill(skill_name, workspace_path=workspace_path)
        if resolved is None:
            raise LookupError(f"Skill '{skill_name}' not found.")
        skill = coerce_preloaded_skill_manifest(resolved, skill_name=skill_name)
        # v5 渐进式披露：如果 body 未加载（None），通过 skill_runtime 按需加载（含延迟 content pipeline）
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
        allowed_tools_limit=resolve_preloaded_allowed_tools_limit(resolved_manifests),
    )
