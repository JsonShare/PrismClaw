"""容器启动自检的行为。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prismclaw.infra import deployment_checks
from prismclaw.infra.deployment_checks import (
    ContainerCheckError,
    validate_container_layout,
)


@pytest.fixture()
def assets(tmp_path: Path) -> Path:
    root = tmp_path / "assets"
    (root / "skills").mkdir(parents=True)
    (root / "plugins").mkdir(parents=True)
    (root / "skills" / "demo").mkdir()
    return root


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    return root


def test_host_mode_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主机（非容器）不做任何校验：物理部署允许 runtime 回落。"""
    monkeypatch.setattr(deployment_checks, "is_container", lambda: False)
    validate_container_layout(
        assets_root=tmp_path / "missing-assets",
        runtime_root=tmp_path / "missing-runtime",
    )


def test_valid_layout_passes(
    assets: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    validate_container_layout(assets_root=assets, runtime_root=runtime)


def test_empty_assets_fails(
    assets: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skills 与 plugins 同时为空 = 镜像漏拷或同步失败，必须中止启动。"""
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    for child in (assets / "skills").iterdir():
        child.rmdir()
    with pytest.raises(ContainerCheckError, match="静态能力目录为空"):
        validate_container_layout(assets_root=assets, runtime_root=runtime)


def test_only_plugins_present_is_allowed(
    assets: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有一个非空即可：按域构建的镜像可能没有 skills。"""
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    (assets / "plugins" / "demo").mkdir()
    validate_container_layout(assets_root=assets, runtime_root=runtime)


def test_missing_runtime_volume_fails(
    assets: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    with pytest.raises(ContainerCheckError, match="runtime 卷未挂载"):
        validate_container_layout(
            assets_root=assets, runtime_root=tmp_path / "not-mounted"
        )


def test_unwritable_runtime_fails(
    assets: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    os.chmod(runtime, 0o500)
    try:
        with pytest.raises(ContainerCheckError, match="runtime 卷不可写"):
            validate_container_layout(assets_root=assets, runtime_root=runtime)
    finally:
        os.chmod(runtime, 0o700)
