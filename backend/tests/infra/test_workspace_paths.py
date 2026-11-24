"""工作区目录布局（workspace_paths）的单一事实源回归测试。

覆盖两件事：
  * 对话任务（sessions/<sid>）与固定业务域（biz_domain/<domain>/inbox/<record>）
    的路径形态，两者同级但结构不同（见 SDD 5.2.8.1）；
  * 裸路径是确定性解析：读落 `sessions/<sid>/outbox/`，写必须显式前缀。
"""

from __future__ import annotations

import pytest

from prismclaw.engine.toolkit.file.workspace import (
    WorkspaceBoundaryError,
    WorkspaceResolver,
)
from prismclaw.infra.workspace_paths import (
    resolve_biz_domain_dir,
    resolve_session_inbox_dir,
    resolve_workspace_dir,
)
from prismclaw.schema.config.config import WorkspaceConfig


@pytest.fixture
def ws_base(tmp_path, monkeypatch):
    """把 workspaces.default 指向临时目录，隔离真实 HOME。"""
    monkeypatch.setenv("PRISMCLAW_WORKSPACE_BASE", str(tmp_path))
    from prismclaw.infra.settings import load_settings

    settings = load_settings()
    monkeypatch.setattr(settings.workspaces, "default", str(tmp_path), raising=False)
    return tmp_path


def test_biz_domain_dir_is_sibling_of_workspaces(ws_base):
    """业务域挂在 user_root 下，与工作区目录同级、跨工作区共享。"""
    ws = resolve_workspace_dir("alice")
    biz = resolve_biz_domain_dir("alice", "medical")
    assert biz.parent == ws.parent / "biz_domain"
    assert biz.name == "medical"
    # 与工作区目录（default）同级
    assert biz.parent.parent == ws.parent
    # 不属于任何工作区
    assert ws not in biz.parents


def test_biz_domain_rejects_unsafe_domain(ws_base):
    with pytest.raises(ValueError):
        resolve_biz_domain_dir("alice", "../etc")


def test_medical_upload_lands_in_biz_domain_inbox(ws_base):
    from subpackages.medical.persistence.storage import resolve_upload_dir

    target = resolve_upload_dir("alice", "rpt-001")
    assert target == (
        resolve_workspace_dir("alice").parent / "biz_domain" / "medical" / "inbox" / "rpt-001"
    )


def test_session_inbox_stay_under_sessions(ws_base):
    target = resolve_session_inbox_dir("alice", "default", "sid-1")
    assert target == (resolve_workspace_dir("alice") / "sessions" / "sid-1" / "inbox")


@pytest.fixture
def resolver_and_ws(tmp_path):
    ws = tmp_path / "web_alice" / "default"
    (ws / "sessions" / "sid" / "outbox").mkdir(parents=True)
    (ws / "biz_domain" / "medical" / "inbox" / "r1").mkdir(parents=True)
    resolver = WorkspaceResolver(WorkspaceConfig(resolve_path=lambda _wid: ws))
    return resolver, ws


def test_inputs_prefix_is_no_longer_special(resolver_and_ws):
    """`inputs/` 前缀已移除；它现在只是普通裸路径，读落到 session outbox/。"""
    resolver, ws = resolver_and_ws
    session_root = ws / "sessions" / "sid"
    assert (
        resolver.resolve_within(ws, "inputs/a.txt", session_root=session_root)
        == (session_root / "outbox" / "inputs" / "a.txt").resolve()
    )


def test_project_prefix_write_into_biz_domain_is_blocked(resolver_and_ws):
    resolver, ws = resolver_and_ws
    with pytest.raises(WorkspaceBoundaryError, match="biz_domain/"):
        resolver.resolve_within(
            ws,
            "project/biz_domain/medical/inbox/r1/x.txt",
            session_root=ws / "sessions" / "sid",
            for_write=True,
        )


def test_project_prefix_read_of_biz_domain_is_allowed(resolver_and_ws):
    resolver, ws = resolver_and_ws
    resolved = resolver.resolve_within(ws, "project/biz_domain/medical/inbox/r1/x.txt")
    assert resolved == ws / "biz_domain" / "medical" / "inbox" / "r1" / "x.txt"


def test_outbox_prefix_still_maps_to_session_root(resolver_and_ws):
    resolver, ws = resolver_and_ws
    resolved = resolver.resolve_within(
        ws, "outbox/report.md", session_root=ws / "sessions" / "sid", for_write=True
    )
    assert resolved == ws / "sessions" / "sid" / "outbox" / "report.md"
