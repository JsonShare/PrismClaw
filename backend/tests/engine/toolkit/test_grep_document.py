"""grep 的文档委派：命中返回块定位符，并声明覆盖范围。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from prismclaw.engine.toolkit.file.grep import GrepTool
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver
from prismclaw.schema.agent.data_types import TextBlock


def _make_ctx(tmp_path: Path):
    from prismclaw.engine.runtime.in_process_client import InProcessRuntimeClient
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext

    return ToolContext(
        workspace_id="ws-test",
        workspace_path=tmp_path,
        runtime=InProcessRuntimeClient(),
    )


def _run_grep(pattern: str, path: str, ctx) -> dict:
    tool = GrepTool(WorkspaceResolver(None))
    result = asyncio.run(tool.execute({"pattern": pattern, "path": path, "_call_id": "c1"}, ctx))
    assert not result.is_error, "".join(b.text for b in result.content)
    text = "".join(b.text for b in result.content if isinstance(b, TextBlock))
    return json.loads(text)


def test_grep_on_unparsed_document_reports_empty_coverage(tmp_path: Path) -> None:
    """未解析的文档：0 命中，且 coverage 明确声明 0/1 页。"""
    (tmp_path / "notes.md").write_text("# 标题\n\n超时阈值是 30s\n", encoding="utf-8")
    payload = _run_grep("超时", "notes.md", _make_ctx(tmp_path))

    assert payload["count"] == 0
    assert payload["coverage"]["parsed_pages"] == 0
    assert payload["coverage"]["total_pages"] == 1
    assert "未解析页" in payload["coverage"]["note"]


def test_grep_after_read_returns_locator(tmp_path: Path) -> None:
    """read 之后同一文档的命中带块定位符，不再是行号。"""
    from prismclaw.engine.toolkit.builtin.file_tools import ReadTool

    (tmp_path / "notes.md").write_text("# 标题\n\n超时阈值是 30s\n", encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    asyncio.run(
        ReadTool(WorkspaceResolver(None)).execute({"file_path": "notes.md", "_call_id": "r1"}, ctx)
    )

    payload = _run_grep("超时", "notes.md", ctx)
    assert payload["count"] >= 1
    match = payload["matches"][0]
    assert match["line_number"].startswith("doc:")
    assert "/p1/b" in match["line_number"]
    assert match["path"] == "notes.md"
    assert "30s" in match["line"]
    assert payload["coverage"]["parsed_pages"] == 1


def test_grep_invalid_regex_on_document_is_error(tmp_path: Path) -> None:
    from prismclaw.engine.toolkit.builtin.file_tools import ReadTool

    (tmp_path / "notes.md").write_text("正文\n", encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    asyncio.run(
        ReadTool(WorkspaceResolver(None)).execute({"file_path": "notes.md", "_call_id": "r1"}, ctx)
    )

    tool = GrepTool(WorkspaceResolver(None))
    result = asyncio.run(tool.execute({"pattern": "([", "path": "notes.md", "_call_id": "c1"}, ctx))
    assert result.is_error
    assert "正则无效" in "".join(b.text for b in result.content)


def test_grep_directory_still_uses_runtime(tmp_path: Path) -> None:
    """目录目标不走文档分支：仍由运行时按行检索。"""
    (tmp_path / "a.py").write_text("value = 1\n", encoding="utf-8")
    payload = _run_grep("value", ".", _make_ctx(tmp_path))
    assert payload["count"] >= 1
    assert "line_number" in payload["matches"][0]
    assert "coverage" not in payload
