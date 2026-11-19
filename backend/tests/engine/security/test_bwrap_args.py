"""bwrap 参数生成：proc 挂载按环境能力降级，其余隔离项恒定。"""

from __future__ import annotations

import shutil as _shutil

import pytest

from prismclaw.engine.security.sandbox import bwrap as bwrap_mod
from prismclaw.schema.config.config import SandboxConfig
from prismclaw.schema.content.workspace import SandboxTier


@pytest.fixture()
def fake_bwrap(tmp_path, monkeypatch):
    binary = tmp_path / "bwrap"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    original = _shutil.which
    monkeypatch.setattr(
        bwrap_mod.shutil,
        "which",
        lambda name, *a, **k: str(binary) if name == "bwrap" else original(name, *a, **k),
    )
    return binary


def _build(monkeypatch, *, proc_supported: bool):
    monkeypatch.setattr(bwrap_mod, "_proc_mount_supported", lambda: proc_supported)
    monkeypatch.setattr(bwrap_mod, "_resolve_runtime_root", lambda: None)
    policy = bwrap_mod.BwrapPolicy(settings=SandboxConfig())
    return policy._build_bwrap_args("/tmp/ws-demo", SandboxTier.STANDARD)


def test_proc_mount_included_when_supported(fake_bwrap, monkeypatch) -> None:
    args = _build(monkeypatch, proc_supported=True)
    assert "--proc" in args
    assert args[args.index("--proc") + 1] == "/proc"


def test_proc_mount_omitted_when_unsupported(fake_bwrap, monkeypatch) -> None:
    """Docker 拒绝 mount proc 时不能带 --proc，否则每条命令都会失败。"""
    args = _build(monkeypatch, proc_supported=False)
    assert "--proc" not in args
    # 其余隔离与挂载不受影响
    assert args[args.index("--bind") + 1] == "/tmp/ws-demo"
    assert "/workspace" in args
    assert "--clearenv" in args
    assert "--unshare-pid" in args


def test_strict_tier_unshares_network(fake_bwrap, monkeypatch) -> None:
    monkeypatch.setattr(bwrap_mod, "_proc_mount_supported", lambda: False)
    monkeypatch.setattr(bwrap_mod, "_resolve_runtime_root", lambda: None)
    policy = bwrap_mod.BwrapPolicy(settings=SandboxConfig())
    args = policy._build_bwrap_args("/tmp/ws-demo", SandboxTier.STRICT)
    assert "--unshare-net" in args
