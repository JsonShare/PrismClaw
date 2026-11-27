from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from prismclaw.engine.experts import ExpertErrorCode
from prismclaw.engine.experts.loading.data_dir import get_expert_data_dir
from prismclaw.engine.experts.loading.discovery_engine import (
    ExpertDiscoveryEngine,
    ExpertLoader,
)
from prismclaw.engine.experts.loading.load_result import ExpertPackage, PackageError
from prismclaw.engine.experts.loading.load_strategy import DeclarativeLoadStrategy
from prismclaw.engine.experts.paths import MANIFEST_REL_PATH
from prismclaw.engine.experts.registry.identity import EXPERT_SOURCE, ExpertID


class _FakeStrategy:
    def __init__(self) -> None:
        self.load_calls: list[str] = []

    def can_handle(self, install_path: Path) -> bool:
        return (install_path / MANIFEST_REL_PATH).is_file()

    async def load(
        self,
        expert_id: ExpertID,
        install_path: Path,
        *,
        enabled: bool = True,
        data_base_dir: Path | None = None,
    ) -> tuple[ExpertPackage | None, list[PackageError]]:
        self.load_calls.append(expert_id.canonical)
        return (
            ExpertPackage(
                name=expert_id.name,
                source=expert_id.source,
                path=str(install_path),
                enabled=enabled,
                expert_name=expert_id.name,
            ),
            [],
        )


def _package(root: Path, name: str, *, manifest: dict[str, Any] | None = None) -> Path:
    path = root / name
    path.mkdir(parents=True)
    manifest_path = path / MANIFEST_REL_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest or {"name": name, "version": "1.0.0"}),
        encoding="utf-8",
    )
    return path


def _engine(
    directories: list[Path],
    strategy: _FakeStrategy,
) -> ExpertDiscoveryEngine:
    return ExpertDiscoveryEngine(
        directories=directories,
        strategies=[strategy],  # type: ignore[list-item]
    )


def test_discovery_deduplicates_same_name_across_expert_roots(tmp_path: Path) -> None:
    first = tmp_path / "experts" / "experts"
    second = tmp_path / "experts" / "experts-extra"
    _package(first, "shared")
    _package(second, "shared")
    strategy = _FakeStrategy()
    engine = _engine([first, second], strategy)

    result = engine.discover()

    assert [expert.canonical for expert in result.to_activate] == ["shared@experts"]


def test_discovery_returns_sorted_flat_packages(tmp_path: Path) -> None:
    experts = tmp_path / "experts" / "experts"
    _package(experts, "expert-b")
    _package(experts, "expert-a")
    _package(experts, "ignored", manifest={"name": "ignored", "version": "1.0.0"})
    (experts / "ignored" / MANIFEST_REL_PATH).unlink()
    strategy = _FakeStrategy()
    engine = _engine([experts], strategy)

    result = engine.discover()

    assert [expert.canonical for expert in result.to_activate] == [
        "expert-a@experts",
        "expert-b@experts",
    ]


@pytest.mark.asyncio
async def test_each_flat_package_loads_exactly_once(tmp_path: Path) -> None:
    experts = tmp_path / "experts" / "experts"
    _package(experts, "expert-a")
    _package(experts, "expert-b")
    _package(experts, "expert-c")
    strategy = _FakeStrategy()
    discovery = _engine([experts], strategy)
    orchestrator = ExpertLoader(
        discovery=discovery,
        strategies=[strategy],  # type: ignore[list-item]
    )

    result = await orchestrator.assemble_expert_load_result()

    assert sorted(strategy.load_calls) == [
        "expert-a@experts",
        "expert-b@experts",
        "expert-c@experts",
    ]
    assert {expert.expert_id for expert in result.enabled} == {
        "expert-a@experts",
        "expert-b@experts",
        "expert-c@experts",
    }


@pytest.mark.asyncio
async def test_duplicate_name_across_roots_loads_once(tmp_path: Path) -> None:
    first = tmp_path / "experts" / "experts"
    second = tmp_path / "experts" / "experts-extra"
    _package(first, "shared")
    _package(second, "shared")
    strategy = _FakeStrategy()
    orchestrator = ExpertLoader(
        discovery=_engine([first, second], strategy),
        strategies=[strategy],  # type: ignore[list-item]
    )

    result = await orchestrator.assemble_expert_load_result()

    assert strategy.load_calls == ["shared@experts"]
    assert [expert.expert_id for expert in result.enabled] == ["shared@experts"]


def test_legacy_suite_is_skipped_and_warned_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    experts = tmp_path / "experts" / "experts"
    suite = _package(experts, "legacy-suite", manifest={"name": "legacy-suite", "experts": ["x"]})
    _package(experts, "flat-package")
    strategy = _FakeStrategy()
    engine = _engine([experts], strategy)

    with caplog.at_level(logging.WARNING):
        first = engine.discover()
        second = engine.discover()

    assert [expert.canonical for expert in first.to_activate] == ["flat-package@experts"]
    assert [expert.canonical for expert in second.to_activate] == ["flat-package@experts"]
    warnings = [
        record
        for record in caplog.records
        if ExpertErrorCode.LEGACY_SUITE_UNSUPPORTED in record.getMessage()
    ]
    assert len(warnings) == 1
    assert str(suite) in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_manifest_data_placeholder_is_manifest_invalid(tmp_path: Path) -> None:
    package = _package(
        tmp_path / "experts",
        "bad-package",
        manifest={
            "name": "bad-package",
            "version": "1.0.0",
            "description": "${PRISMCLAW_MARKETPLACE_DATA}/profile.md",
        },
    )
    strategy = DeclarativeLoadStrategy(agent_loader=object())  # type: ignore[arg-type]

    loaded, errors = await strategy.load(
        ExpertID(name="bad-package", source=EXPERT_SOURCE), package
    )

    assert loaded is None
    assert len(errors) == 1
    assert errors[0].type == ExpertErrorCode.MANIFEST_INVALID.value


def test_duplicate_expert_namespace_is_reported_and_skipped() -> None:
    from prismclaw.engine.experts.loading.discovery_engine import (
        _dedupe_loaded_packages,
    )

    first = ExpertPackage(
        name="dir-a", source=EXPERT_SOURCE, path="/tmp/a", expert_name="dup"
    )
    second = ExpertPackage(
        name="dir-b", source=EXPERT_SOURCE, path="/tmp/b", expert_name="dup"
    )
    errors: list[PackageError] = []

    deduped = _dedupe_loaded_packages([first, second], errors)

    assert deduped == [first]
    assert len(errors) == 1
    assert errors[0].type == "duplicate_expert"
    assert errors[0].expert == "dup"
