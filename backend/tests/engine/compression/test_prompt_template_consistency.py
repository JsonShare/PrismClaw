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
from prismclaw.engine.compression.hardening import (
    _section_is_substantive,
    carry_forward_missing_sections,
    extract_sections,
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

# ─────────────── step 10: prompt 空段契约 ↔ hardening parser 联动守卫 ───────────────


def test_prompt_empty_section_marker_triggers_carry_forward():
    """prompt 明文规定的空段写法必须被判空，并触发第 7 段结转（W8/W12）。

    这是跨文件契约：compact_summary.md 规定「确无内容时写「（无）」」，
    hardening._section_is_substantive 若只认英文空声明，新摘要的每个章节
    都必出现且写成「（无）」时，carry_forward 的缺段路径与空段路径会被
    同时堵死，prior 摘要的待办任务将静默丢失。测试直接从 md 提取该写法，
    prompt 一旦改写而 parser 未跟进，此处即变红。
    """
    body = _strip_md_frontmatter(_PROMPT_MD.read_text(encoding="utf-8"))
    m = re.search(r"正文写「(.+?)」", body)
    assert m is not None, "prompt 未声明空段写法"
    marker = m.group(1)

    assert _section_is_substantive(f"7. 待办任务\n{marker}") is False

    prior = "1. 主要请求与意图\n- 继续推进\n\n7. 待办任务\n- 调整降糖药剂量\n"
    new = f"1. 主要请求与意图\n- 继续推进\n\n7. 待办任务\n{marker}\n"
    out = carry_forward_missing_sections(new, prior)
    assert "调整降糖药剂量" in out
    assert "[从既有摘要结转" in out


def test_empty_section_detection_does_not_eat_substantive_content():
    """归一化不得误伤：中英文空声明判空，含实质内容的章节仍判实质。"""
    empty_markers = (
        "None", "N/A", "Nothing", "No pending tasks", "- none",
        "（无）", "(无)", "- （无）", "（无）。", "无",
    )
    for marker in empty_markers:
        assert _section_is_substantive(f"7. 待办任务\n{marker}") is False, marker

    substantive_bodies = (
        "- 调整降糖药剂量",
        "无明显异常，仍需复查",
        "（无）。但需复核血常规",
        "7. 待办任务",
    )
    for body in substantive_bodies:
        assert _section_is_substantive(f"7. 待办任务\n{body}") is True, body

    assert _section_is_substantive("7. 待办任务") is False

def test_english_empty_prefix_does_not_swallow_real_content():
    """归一剥离项目符号/括号后，英文空声明短语不得吞掉带实质指令的长句。

    step 10 首次实现沿用旧代码的 startswith("no pending")，归一化剥掉
    "- " 与括号后，"- No pending appeal: refile by Friday" 会被误判为空
    并触发结转，把 prior 的过时待办注入新摘要。最终设计让归一结果只做
    精确词形比对、不参与前缀匹配，此处锁住该边界。
    """
    sentences = (
        "- No pending appeal: refile by Friday",
        "* no current items, however escalate to on-call",
        "（No pending appeal: refile by Friday）",
    )
    for sentence in sentences:
        assert _section_is_substantive(f"7. 待办任务\n{sentence}") is True, sentence

def test_legacy_english_empty_phrases_still_detected():
    """既有英文空声明长写法不得因中文契约修复而丢失判空能力。

    旧实现的 startswith(("no pending", ...)) 覆盖任意长度的纯空声明句。
    若为中文标记改用整句正则并删掉前缀能力，这些写法会被判为实质，
    carry_forward 被跳过，prior 摘要的待办静默丢失（能力回退）。
    """
    phrases = (
        "No pending tasks at this time",
        "No current items to report",
        "no outstanding issues remain open",
        "no active work in progress",
        "no pending tasks were found",
        "no pending tasks and no active items",
        "no pending tasks!",
        "no pending-tasks",
    )
    for phrase in phrases:
        assert _section_is_substantive(f"7. 待办任务\n{phrase}") is False, phrase

def test_summary_tags_do_not_hide_empty_last_section():
    """结构性 <summary> 标签不得并入段落正文，否则最后一个空段被判实质。

    生产链路：LLM 输出 <analysis> + <summary>，strip_analysis_block 只剥
    analysis，compact_result.summary 仍带 <summary>/</summary>。若
    extract_sections 不剔除标签，</summary> 会并进第 11 段，使「（无）」
    被判为 substantive，W12 作废条目的结转被跳过。
    """
    prior = (
        "<summary>\n"
        "10. 用户陈述的事实与约束\n- 通知时间: 周六14:00\n\n"
        "11. 已失效/作废的需求\n- 二甲双胍 (voided)\n"
        "</summary>"
    )
    new = prior.replace("- 二甲双胍 (voided)", "（无）")
    out = carry_forward_missing_sections(new, prior)
    assert "二甲双胍" in out
    assert "[从既有摘要结转" in out

def test_summary_literals_in_body_are_preserved():
    """只有最外层结构标签可剥离；正文里同形字面量必须逐字保留。

    全局替换会删掉用户原话/标识符中的 <summary> 字面量（违反第 6/10 段
    逐字保留契约），甚至把只剩一个字面量的非空段误判为空，从而错误注入
    prior 的过时待办。
    """
    text = (
        "<summary>\n"
        "1. 主要请求与意图\n- 请使用 <summary> 与 </summary> 包裹输出\n\n"
        "6. 用户关键原话\n- 用户说：输出必须用 <SUMMARY> 和 </ Summary > 包裹\n\n"
        "7. 待办任务\n- <summary>\n"
        "</summary>"
    )
    sections = extract_sections(text)
    assert sorted(sections) == [1, 6, 7]
    assert "<summary>" in sections[1]
    assert "</summary>" in sections[1]
    assert "<SUMMARY>" in sections[6]
    assert "</ Summary >" in sections[6]
    assert "<summary>" in sections[7]
    assert _section_is_substantive(sections[7]) is True
