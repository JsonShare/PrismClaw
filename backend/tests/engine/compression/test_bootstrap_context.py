"""bootstrap 上下文加载 —— 空载不得注入引导语。

历史 bug：固定 header 先 append 再读文件，导致没有任何 AGENTS.md 时
仍返回一段"以下展示代码库"的引导语，既误导又白耗 token。
"""
from __future__ import annotations

import asyncio
from typing import Any

from prismclaw.engine.compression.bootstrap import load_bootstrap_context


class _FakeStore:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    async def get_file(self, workspace_id: str, path: str) -> str | None:
        return self._files.get(path)


def test_returns_empty_when_no_files_loaded() -> None:
    out = asyncio.run(
        load_bootstrap_context("w1", _FakeStore({}), ["AGENTS.md", "/no/such/file.md"])
    )
    assert out == ""


def test_returns_empty_when_file_is_blank() -> None:
    out = asyncio.run(
        load_bootstrap_context("w1", _FakeStore({"AGENTS.md": "   \n"}), ["AGENTS.md"])
    )
    assert out == ""


def test_header_prepended_when_content_exists() -> None:
    out = asyncio.run(
        load_bootstrap_context(
            "w1", _FakeStore({"AGENTS.md": "项目规则 A"}), ["AGENTS.md"]
        )
    )
    assert out.startswith("以下为工作区与用户指令")
    assert "项目规则 A" in out
    assert "AGENTS.md" in out


def test_header_not_duplicated_per_file() -> None:
    out = asyncio.run(
        load_bootstrap_context(
            "w1",
            _FakeStore({"a.md": "A", "b.md": "B"}),
            ["a.md", "b.md"],
        )
    )
    assert out.count("以下为工作区与用户指令") == 1


def test_missing_global_file_does_not_raise() -> None:
    out: Any = asyncio.run(
        load_bootstrap_context("w1", _FakeStore({}), ["/definitely/not/here.md"])
    )
    assert out == ""
