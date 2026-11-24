"""PR0 守卫测试：压缩摘要 prompt 文本 + 组装机制一致性。

锁定 §2.5 目标全文、单一真相源、payload 逐字节形状，防止后续重构 silently 漂移。
运行时真相源始终是 ``prompts/compact_summary.md``；canonical fixture 只是
"故意的第二份" checked-in golden，只用于逐字节比对。
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.engine.compression._compact_prompt import (
    _resolve_no_tools_preamble_enabled,
    _resolve_prompt_template,
    _strip_md_frontmatter,
)
from prismclaw.engine.compression.summary_engine import SummaryEngine
from prismclaw.schema.config.config import CompactionConfig

# backend/ —— conftest 已把 backend/src 插入 sys.path
_BACKEND = Path(__file__).resolve().parents[3]
_PROMPT_MD = _BACKEND / "src/prismclaw/engine/compression/prompts/compact_summary.md"
_FIXTURES = _BACKEND / "tests/fixtures"
_CANONICAL = _FIXTURES / "compact_summary.canonical.md"
_GOLDEN_SYS = _FIXTURES / "compact_summary.system.golden.bin"
_GOLDEN_USR = _FIXTURES / "compact_summary.user.golden.bin"
_MANIFEST = _FIXTURES / "compact_summary_golden.sha256.json"
_SRC = _BACKEND / "src"

_MARKERS = ("voided", "superseded", "cancelled", "retracted", "replaced")


def test_compact_md_matches_canonical_text():
    """正文须与 §2.5 canonical 逐字相等，且 W8/W12 与 marker 齐全、恰 11 段。"""
    body = _strip_md_frontmatter(_PROMPT_MD.read_text(encoding="utf-8"))
    canonical = _CANONICAL.read_text(encoding="utf-8")
    assert body.strip() == canonical.strip()

    for keyword in ("SDD context-governance W8", "SDD context-governance W12"):
        assert keyword in body
    for marker in _MARKERS:
        assert marker in body

    # 行锚定完整序列：非锚定正则会把 <analysis> 里的编号与叙述句算进来。
    m = re.search(r"^<summary>[ \t]*\r?\n(.*?)^</summary>[ \t]*$", body, re.M | re.S)
    assert m is not None
    numbers = [int(n) for n in re.findall(r"^(\d+)\. ", m.group(1), re.M)]
    assert numbers == list(range(1, 12))


def test_no_in_code_template_duplicate():
    """backend/src 下不得再有 COMPACT_SUMMARY_DEFAULT_TEMPLATE 的代码级定义/导入。"""
    forbidden = "COMPACT_SUMMARY_DEFAULT_TEMPLATE"
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == forbidden:
                offenders.append(f"{path}:{node.lineno}: Name")
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == forbidden for alias in node.names):
                    offenders.append(f"{path}:{node.lineno}: ImportFrom")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == forbidden:
                    offenders.append(f"{path}:{node.lineno}: Def")
    assert offenders == [], f"stale in-code template symbols: {offenders}"


def test_missing_template_fails_loud():
    """模板文件缺失时必须抛 FileNotFoundError，不得静默回退到过期常量文本。"""
    with pytest.raises(FileNotFoundError):
        _resolve_prompt_template(
            "",
            default_path=Path("/nonexistent/x.md"),
            config_label="x",
        )


def test_preamble_reads_schema_field():
    """preamble 开关只读 config.history.preamble_no_tools，缺省安全值为 True。"""
    stub_off = SimpleNamespace(history=SimpleNamespace(preamble_no_tools=False))
    assert _resolve_no_tools_preamble_enabled(stub_off) is False

    real = CompactionConfig()
    real.history.preamble_no_tools = False
    assert _resolve_no_tools_preamble_enabled(real) is False

    stub_on = SimpleNamespace(history=SimpleNamespace(preamble_no_tools=True))
    assert _resolve_no_tools_preamble_enabled(stub_on) is True
    assert _resolve_no_tools_preamble_enabled(None) is True
    assert _resolve_no_tools_preamble_enabled(SimpleNamespace()) is True


def test_payload_shape_unchanged_by_cleanup():
    """最终 system/user 两条的字节须与 Step 2 机制 golden 逐字节相等。"""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    fixed_messages = manifest["fixed_messages"]
    golden_sys = _GOLDEN_SYS.read_bytes()
    golden_usr = _GOLDEN_USR.read_bytes()

    captured: list[list[dict]] = []

    async def spy(payload, *, model=None):
        captured.append(payload)
        return "OK"

    engine = SummaryEngine(
        summarize=spy,
        compaction_timeout_s=30.0,
        chunk_token_budget=100000,
        compaction_config=CompactionConfig(),
    )
    assert asyncio.run(
        engine.summarize_messages(fixed_messages, abort_event=None, model="m")
    ) == "OK"
    assert len(captured) == 1
    payload = captured[0]
    assert [m["role"] for m in payload] == ["system", "user"]
    assert payload[0]["content"].encode("utf-8") == golden_sys
    assert payload[1]["content"].encode("utf-8") == golden_usr


def test_single_typeerror_not_swallowed():
    """summarizer 内部 TypeError 原样上抛；正常路径只调用一次且透传 model。"""
    calls: list[object] = []

    async def boom(payload, *, model=None):
        calls.append((payload, model))
        raise TypeError("provider internal bug")

    engine = SummaryEngine(
        summarize=boom,
        compaction_timeout_s=30.0,
        chunk_token_budget=100000,
        compaction_config=CompactionConfig(),
    )
    with pytest.raises(TypeError, match="provider internal bug"):
        asyncio.run(engine.summarize_messages(
            [{"role": "user", "content": "x"}], abort_event=None, model="m"
        ))
    assert len(calls) == 1  # 无无参重试
    assert calls[0][1] == "m"

    ok_calls: list[object] = []

    async def ok(payload, *, model=None):
        ok_calls.append((payload, model))
        return "OK"

    engine_ok = SummaryEngine(
        summarize=ok,
        compaction_timeout_s=30.0,
        chunk_token_budget=100000,
        compaction_config=CompactionConfig(),
    )
    assert asyncio.run(engine_ok.summarize_messages(
        [{"role": "user", "content": "x"}], abort_event=None, model="m2"
    )) == "OK"
    assert len(ok_calls) == 1
    assert ok_calls[0][1] == "m2"
