"""Skill origin classification — filesystem and plugin source resolution.

This module determines where a skill comes from (bundled/project/plugin/mcp)
based on its manifest path and plugin descriptor metadata. It defines its own
Protocol for the descriptor interface so that the skills package does not
depend on core.services.plugins.
"""
from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.services.skills import SkillManifest


@runtime_checkable
class SkillOriginDescriptor(Protocol):
    """Minimal interface that origin.py needs from a skill descriptor.

    This Protocol decouples ``skills`` from ``core.services.plugins.descriptors``:
    any object providing ``origin`` and ``plugin_name`` satisfies the contract.
    The concrete ``SkillDescriptor`` in core already implements this.
    """

    origin: str | None
    plugin_name: str | None


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except Exception:
        return False


def _skill_settings(settings: Any) -> Any:
    nested = getattr(settings, "skills", None)
    return nested if nested is not None else settings


def _plugin_settings(settings: Any) -> Any:
    return getattr(settings, "plugins", None)


def _capability_root(workspace_root: Path, raw: str) -> Path:
    """把 settings 里的能力目录解析成绝对路径。

    ``~`` 前缀（``~/.prismclaw/assets/plugins``）直接 expanduser；相对目录才拼到
    workspace 根上。
    """
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (workspace_root / expanded).resolve()


def _iter_plugin_roots(workspace_root: Path, settings: Any) -> list[Path]:
    plugin_settings = _plugin_settings(settings)
    if plugin_settings is None:
        return []

    roots: list[Path] = []
    raw = getattr(plugin_settings, "asset_plugins_dir", None)
    if isinstance(raw, str) and raw.strip():
        with suppress(Exception):
            roots.append(_capability_root(workspace_root, raw))
    return roots


def _resolve_skill_descriptor(
    manifest: SkillManifest,
    extension_manager: Any | None,
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


def _supports_skill_descriptor(extension_manager: Any | None) -> bool:
    if extension_manager is None:
        return False
    return callable(getattr(extension_manager, "get_skill_descriptor", None))


def _iter_skill_roots(workspace_root: Path, settings: Any) -> list[Path]:
    """skills 的双来源根：workspace 级 ``.work/skills`` + 用户级 assets/skills。"""
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
    settings: Any,
    extension_manager: Any | None = None,
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
        if descriptor.plugin_name:
            return SkillSource.PLUGIN
    try:
        mp = Path(manifest.file_path).expanduser().resolve()
    except Exception:
        return None

    for root in _iter_skill_roots(workspace_root, settings):
        if _path_is_under(mp, root):
            return SkillSource.PROJECT

    plugin_roots = _iter_plugin_roots(workspace_root, settings)
    plugin_root_hit = any(_path_is_under(mp, root) for root in plugin_roots)
    if plugin_root_hit and descriptor is None and _supports_skill_descriptor(extension_manager):
        return None
    if plugin_root_hit:
        return SkillSource.PLUGIN

    # 后备：路径中包含 /assets/plugins/ 的技能视为插件来源。
    # 插件只存在于用户级静态能力目录（~/.prismclaw/assets/plugins/）；
    # workspace 级不存在插件安装落点。
    try:
        parts = mp.parts
        for i, part in enumerate(parts):
            if i + 1 >= len(parts):
                continue
            next_part = parts[i + 1]
            if part == "assets" and next_part == "plugins":
                return SkillSource.PLUGIN
    except Exception:
        pass

    return None


def is_plugin_skill(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: Any,
    extension_manager: Any | None = None,
) -> bool:
    return classify_skill_origin(
        manifest,
        workspace_root,
        settings,
        extension_manager=extension_manager,
    ) is SkillSource.PLUGIN


def classify_skill_group_path(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: Any,
    extension_manager: Any | None = None,
) -> list[str]:
    descriptor = _resolve_skill_descriptor(manifest, extension_manager)
    if descriptor is not None and descriptor.plugin_name:
        return [descriptor.plugin_name]
    try:
        mp = Path(manifest.file_path).expanduser().resolve()
    except Exception:
        return []

    plugin_roots = _iter_plugin_roots(workspace_root, settings)
    plugin_root = next((root for root in plugin_roots if _path_is_under(mp, root)), None)
    plugin_root_hit = plugin_root is not None
    if plugin_root_hit:
        if descriptor is None and _supports_skill_descriptor(extension_manager):
            return []
        return _skill_group_parts_for_root(
            mp,
            plugin_root,
        )

    for root in _iter_skill_roots(workspace_root, settings):
        if _path_is_under(mp, root):
            return _skill_group_parts_for_root(mp, root)

    return []


def classify_skill_group_label(
    manifest: SkillManifest,
    workspace_root: Path,
    settings: Any,
    extension_manager: Any | None = None,
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
