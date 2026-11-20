"""通道 A（虚拟路径契约）回归测试。

锁定三条验收不变量：
  1. 字面 shell 变量（``$PRISMCLAW_OUTPUT_DIR/x``）进文件工具必须报错，不能
     生成名为 ``$...`` 的目录；
  2. 容器挂载点前缀泄漏（``/workspace/...``）按设计剥离；
  3. 裸路径写必须显式前缀（``outputs/`` / ``project/``），无兜底猜测。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prismclaw.engine.toolkit.file.workspace import (
    WorkspaceBoundaryError,
    WorkspaceResolver,
)
from prismclaw.schema.config.config import WorkspaceConfig


def _resolver() -> WorkspaceResolver:
    return WorkspaceResolver(WorkspaceConfig())


def test_literal_shell_variable_is_rejected(tmp_path: Path):
    with pytest.raises(WorkspaceBoundaryError, match="shell 变量"):
        _resolver().resolve_within(
            tmp_path, "$PRISMCLAW_OUTPUT_DIR/report.md", for_write=True
        )

    # 且不能留下字面 `$` 目录
    assert not (tmp_path / "$PRISMCLAW_OUTPUT_DIR").exists()


def test_container_workspace_prefix_is_stripped(tmp_path: Path):
    resolved = _resolver().resolve_within(tmp_path, "workspace/notes/a.md")

    assert resolved == (tmp_path / "notes" / "a.md").resolve()


def _mk_session(tmp_path: Path, sid: str = "s1") -> Path:
    session_root = tmp_path / "sessions" / sid
    (session_root / "outputs").mkdir(parents=True)
    return session_root


def test_sessions_prefix_same_session_normalizes_no_double_nesting(tmp_path: Path):
    """`sessions/<sid>/outputs/x`（present_files 报错/系统提示里的物理格式，
    模型照抄）本会话自引用时必须归一，不得拼出
    ``outputs/sessions/<sid>/outputs/x`` 双重嵌套——包括文件尚不存在的写场景。
    """
    sr = _mk_session(tmp_path)
    existing = sr / "outputs" / "report.docx"
    existing.write_text("x", encoding="utf-8")

    r = _resolver()
    assert r.resolve_within(tmp_path, "sessions/s1/outputs/report.docx", session_root=sr) == existing
    # 写新文件：落点必须是 session outputs/ 根下，而非 outputs/sessions/... 嵌套
    assert (
        r.resolve_within(tmp_path, "sessions/s1/outputs/new.docx", session_root=sr, for_write=True)
        == sr / "outputs" / "new.docx"
    )
    # 读不存在文件：报错路径同样不嵌套
    assert (
        r.resolve_within(tmp_path, "sessions/s1/outputs/missing.docx", session_root=sr)
        == sr / "outputs" / "missing.docx"
    )


def test_sessions_prefix_cross_session_is_read_only(tmp_path: Path):
    sr = _mk_session(tmp_path, "s1")
    other = tmp_path / "sessions" / "s2" / "outputs" / "o.docx"
    other.parent.mkdir(parents=True)
    other.write_text("y", encoding="utf-8")

    r = _resolver()
    # 跨会话读：直达 workspace 根下目标会话目录
    assert r.resolve_within(tmp_path, "sessions/s2/outputs/o.docx", session_root=sr) == other
    # 跨会话写：拒绝
    with pytest.raises(WorkspaceBoundaryError, match="禁止写入"):
        r.resolve_within(tmp_path, "sessions/s2/outputs/evil.docx", session_root=sr, for_write=True)


def test_ensure_session_layout_creates_all_standard_subdirs(tmp_path: Path):
    """新会话必须在 run 启动时就建好全部标准子目录，读取方不应看到“不存在”。"""
    from prismclaw.infra.workspace_paths import SESSION_SUBDIRS, ensure_session_layout

    root = ensure_session_layout(tmp_path, "web:admin:default:s:1")

    assert root == tmp_path / "sessions" / "web-admin-default-s-1"
    for sub in SESSION_SUBDIRS:
        assert (root / sub).is_dir(), sub
    # 幂等：重复调用不报错且落点不变
    assert ensure_session_layout(tmp_path, "web:admin:default:s:1") == root
