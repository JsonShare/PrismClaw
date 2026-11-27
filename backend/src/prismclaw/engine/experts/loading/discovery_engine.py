"""专家包发现引擎。

只扫描专家包目录：每个非隐藏子目录命中任一加载策略即视为一个专家包。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from prismclaw.engine.experts import ExpertErrorCode
from prismclaw.engine.experts.loading.load_result import (
    ExpertPackage,
    PackageError,
    PackageLoadResult,
)
from prismclaw.engine.experts.loading.load_strategy import LoadStrategy
from prismclaw.engine.experts.paths import MANIFEST_REL_PATH
from prismclaw.engine.experts.registry.identity import EXPERT_SOURCE, ExpertID

logger = logging.getLogger(__name__)


def _is_legacy_suite(entry: Path) -> bool:
    """旧套件只识别非空 ``experts[]``；其它清单交给正常加载校验。"""
    manifest = entry / MANIFEST_REL_PATH
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("Failed to inspect %s: %s", manifest, exc)
        return False
    experts = data.get("experts") if isinstance(data, dict) else None
    return isinstance(experts, list) and bool(experts)


@dataclass(slots=True)
class ExpertDiscoveryResult:
    """发现结果。"""

    to_activate: list[ExpertID] = field(default_factory=list)
    errors: list[PackageError] = field(default_factory=list)


class ExpertDiscoveryEngine:
    """扫描专家包目录，产出可加载的专家包身份。"""

    def __init__(
        self,
        *,
        directories: Sequence[Path],
        strategies: list[LoadStrategy],
    ) -> None:
        self._directories = list(directories)
        self._strategies = strategies
        self._legacy_suite_warnings: set[Path] = set()

    def discover(self) -> ExpertDiscoveryResult:
        """发现需要激活的专家包。"""
        result = ExpertDiscoveryResult()
        seen: set[ExpertID] = set()
        for root in self._directories:
            for expert_id in self._scan_root(root):
                if expert_id in seen:
                    continue
                seen.add(expert_id)
                result.to_activate.append(expert_id)
        return result

    def _scan_root(self, path: Path) -> list[ExpertID]:
        """每个可加载子目录恰好产生一个专家包；旧套件只告警一次。"""
        if not path.exists() or not path.is_dir():
            return []
        discovered: list[ExpertID] = []
        try:
            for entry in sorted(path.iterdir(), key=lambda p: p.name):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if _is_legacy_suite(entry):
                    if entry not in self._legacy_suite_warnings:
                        self._legacy_suite_warnings.add(entry)
                        logger.warning(
                            "%s: legacy suite %s is not loaded",
                            ExpertErrorCode.LEGACY_SUITE_UNSUPPORTED,
                            entry,
                        )
                    continue
                if any(strategy.can_handle(entry) for strategy in self._strategies):
                    with contextlib.suppress(ValueError):
                        discovered.append(ExpertID(name=entry.name, source=EXPERT_SOURCE))
        except OSError as exc:
            logger.debug("Failed to scan %s: %s", path, exc)
        return discovered

    def resolve_install_path(self, expert_id: ExpertID) -> Path | None:
        """在扫描根中解析同名包目录。"""
        for root in self._directories:
            candidate = root / expert_id.name
            if candidate.is_dir() and not _is_legacy_suite(candidate):
                return candidate
        return None


def _dedupe_loaded_packages(
    packages: Sequence[ExpertPackage], errors: list[PackageError]
) -> list[ExpertPackage]:
    """按专家命名空间去重：同名包保留先加载者，后者记 issue 并跳过。

    命名空间（``expert_name``，缺省为目录名）同时用于 ``tool_owners`` 与
    技能 canonical 前缀，重复会导致后者静默覆盖前者的技能/代理。
    """
    deduped: list[ExpertPackage] = []
    seen: set[str] = set()
    for package in packages:
        key = package.expert_name or package.name
        if key in seen:
            errors.append(
                PackageError(
                    type="duplicate_expert",
                    source=package.source,
                    expert=key,
                    message=f"专家包名 {key!r} 重复；保留先加载者，后者已跳过",
                )
            )
            continue
        seen.add(key)
        deduped.append(package)
    return deduped


class ExpertLoader:
    """并行加载发现的专家包。"""

    def __init__(
        self,
        *,
        discovery: ExpertDiscoveryEngine,
        strategies: list[LoadStrategy],
        data_base_dir: Path | None = None,
    ) -> None:
        self._discovery = discovery
        self._strategies = strategies
        self._data_base_dir = data_base_dir

    async def assemble_expert_load_result(self) -> PackageLoadResult:
        """发现 → 并行逐个加载 → 汇总 ``PackageLoadResult``。"""
        discovery_result = self._discovery.discover()
        expert_ids = discovery_result.to_activate
        results = await asyncio.gather(
            *(self._load_one(expert_id) for expert_id in expert_ids),
            return_exceptions=True,
        )

        enabled: list[ExpertPackage] = []
        disabled: list[ExpertPackage] = []
        errors: list[PackageError] = list(discovery_result.errors)
        for expert_id, result in zip(expert_ids, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(
                    PackageError(
                        type="generic_error",
                        source=expert_id.source,
                        expert=expert_id.name,
                        message=str(result),
                    )
                )
                continue
            loaded, load_errors = result
            errors.extend(load_errors)
            if loaded is None:
                continue
            if loaded.enabled:
                enabled.append(loaded)
            else:
                disabled.append(loaded)

        deduped = _dedupe_loaded_packages([*enabled, *disabled], errors)
        return PackageLoadResult(
            enabled=[package for package in deduped if package.enabled],
            disabled=[package for package in deduped if not package.enabled],
            errors=errors,
        )

    async def _load_one(
        self, expert_id: ExpertID
    ) -> tuple[ExpertPackage | None, list[PackageError]]:
        """加载单个专家包。"""
        install_path = self._discovery.resolve_install_path(expert_id)
        if install_path is None:
            return None, [
                PackageError(
                    type="expert_not_found",
                    source=expert_id.source,
                    expert=expert_id.name,
                    message=f"未找到 {expert_id} 的安装路径",
                )
            ]
        for strategy in self._strategies:
            if strategy.can_handle(install_path):
                return await strategy.load(
                    expert_id,
                    install_path,
                    enabled=True,
                    data_base_dir=self._data_base_dir,
                )
        return None, [
            PackageError(
                type="expert_not_found",
                source=expert_id.source,
                expert=expert_id.name,
                message=f"没有可处理 {install_path} 的加载策略",
            )
        ]
