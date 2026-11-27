"""专家包加载策略接口。

直接构建 ``ExpertPackage`` 的结构化字段。
v5 渐进式披露：技能加载时只解析 frontmatter（body=""），
content pipeline 参数暂存到 ``pipeline_context``，
body 按 ``SkillRuntime._ensure_body_loaded()`` 按需加载。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from prismclaw.engine.experts.loading._declarative_load import (
    SkillScanner,
    _load_declarative_expert,
)
from prismclaw.engine.experts.loading.agent_loader import ExpertAgentLoader
from prismclaw.engine.experts.loading.load_result import ExpertPackage, PackageError
from prismclaw.engine.experts.paths import MANIFEST_REL_PATH
from prismclaw.engine.experts.registry.identity import ExpertID

logger = logging.getLogger(__name__)


class LoadStrategy(ABC):
    """专家包加载策略接口。"""

    @abstractmethod
    def can_handle(self, install_path: Path) -> bool:
        """是否能处理该安装路径。"""

    @abstractmethod
    async def load(
        self,
        expert_id: ExpertID,
        install_path: Path,
        *,
        enabled: bool = True,
        data_base_dir: Path | None = None,
    ) -> tuple[ExpertPackage | None, list[PackageError]]:
        """加载专家包，返回 (ExpertPackage | None, errors)。"""


class DeclarativeLoadStrategy(LoadStrategy):
    """加载声明式专家包（plugin.json + markdown）。"""

    def __init__(
        self,
        *,
        agent_loader: ExpertAgentLoader,
        data_base_dir: Path | None = None,
        skill_scanner: SkillScanner | None = None,
        extra_skill_dirs: Sequence[Path] = (),
    ) -> None:
        self._agent_loader = agent_loader
        self._data_base_dir = data_base_dir
        self._skill_scanner = skill_scanner
        self._extra_skill_dirs = tuple(extra_skill_dirs)

    def can_handle(self, install_path: Path) -> bool:
        if not install_path.is_dir():
            return False
        return (install_path / MANIFEST_REL_PATH).exists()

    async def load(
        self,
        expert_id: ExpertID,
        install_path: Path,
        *,
        enabled: bool = True,
        data_base_dir: Path | None = None,
    ) -> tuple[ExpertPackage | None, list[PackageError]]:
        effective_data_base_dir = (
            data_base_dir if data_base_dir is not None else self._data_base_dir
        )
        return await _load_declarative_expert(
            expert_id,
            install_path,
            enabled=enabled,
            agent_loader=self._agent_loader,
            data_base_dir=effective_data_base_dir,
            dialect="declarative",
            skill_scanner=self._skill_scanner,
            extra_skill_dirs=self._extra_skill_dirs,
        )


def default_strategies(
    *,
    agent_loader: ExpertAgentLoader,
    data_base_dir: Path | None = None,
    skill_scanner: SkillScanner | None = None,
    extra_skill_dirs: Sequence[Path] = (),
) -> list[LoadStrategy]:
    return [
        DeclarativeLoadStrategy(
            agent_loader=agent_loader,
            data_base_dir=data_base_dir,
            skill_scanner=skill_scanner,
            extra_skill_dirs=extra_skill_dirs,
        ),
    ]
