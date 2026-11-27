"""启动自检的行为。

两组检查的环境适用范围不同，分别验证：

* ``validate_assets`` —— 任何环境都执行（宿主机/容器都必须有静态能力）；
* ``validate_runtime_volume`` —— 仅容器内执行（宿主机允许 runtime 回落）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prismclaw.infra import deployment_checks
from prismclaw.infra.deployment_checks import (
    DeploymentCheckError,
    validate_assets,
    validate_runtime_volume,
)


@pytest.fixture()
def assets(tmp_path: Path) -> Path:
    root = tmp_path / "assets"
    (root / "skills").mkdir(parents=True)
    (root / "experts").mkdir(parents=True)
    (root / "skills" / "demo").mkdir()
    return root


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    return root


def test_assets_checked_on_host_too(tmp_path: Path) -> None:
    """宿主机同样校验 assets：能力全空不是可接受的降级。"""
    with pytest.raises(DeploymentCheckError, match="静态能力目录为空"):
        validate_assets(tmp_path / "missing-assets")


def test_valid_assets_pass(assets: Path) -> None:
    validate_assets(assets)


def test_empty_assets_fails(assets: Path) -> None:
    """skills 与 experts 同时为空 = 漏拷或同步失败，必须中止启动。"""
    for child in (assets / "skills").iterdir():
        child.rmdir()
    with pytest.raises(DeploymentCheckError, match="静态能力目录为空"):
        validate_assets(assets)


def test_only_experts_present_is_allowed(assets: Path) -> None:
    """只有一个非空即可：按域构建的镜像可能没有 skills。"""
    for child in (assets / "skills").iterdir():
        child.rmdir()
    (assets / "experts" / "demo").mkdir()
    validate_assets(assets)


def test_host_mode_runtime_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主机（非容器）不校验 runtime：物理部署允许回落。"""
    monkeypatch.setattr(deployment_checks, "is_container", lambda: False)
    validate_runtime_volume(tmp_path / "missing-runtime")


def test_missing_runtime_volume_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    with pytest.raises(DeploymentCheckError, match="runtime 卷未挂载"):
        validate_runtime_volume(tmp_path / "not-mounted")


def test_unwritable_runtime_fails(runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deployment_checks, "is_container", lambda: True)
    os.chmod(runtime, 0o500)
    try:
        with pytest.raises(DeploymentCheckError, match="runtime 卷不可写"):
            validate_runtime_volume(runtime)
    finally:
        os.chmod(runtime, 0o700)
