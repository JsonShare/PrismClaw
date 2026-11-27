from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.experts.loading.load_result import PackageLoadResult


async def assemble_expert_load_result(
    *,
    directories: Sequence[Path],
    data_base_dir: Path | None = None,
    extra_skill_dirs: Sequence[Path] = (),
) -> PackageLoadResult:
    from prismclaw.assembly.experts.agent_manifest_loader import DefaultExpertAgentLoader
    from prismclaw.engine.experts.loading.discovery_engine import (
        ExpertDiscoveryEngine,
        ExpertLoader,
    )
    from prismclaw.engine.experts.loading.load_strategy import default_strategies
    from prismclaw.runtime.skills.discovery import _scan_skills_directory_frontmatter_only

    agent_loader = DefaultExpertAgentLoader()
    strategies = default_strategies(
        agent_loader=agent_loader,
        data_base_dir=data_base_dir,
        skill_scanner=_scan_skills_directory_frontmatter_only,
        extra_skill_dirs=extra_skill_dirs,
    )
    discovery = ExpertDiscoveryEngine(directories=directories, strategies=strategies)
    orchestrator = ExpertLoader(
        discovery=discovery,
        strategies=strategies,
        data_base_dir=data_base_dir,
    )
    return await orchestrator.assemble_expert_load_result()
