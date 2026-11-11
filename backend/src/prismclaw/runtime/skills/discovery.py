from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from pathspec import PathSpec

from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.shared import (
    SKILL_SCAN_MAX_DEPTH,
    CapabilityLocation,
    _scan_directory_v2,
)
from prismclaw.infra.settings import SkillSettings
from prismclaw.schema.services.skills import SkillManifest
from prismclaw.runtime.skills.parser import parse_skill_file, parse_skill_frontmatter

logger = logging.getLogger(__name__)


@dataclass
class SkillCatalogState:
    """Instance-scoped state for dynamic skill discovery and conditional activation.

    Eliminates module-level globals so each DefaultSkillProvider instance
    maintains its own independent dynamic state.
    """
    discovered_dirs: dict[str, Path] = field(default_factory=dict)
    dynamic_skills: dict[str, SkillManifest] = field(default_factory=dict)
    conditional_skills: dict[str, SkillManifest] = field(default_factory=dict)
    activated_names: set[str] = field(default_factory=set)
    skill_sources: dict[str, Path] = field(default_factory=dict)

    def reset(self) -> None:
        self.discovered_dirs.clear()
        self.dynamic_skills.clear()
        self.conditional_skills.clear()
        self.activated_names.clear()
        self.skill_sources.clear()


def _find_containing_root(file_path: Path, root_paths: list[Path]) -> Path | None:
    """Find which root_path contains file_path. Returns the resolved root or None."""
    try:
        resolved = file_path.resolve()
    except Exception:
        return None
    for root in root_paths:
        try:
            resolved.relative_to(root.resolve())
            return root.resolve()
        except ValueError:
            continue
    return None


def _matches_skill_paths(
    file_path: Path,
    root_paths: list[Path],
    patterns: list[str],
) -> bool:
    """Check if a file path matches any pattern using pathspec (gitignore)."""
    containing = _find_containing_root(file_path, root_paths)
    if containing is None:
        return False

    try:
        rel_path = file_path.resolve().relative_to(containing)
    except Exception:
        return False

    rel_str = rel_path.as_posix()
    valid_patterns = [p for p in patterns if p.strip() and p.strip() != "**"]
    if not valid_patterns:
        return False

    normalized: list[str] = []
    for p in valid_patterns:
        if not p.startswith("**/") and "/" not in p and "*" in p:
            normalized.append(f"**/{p}")
        else:
            normalized.append(p)

    try:
        spec = PathSpec.from_lines("gitignore", normalized)
        return spec.match_file(rel_str)
    except Exception:
        logger.error(f"Failed to compile pathspec for patterns {valid_patterns}")
        return False


def _get_merged_gitignore_spec(root_paths: list[Path]) -> PathSpec | None:
    """Load and merge .gitignore rules from all provided root paths."""
    all_lines: list[str] = []
    for root in root_paths:
        p = root.resolve() / ".gitignore"
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    all_lines.extend(f.readlines())
                    all_lines.append("\n")
            except Exception:
                pass
    if not all_lines:
        return None
    try:
        return PathSpec.from_lines("gitignore", all_lines)
    except Exception:
        return None


def discover_skill_dirs_for_paths(
    file_paths: Iterable[str | Path],
    root_paths: list[Path],
    *,
    max_depth: int = 3,
    candidate_dir_names: tuple[str, ...] | None = None,
) -> list[Path]:
    """Walk up directory tree to discover nested skill dirs, scoped to root_paths.

    [关键设计]：严格限制在文件所在根路径内遍历，防止越界。
    candidate_dir_names：显式传入要扫描的子目录名集合；None 时从 SkillSettings
    默认值读取单一契约，消除硬编码复制。
    """
    if not root_paths:
        return []
    if candidate_dir_names is None:
        # walk-up 只做 workspace 内相对目录发现：`.work/skills`（graduation/ClawHub
        # 安装落点）。用户级 assets/skills 是绝对路径，由 layout builder 直接展开，
        # 不参与向上遍历。
        candidate_dir_names = (SkillSettings().workspace_skills_dir,)

    found: dict[str, Path] = {}
    gitignore_spec = _get_merged_gitignore_spec(root_paths)

    for raw_fp in file_paths:
        fp = Path(raw_fp).resolve()
        containing_root = _find_containing_root(fp, root_paths)
        if containing_root is None:
            continue

        cursor = fp.parent if fp.is_file() else fp
        for _ in range(max_depth):
            for candidate_name in candidate_dir_names:
                candidate = (cursor / candidate_name).resolve()
                if not candidate.is_dir():
                    continue
                if gitignore_spec:
                    try:
                        rel = candidate.relative_to(containing_root)
                        if gitignore_spec.match_file(rel.as_posix() + "/"):
                            continue
                    except ValueError:
                        pass
                key = str(candidate)
                if key not in found:
                    found[key] = candidate
            if cursor == containing_root or not cursor.is_relative_to(containing_root):
                break
            parent = cursor.parent
            if parent == cursor:
                break
            cursor = parent

    return sorted(found.values())


def add_skill_directories(
    state: SkillCatalogState,
    dirs: Iterable[Path],
    *,
    settings: SkillSettings | None = None,
) -> list[SkillManifest]:
    """Scan newly-discovered directories and merge their skills into state."""
    if settings is None:
        settings = SkillSettings()
    newly_added: list[SkillManifest] = []
    for d in dirs:
        d_key = str(d.resolve())
        if d_key in state.discovered_dirs:
            continue
        if not d.is_dir():
            continue
        state.discovered_dirs[d_key] = d.resolve()
        skills = _scan_skills_directory_frontmatter_only(
            d,
            max_file_bytes=settings.max_skill_file_bytes,
            max_candidates=settings.max_candidates_per_root,
            max_skills_loaded=settings.max_skills_loaded_per_source,
        )
        for skill in skills:
            skill.loaded_from = str(d.resolve())
            if skill.paths:
                state.conditional_skills[skill.name] = skill
            else:
                if skill.name not in state.dynamic_skills:
                    newly_added.append(skill)
                state.dynamic_skills[skill.name] = skill
    return newly_added


def activate_conditional_skills_for_paths(
    state: SkillCatalogState,
    file_paths: Iterable[str | Path],
    root_paths: list[Path],
) -> list[SkillManifest]:
    """Lightweight in-memory conditional activation via pathspec.

    Pure string matching. No disk I/O. Safe to call after every tool call.
    """
    if not root_paths:
        return []

    files = [Path(fp) for fp in file_paths]
    activated: list[SkillManifest] = []
    to_remove: list[str] = []

    for sname, skill in state.conditional_skills.items():
        if not skill.paths:
            continue
        if sname in state.activated_names:
            continue

        if any(
            _matches_skill_paths(fp, root_paths, skill.paths)
            for fp in files
        ):
            to_remove.append(sname)
            state.dynamic_skills[sname] = skill
            state.activated_names.add(sname)
            activated.append(skill)

    for sname in to_remove:
        del state.conditional_skills[sname]

    return activated


def _parse_skill_with_namespace(path: Path, namespace: str) -> SkillManifest:
    return parse_skill_file(path, fallback_name=namespace or None)


def _parse_skill_frontmatter_with_namespace(path: Path, namespace: str) -> SkillManifest:
    return parse_skill_frontmatter(path, fallback_name=namespace or None)


def _scan_skills_directory(
    root: Path,
    max_file_bytes: int,
    max_candidates: int,
    max_skills_loaded: int,
) -> list[SkillManifest]:
    return _scan_directory_v2(
        root,
        _parse_skill_with_namespace,
        manifest_filename="SKILL.md",
        max_file_bytes=max_file_bytes,
        max_candidates=max_candidates,
        max_loaded=max_skills_loaded,
        max_depth=SKILL_SCAN_MAX_DEPTH,
    )


def _scan_skills_directory_frontmatter_only(
    root: Path,
    max_file_bytes: int,
    max_candidates: int,
    max_skills_loaded: int,
) -> list[SkillManifest]:
    """与 _scan_skills_directory 相同的扫描逻辑，但仅解析 frontmatter。

    v5 渐进式披露：插件加载时只读 frontmatter（body=""），
    body 在 SkillRuntime._ensure_body_loaded() 按需加载。
    """
    return _scan_directory_v2(
        root,
        _parse_skill_frontmatter_with_namespace,
        manifest_filename="SKILL.md",
        max_file_bytes=max_file_bytes,
        max_candidates=max_candidates,
        max_loaded=max_skills_loaded,
        max_depth=SKILL_SCAN_MAX_DEPTH,
    )


def _coerce_existing_dirs(
    locations: Iterable[CapabilityLocation | Path | str] | None,
) -> list[Path]:
    """把 location/路径归一化为去重后、真实存在的目录列表。

    接受 :class:`CapabilityLocation`（只取其已判定的 ``path``）或裸路径；
    不存在的目录一律过滤——发现器只负责扫描，定位与 exists 判定已由
    ``build_project_capability_layout`` 在上游完成。
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for item in locations or ():
        candidate = item.path if isinstance(item, CapabilityLocation) else Path(item)
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def discover_skills(
    *,
    project_locations: Iterable[CapabilityLocation | Path] = (),
    plugin_locations: Iterable[CapabilityLocation | Path] = (),
    settings: SkillSettings | None = None,
    dynamic_state: SkillCatalogState | None = None,
) -> list[SkillManifest]:
    """扫描项目级 + 插件级技能目录并合并（纯发现，不做路径定位）。

    目录的解析、root 去重、相对目录展开与 exists 判定统一由
    :func:`prismclaw.infra.paths.build_project_capability_layout` 完成，
    调用方把 ``layout.existing_locations_of(...)`` 传入即可。本函数不再接收
    project_root，也不自行拼接 ``root / asset_skills_dir``。

    合并顺序：project 首见胜出 → plugin 只补空缺（不覆盖同名 project）→
    dynamic_state 动态技能最高优先级。

    带 ``paths`` 条件的技能不直接进入清单，而是挂到
    ``dynamic_state.conditional_skills``，等工具调用触达匹配路径后由
    ``activate_conditional_skills_for_paths`` 激活。没有 dynamic_state 时
    退回旧行为（一律展示），避免调用方拿不到条件技能。
    """
    if settings is None:
        settings = SkillSettings()

    merged: dict[str, SkillManifest] = {}
    project_names: set[str] = set()

    def _stash_conditional(skill: SkillManifest) -> bool:
        """条件式技能入 conditional 池（已激活/已成动态技能的不再重复挂）。"""
        if dynamic_state is None:
            return False
        if skill.name in dynamic_state.activated_names or skill.name in dynamic_state.dynamic_skills:
            return True
        # 覆盖写入而非 setdefault：技能文件可能被编辑，conditional 池必须跟着刷新，
        # 否则首次扫描的旧 frontmatter 会一直存活到进程重启。
        dynamic_state.conditional_skills[skill.name] = skill
        return True

    # Phase B: 项目级技能（首见胜出；frontmatter-only，body 延迟加载）
    for candidate in _coerce_existing_dirs(project_locations):
        skills = _scan_skills_directory_frontmatter_only(
            candidate,
            max_file_bytes=settings.max_skill_file_bytes,
            max_candidates=settings.max_candidates_per_root,
            max_skills_loaded=settings.max_skills_loaded_per_source,
        )
        for skill in skills:
            skill.source = SkillSource.PROJECT
            skill.loaded_from = str(candidate)
            if dynamic_state is not None:
                dynamic_state.skill_sources[skill.name] = candidate
            project_names.add(skill.name)
            if skill.paths and _stash_conditional(skill):
                continue
            if skill.name not in merged:
                merged[skill.name] = skill

    # Phase C: 插件技能（仅补空缺，同名 project 保留用户可见版本）
    for candidate in _coerce_existing_dirs(plugin_locations):
        skills = _scan_skills_directory_frontmatter_only(
            candidate,
            max_file_bytes=settings.max_skill_file_bytes,
            max_candidates=settings.max_candidates_per_root,
            max_skills_loaded=settings.max_skills_loaded_per_source,
        )
        for skill in skills:
            if not skill.source or skill.source is SkillSource.BUNDLED:
                skill.source = SkillSource.PLUGIN
            skill.loaded_from = str(candidate)
            if dynamic_state is not None:
                dynamic_state.skill_sources[skill.name] = candidate
            if skill.name in project_names:
                logger.info(
                    "skill.name_conflict name=%s project=%s plugin_ignored=%s",
                    skill.name,
                    merged[skill.name].file_path if skill.name in merged else "<conditional>",
                    candidate,
                )
                continue
            if skill.paths and _stash_conditional(skill):
                continue
            if skill.name not in merged:
                merged[skill.name] = skill

    # 动态（运行时发现）技能以最高优先级合并
    if dynamic_state is not None:
        for name, skill in dynamic_state.dynamic_skills.items():
            merged[name] = skill

    return sorted(merged.values(), key=lambda s: s.name)


__all__ = [
    "SkillCatalogState",
    "discover_skills",
    "_scan_skills_directory",
    "_scan_skills_directory_frontmatter_only",
    "discover_skill_dirs_for_paths",
    "add_skill_directories",
    "activate_conditional_skills_for_paths",
]
