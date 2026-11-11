"""present_files 会话交付目录强制校验（交付物展示链路修复）。

背景：科技挖掘任务把交付物写到技能项目目录 rust_inhibitor_mining/outputs/，
present_files 零校验返回"已展示"，但 Web 展示链只认 sessions/<sid>/outputs/，
导致前端无卡片、模型被假成功蒙骗。修复后：工具层强制校验 + 纠正指引。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from prismclaw.engine.toolkit.file.present_files import PresentFilesTool
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.config.config import WorkspaceConfig


def _make_context(tmp_path: Path, session_id: str = "s-abc123") -> ToolContext:
    return ToolContext(workspace_id="default", workspace_path=tmp_path, session_id=session_id)


@pytest.fixture
def resolver() -> WorkspaceResolver:
    return WorkspaceResolver(WorkspaceConfig())


def _run(coro):
    return asyncio.run(coro)


def _call(resolver, tmp_path: Path, files: list[str], session_id: str = "s-abc123"):
    ctx = _make_context(tmp_path, session_id)
    tool = PresentFilesTool(resolver)
    result = _run(tool.execute({"files": files, "_call_id": "c1"}, ctx))
    return result.content[0].text


class TestPresentFilesEnforcement:
    def test_accepts_file_in_session_outputs(self, resolver, tmp_path):
        out_dir = tmp_path / "sessions" / "s-abc123" / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        text = _call(resolver, tmp_path, ["sessions/s-abc123/outputs/report.html"])
        assert "已向用户展示 1 个交付文件" in text

    def test_rejects_file_outside_session_outputs_with_guidance(self, resolver, tmp_path):
        skill_out = tmp_path / "rust_inhibitor_mining" / "outputs"
        skill_out.mkdir(parents=True)
        (skill_out / "report.html").write_text("<html></html>", encoding="utf-8")
        text = _call(resolver, tmp_path, ["rust_inhibitor_mining/outputs/report.html"])
        assert "未展示任何文件" in text
        assert "outputs/" in text  # 虚拟前缀（不再暴露宿主物理路径）
        assert "rust_inhibitor_mining/outputs/report.html" in text  # 指出违规路径
        assert "复制" in text  # 纠正指引

    def test_rejects_missing_file(self, resolver, tmp_path):
        text = _call(resolver, tmp_path, ["sessions/s-abc123/outputs/ghost.html"])
        assert "未展示任何文件" in text
        assert "文件不存在" in text

    def test_mixed_acceptance_reports_rejected(self, resolver, tmp_path):
        out_dir = tmp_path / "sessions" / "s-abc123" / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "ok.html").write_text("x", encoding="utf-8")
        (tmp_path / "elsewhere.html").write_text("x", encoding="utf-8")
        text = _call(resolver, tmp_path, ["sessions/s-abc123/outputs/ok.html", "elsewhere.html"])
        assert "已向用户展示 1 个交付文件" in text
        assert "1 个路径被忽略" in text

    def test_absolute_path_in_session_outputs_accepted(self, resolver, tmp_path):
        out_dir = tmp_path / "sessions" / "s-abc123" / "outputs"
        out_dir.mkdir(parents=True)
        target = out_dir / "abs.html"
        target.write_text("x", encoding="utf-8")
        text = _call(resolver, tmp_path, [str(target)])
        assert "已向用户展示 1 个交付文件" in text

    def test_subagent_session_id_sanitized(self, resolver, tmp_path):
        """subagent sid `parent#sub:uuid` → `parent-sub-uuid`，与其自身会话目录一致。"""
        sid = "parent#sub:uuid"
        out_dir = tmp_path / "sessions" / "parent-sub-uuid" / "outputs"
        out_dir.mkdir(parents=True)
        (out_dir / "r.html").write_text("x", encoding="utf-8")
        text = _call(resolver, tmp_path, ["sessions/parent-sub-uuid/outputs/r.html"], session_id=sid)
        assert "已向用户展示 1 个交付文件" in text

    def test_empty_session_id_rejected(self, resolver, tmp_path):
        text = _call(resolver, tmp_path, ["sessions/x/outputs/a.html"], session_id="")
        assert "未展示任何文件" in text
        assert "缺少会话上下文" in text

    def test_empty_files_list_rejected(self, resolver, tmp_path):
        text = _call(resolver, tmp_path, [])
        assert "没有可展示的文件" in text
