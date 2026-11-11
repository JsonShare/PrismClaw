from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from prismclaw.infra.settings import SkillSettings
from prismclaw.infra.paths import build_project_capability_layout
from prismclaw.schema.shared import CapabilityKind, CapabilityLocation
from prismclaw.runtime.skills.discovery import (
    SkillCatalogState,
    activate_conditional_skills_for_paths,
    add_skill_directories,
    discover_skill_dirs_for_paths,
    discover_skills,
)
from prismclaw.runtime.skills.eligibility import filter_eligible
from prismclaw.schema.services.skills import SkillManifest
from prismclaw.schema.services.skills import resolve_skill_name

logger = logging.getLogger(__name__)


class DefaultSkillProvider:
    def __init__(
        self,
        settings: SkillSettings | None = None,
        project_root: str | Path | None = None,
        additional_project_roots: list[str | Path] | None = None,
        *,
        project_locations: Iterable[CapabilityLocation | Path] | None = None,
    ) -> None:
        self._settings = settings
        self._project_root = Path(project_root) if project_root is not None else None
        self._additional_project_roots = additional_project_roots
        # 项目级 skills 目录由上游 layout 唯一展开后注入；None 表示在 list_skills
        # 时以 workspace 为根经 builder 现场解析（workspace-only / 裸构造语义）。
        self._project_locations: tuple[CapabilityLocation | Path, ...] | None = (
            tuple(project_locations) if project_locations is not None else None
        )
        self._dynamic_state = SkillCatalogState()
        self._tracked_file_paths: set[str] = set()
        self._workspace_root: Path | None = None
        self._cached_root_paths: list[Path] = []
        self._plugin_skill_dirs: list[Path] = []

    def reset_dynamic_state(self) -> None:
        self._dynamic_state.reset()
        self._tracked_file_paths.clear()
        self._workspace_root = None
        self._cached_root_paths = []

    def set_plugin_skill_dirs(self, dirs: list[Path]) -> None:
        """Configure plugin skill directories for discovery via file system scan."""
        self._plugin_skill_dirs = [d.resolve() for d in dirs]

    def _resolve_project_locations(
        self, workspace_path: str | Path
    ) -> tuple[CapabilityLocation | Path, ...]:
        """返回本次扫描要用的项目级 skills 目录。

        优先用 deps 边界注入的 layout locations；未注入时以 workspace 为根、
        经唯一 builder 现场展开（保持 workspace-as-root 的历史语义）。
        """
        if self._project_locations is not None:
            return self._project_locations
        settings = self._settings or SkillSettings()
        layout = build_project_capability_layout(
            primary=self._project_root or Path(workspace_path),
            additional=self._additional_project_roots,
            skills_dir=settings.workspace_skills_dir,
            extra_skills_dirs=(settings.asset_skills_dir,),
        )
        return layout.existing_locations_of(CapabilityKind.SKILLS)

    def track_file_paths(self, paths: Iterable[str | Path]) -> None:
        for p in paths:
            self._tracked_file_paths.add(str(Path(p).resolve()))

    def drain_tracked_file_paths(self) -> set[str]:
        paths = self._tracked_file_paths.copy()
        self._tracked_file_paths.clear()
        return paths

    def _build_root_paths(self, workspace_path: str | Path) -> list[Path]:
        """Build the complete root paths list for the given workspace."""
        ws = Path(workspace_path).resolve()
        if self._workspace_root == ws and self._cached_root_paths:
            return self._cached_root_paths.copy()

        roots: list[Path] = [ws]
        if self._project_root:
            pr = self._project_root.resolve()
            if pr not in roots:
                roots.append(pr)
        for add in (self._additional_project_roots or []):
            ar = Path(add).resolve()
            if ar not in roots:
                roots.append(ar)

        self._workspace_root = ws
        self._cached_root_paths = roots
        return roots

    def refresh_catalog(self, workspace_path: str) -> list[SkillManifest]:
        return self.list_skills(workspace_path)

    def list_skills(self, workspace_path: str | None = None) -> list[SkillManifest]:
        if workspace_path is None:
            # 复用上一次解析出的 workspace，避免 capability 装配在未传参时
            # 静默拿到空清单（技能"消失"极难排查）。
            workspace_path = str(self._workspace_root) if self._workspace_root else None
        if workspace_path is None:
            logger.warning(
                "skill_provider.list_skills missing workspace_path; returning empty catalog"
            )
            return []

        all_skills = discover_skills(
            project_locations=self._resolve_project_locations(workspace_path),
            plugin_locations=self._plugin_skill_dirs,
            settings=self._settings,
            dynamic_state=self._dynamic_state,
        )

        # 禁用状态属于"工作区"级配置：读 workspace 而不是 project_root，
        # 否则用户在 workspace 里禁用的技能不生效。
        disabled = self._disabled_skills(Path(workspace_path))
        return [
            skill
            for skill in filter_eligible(all_skills)
            if skill.name not in disabled
        ]

    def get(self, name: str, workspace_path: str | None = None) -> SkillManifest | None:
        skills = self.list_skills(workspace_path)
        resolved = resolve_skill_name(name, skills)
        if resolved is None:
            return None
        for skill in skills:
            if skill.name == resolved:
                return skill
        return None

    @staticmethod
    def _normalize_file_paths(
        file_paths: Iterable[str | Path],
        workspace_path: str | Path,
    ) -> list[str]:
        """把工具调用里的路径归一化为绝对路径。

        LLM 给出的路径通常是相对 workspace 的（如 ``src/App.tsx``），
        而 discovery 侧用 ``Path.resolve()`` 解析，相对路径会落到进程 CWD，
        导致 paths 匹配与 skill 目录发现全部落空。这里统一按 workspace 兜底。
        """
        ws = Path(workspace_path).resolve()
        normalized: list[str] = []
        for raw in file_paths:
            p = Path(raw).expanduser()
            normalized.append(str(p if p.is_absolute() else (ws / p)))
        return normalized

    def refresh_lightweight(
        self,
        file_paths: Iterable[str | Path] | None = None,
        workspace_path: str | None = None,
    ) -> list[SkillManifest]:
        """Layer 1: In-memory conditional skill activation (per tool call).

        Pure string matching via pathspec. No disk I/O.
        Safe to call after every tool execution.
        """
        if workspace_path is None:
            return []

        root_paths = self._build_root_paths(workspace_path)

        if file_paths is None:
            file_paths = self.drain_tracked_file_paths()
        else:
            file_paths = list(file_paths)

        if not file_paths:
            return []

        return activate_conditional_skills_for_paths(
            self._dynamic_state,
            self._normalize_file_paths(file_paths, workspace_path),
            root_paths,
        )

    def refresh_heavyweight(
        self,
        workspace_path: str | None = None,
        *,
        file_paths: Iterable[str | Path] | None = None,
    ) -> list[SkillManifest]:
        """Layer 2: Directory scan + conditional activation (per agent loop cycle).

        Involves disk I/O (directory traversal, YAML parsing).
        Should only be called once per agent loop cycle.

        ``file_paths`` 显式传入本轮触达的文件（agent loop 走这条），
        未传时退回 drain 内部追踪集合。
        """
        if workspace_path is None:
            return []

        root_paths = self._build_root_paths(workspace_path)
        newly: list[SkillManifest] = []

        tracked = (
            self._normalize_file_paths(file_paths, workspace_path)
            if file_paths is not None
            else self.drain_tracked_file_paths()
        )
        if tracked:
            new_dirs = discover_skill_dirs_for_paths(tracked, root_paths)
            if new_dirs:
                newly.extend(
                    add_skill_directories(
                        self._dynamic_state,
                        new_dirs,
                        settings=self._settings,
                    )
                )

            newly.extend(
                activate_conditional_skills_for_paths(
                    self._dynamic_state,
                    tracked,
                    root_paths,
                )
            )

        return newly

    @staticmethod
    def _disabled_skills(workspace_root: Path) -> set[str]:
        state_path = workspace_root / ".work" / "skills_state.json"
        if not state_path.is_file():
            return set()
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        disabled = payload.get("disabled") if isinstance(payload, dict) else None
        if not isinstance(disabled, list):
            return set()
        return {
            item.strip()
            for item in disabled
            if isinstance(item, str) and item.strip()
        }
