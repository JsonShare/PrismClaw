"""Skill origin classification — filesystem and expert source resolution.

This module determines where a skill comes from (bundled/project/expert/mcp)
based on its manifest path and expert descriptor metadata. It defines its own
Protocol for the descriptor interface so that the skills package does not
depend on core.services.experts.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Protocol, runtime_checkable

from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.services.skills import SkillManifest


@runtime_checkable
class SkillOriginDescriptor(Protocol):
    """Minimal interface that origin.py needs from a skill descriptor.

    This Protocol decouples ``skills`` from ``core.services.experts.descriptors``:
    any object providing ``origin`` and ``expert_name`` satisfies the contract.
    The concrete ``SkillDescriptor`` in core already implements this.
    """

    origin: str | None
    expert_name: str | None


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except Exception:
        return False


def _skill_settings(settings: object) -> object:
    nested = getattr(settings, "skills", None)
    return nested if nested is not None else settings


def _expert_settings(settings: object) -> object:
    return getattr(settings, "experts", None)


def _capability_root(workspace_root: Path, raw: str) -> Path:
    """把 settings 里的能力目录解析成绝对路径。

    ``~`` 前缀（``~/.prismclaw/assets/experts``）直接 expanduser；相对目录才拼到
    workspace 根上。
    """
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (workspace_root / expanded).resolve()


def _iter_expert_roots(workspace_root: Path, settings: object) -> list[Path]:
    expert_settings = _expert_settings(settings)
    if expert_settings is None:
        return []

    roots: list[Path] = []
    raw = getattr(expert_settings, "asset_experts_dir", None)
    if isinstance(raw, str) and raw.strip():
        with suppress(Exception):
            roots.append(_capability_root(workspace_root, raw))
    return roots


def _resolve_skill_descriptor(
    manifest: SkillManifest,
    extension_manager: object | None,
) -> SkillOriginDescriptor | None:
    if extension_manager is None:
        return None
    get_descriptor = getattr(extension_manager, "get_skill_descriptor", None)
    if not callable(get_descriptor):
        return None
    descriptor = get_descriptor(manifest.name)
    if isinstance(descriptor, SkillOriginDescriptor):
        return descriptor
    return None


def _supports_skill_descriptor(extension_manager: object | None) -> bool:
    if extension_manager is None:
        return False
    return callable(getattr(extension_manager, "get_skill_descriptor", None))


def _iter_skill_roots(workspace_root: Path, settings: object) -> list[Path]:
    """skills 的双来源根：workspace 级 ``.work/state/skills`` + 用户级 assets/skills。"""
    skill_settings = _skill_settings(settings)
    roots: list[Path] = []

    for field in ("workspace_skills_dir", "asset_skills_dir"):
        raw = getattr(skill_settings, field, None)
        if isinstance(raw, str) and raw.strip():
            with suppress(Exception):
                root = _capability_root(workspace_root, raw)
                if root not in roots:
                    roots.append(root)

    return roots


def _skill_group_parts_for_root(skill_path: Path, root: Path) -> list[str]:
    try:
        relative = skill_path.relative_to(root)
    except Exception:
        return []

    parent_parts = list(relative.parts[:-2])
    if parent_parts and parent_parts[-1] == "skills":
        parent_parts = parent_parts[:-1]
    return parent_parts


def classify_skill_origin(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: object,
    extension_manager: object | None = None,
) -> SkillSource | None:
    # MCP skills are not filesystem-backed; their origin is fixed by source.
    src = getattr(manifest, "source", None)
    if isinstance(src, SkillSource):
        if src is SkillSource.MCP:
            return SkillSource.MCP
    elif src == SkillSource.MCP.value:
        return SkillSource.MCP

    descriptor = _resolve_skill_descriptor(manifest, extension_manager)
    if descriptor is not None:
        if descriptor.origin is not None:
            # descriptor.origin 可能是原始字符串或枚举,统一走 SkillSource.coerce
            return SkillSource.coerce(descriptor.origin)
        if descriptor.expert_name:
            return SkillSource.EXPERT
    try:
        mp = Path(manifest.file_path).expanduser().resolve()
    except Exception:
        return None

    for root in _iter_skill_roots(workspace_root, settings):
        if _path_is_under(mp, root):
            return SkillSource.PROJECT

    expert_roots = _iter_expert_roots(workspace_root, settings)
    expert_root_hit = any(_path_is_under(mp, root) for root in expert_roots)
    if expert_root_hit and descriptor is None and _supports_skill_descriptor(extension_manager):
        return None
    if expert_root_hit:
        return SkillSource.EXPERT

    # 后备：路径中包含 /assets/experts/ 的技能视为专家包来源。
    # 专家包只存在于用户级静态能力目录（~/.prismclaw/assets/experts/）；
    # workspace 级不存在专家包安装落点。
    try:
        parts = mp.parts
        for i, part in enumerate(parts):
            if i + 1 >= len(parts):
                continue
            next_part = parts[i + 1]
            if part == "assets" and next_part == "experts":
                return SkillSource.EXPERT
    except Exception:
        pass

    return None


def is_expert_skill(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: object,
    extension_manager: object | None = None,
) -> bool:
    return (
        classify_skill_origin(
            manifest,
            workspace_root,
            settings,
            extension_manager=extension_manager,
        )
        is SkillSource.EXPERT
    )


def classify_skill_group_path(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: object,
    extension_manager: object | None = None,
) -> list[str]:
    descriptor = _resolve_skill_descriptor(manifest, extension_manager)
    if descriptor is not None and descriptor.expert_name:
        return [descriptor.expert_name]
    try:
        mp = Path(manifest.file_path).expanduser().resolve()
    except Exception:
        return []

    expert_roots = _iter_expert_roots(workspace_root, settings)
    expert_root = next((root for root in expert_roots if _path_is_under(mp, root)), None)
    if expert_root is not None:
        if descriptor is None and _supports_skill_descriptor(extension_manager):
            return []
        return _skill_group_parts_for_root(
            mp,
            expert_root,
        )

    for root in _iter_skill_roots(workspace_root, settings):
        if _path_is_under(mp, root):
            return _skill_group_parts_for_root(mp, root)

    return []


def classify_skill_group_label(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: object,
    extension_manager: object | None = None,
) -> str | None:
    parts = classify_skill_group_path(
        manifest,
        workspace_root,
        settings,
        extension_manager=extension_manager,
    )
    if not parts:
        return None
    return " / ".join(parts)
