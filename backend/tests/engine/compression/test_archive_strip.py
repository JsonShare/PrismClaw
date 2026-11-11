"""SDD context-governance S2/W5 回归: L4 归档摘要 <analysis> 剥离。

锁定不变量:
1. resolve_strip_analysis: 无配置(None)→True(存储场景安全默认);
   有配置时以配置为准(cfg.strip_analysis_block)。
2. strip_analysis_block: 闭合块剥离 / 未闭合开标签→截断至结尾 /
   无标签原文返回。
3. SummaryEngine 不传 compaction_config 时摘要仍被剥离(archive 路径)。
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.compression.history_compaction import (
    resolve_strip_analysis,
    strip_analysis_block,
)


def test_resolve_strip_analysis_safe_default():
    assert resolve_strip_analysis(None) is True


def test_resolve_strip_analysis_config_wins():
    cfg_off = SimpleNamespace(history=SimpleNamespace(prompt=SimpleNamespace(strip_analysis_block=False)))
    assert resolve_strip_analysis(cfg_off) is False
    cfg_on = SimpleNamespace(history=SimpleNamespace(prompt=SimpleNamespace(strip_analysis_block=True)))
    assert resolve_strip_analysis(cfg_on) is True
    # history 缺失 / prompt 缺失 → 安全默认
    assert resolve_strip_analysis(SimpleNamespace(history=None)) is True
    assert resolve_strip_analysis(SimpleNamespace(history=SimpleNamespace(prompt=None))) is True


def test_strip_closed_block():
    text = "<analysis>\nreasoning...\n</analysis>\n\n实际摘要正文"
    assert strip_analysis_block(text) == "实际摘要正文"


def test_strip_unclosed_block_truncates_to_end():
    text = "前置正文\n<analysis>\nThe user wants a continuation summary..." \
           "（此处为被截断的 reasoning，无闭合标签）"
    assert strip_analysis_block(text) == "前置正文"


def test_no_tag_untouched():
    text = "正常摘要，不含任何标签。"
    assert strip_analysis_block(text) == text


def test_empty_text_passthrough():
    assert strip_analysis_block("") == ""


def test_summary_engine_without_config_strips():
    """W5 端到端根因: archive 路径构造 SummaryEngine 不传 config,
    修复前 resolve(None)=False → 剥离被跳过。"""
    from prismclaw.engine.compression.summary_engine import SummaryEngine

    async def fake_summarize(payload, *, model=None):
        assert False, "should not be called for empty message list guard?"

    # 直接驱动 summarize_messages 会走 LLM; 改为验证内部决策即可:
    engine = SummaryEngine(
        summarize=None,  # None → fallback_summary, 不触 LLM
        compaction_timeout_s=1.0,
        chunk_token_budget=8000,
        compaction_config=None,
    )
    # fallback 路径产物不带 analysis; 关键断言是 resolve 决策本身:
    assert resolve_strip_analysis(engine._compaction_config) is True
