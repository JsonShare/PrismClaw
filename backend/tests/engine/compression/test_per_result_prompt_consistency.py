"""step 12 守卫：result scope 的 prompt 真相源与模型可见内容一致性。

与 history 的 ``test_prompt_template_consistency.py`` 严格隔离：per-result
是 str→str 的独立链路，其字节基线不得复用 history golden（评审 B4）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from prismclaw.engine.compression._compact_prompt import _strip_md_frontmatter
from prismclaw.engine.compression._result_llm_summary import (
    P2_2_PER_RESULT_PROMPT,
    _load_per_result_prompt,
    run_per_result_fork_agent,
)

# backend/ —— conftest 已把 backend/src 插入 sys.path
_BACKEND = Path(__file__).resolve().parents[3]
_FIXTURES = _BACKEND / "tests/fixtures"
_BODY_GOLDEN = _FIXTURES / "per_result_summary.golden.bin"
_PROMPT_GOLDEN = _FIXTURES / "per_result_summary.prompt.golden.bin"
_MANIFEST = _FIXTURES / "per_result_summary_golden.sha256.json"
_PROMPT_MD = (
    _BACKEND / "src/prismclaw/engine/compression/prompts/per_result_summary.md"
)
_HISTORY_GOLDEN = _FIXTURES / "compact_summary.system.golden.bin"

_TOOL = "probe_tool"
_RAW = "示例工具结果\nexit_code=0\n"


def test_loaded_prompt_has_no_frontmatter():
    """HTML 注释头是运维元数据，不得进入模型可见内容。"""
    loaded = _load_per_result_prompt()
    assert "<!--" not in loaded
    assert "per_result_summary.md" not in loaded
    assert loaded.startswith("你正在总结单条工具结果")


def test_loaded_prompt_matches_inline_fallback():
    """文件存在与文件缺失两条路径必须给出同一套指令。"""
    assert _load_per_result_prompt().strip() == P2_2_PER_RESULT_PROMPT.strip()


def test_result_golden_is_independent_from_history():
    """result scope 的 golden 与 history golden 不得混淆（评审 B4）。"""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["scope"] == "result"
    assert manifest["after"]["bytes"] == len(_BODY_GOLDEN.read_bytes())
    assert _BODY_GOLDEN.read_bytes() != _HISTORY_GOLDEN.read_bytes()


def test_loaded_prompt_matches_result_golden():
    """修复后的模型可见正文逐字节锁定。"""
    assert _load_per_result_prompt().encode("utf-8") == _BODY_GOLDEN.read_bytes()


def test_md_body_matches_result_golden_after_strip():
    """md 剥离注释头后与 golden 一致，防止 md 被改而 golden 未跟进。"""
    body = _strip_md_frontmatter(_PROMPT_MD.read_text(encoding="utf-8"))
    assert body.encode("utf-8") == _BODY_GOLDEN.read_bytes()


def test_model_visible_prompt_matches_golden():
    """spy 捕获 summarizer 实际收到的完整 prompt，对 result golden 逐字节相等。"""
    captured: list[str] = []

    async def spy(prompt):
        captured.append(prompt)
        return "OK"

    deps = SimpleNamespace(summarize_fn=spy)
    assert asyncio.run(
        run_per_result_fork_agent(raw_text=_RAW, tool_name=_TOOL, deps=deps)
    ) == "OK"
    assert len(captured) == 1
    assert captured[0].encode("utf-8") == _PROMPT_GOLDEN.read_bytes()


def test_missing_file_falls_back_to_inline(monkeypatch):
    """文件不可读时仍回退到内联常量，且该常量不含注释头。"""

    def boom(*args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", boom)
    assert _load_per_result_prompt() == P2_2_PER_RESULT_PROMPT
    assert "<!--" not in P2_2_PER_RESULT_PROMPT
