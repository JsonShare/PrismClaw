"""通道 A（虚拟路径契约）回归测试。

锁定三条验收不变量：
  1. 字面 shell 变量（``$PRISMCLAW_OUTPUT_DIR/x``）进文件工具必须报错，不能
     生成名为 ``$...`` 的目录；
  2. 容器挂载点前缀泄漏（``/workspace/...``）按设计剥离；
  3. 裸路径读兜底可命中 workspace 根下的真实项目文件，写命中该项目文件时必须
     报错并指引 ``project/`` / ``outputs/``。
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


def test_bare_read_falls_back_to_workspace_root_file(tmp_path: Path):
    session_root = tmp_path / "sessions" / "s1"
    session_root.mkdir(parents=True)
    project_file = tmp_path / "brief.md"
    project_file.write_text("v1", encoding="utf-8")

    resolved = _resolver().resolve_within(
        tmp_path, "brief.md", session_root=session_root
    )

    assert resolved == project_file.resolve()


def test_bare_write_hitting_project_file_requires_explicit_prefix(tmp_path: Path):
    session_root = tmp_path / "sessions" / "s1"
    session_root.mkdir(parents=True)
    project_file = tmp_path / "brief.md"
    project_file.write_text("v1", encoding="utf-8")

    with pytest.raises(WorkspaceBoundaryError, match="project/"):
        _resolver().resolve_within(
            tmp_path, "brief.md", session_root=session_root, for_write=True
        )


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
    # 无 session 上下文：读允许
    assert r.resolve_within(tmp_path, "sessions/s2/outputs/o.docx") == other
