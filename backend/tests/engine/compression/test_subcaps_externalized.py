"""批 3：上下文子额度配置外置 契约测试。

锁定：
1. subcaps 各字段默认/配置文件值符合计划数值。
2. microcompact 分档表从配置注入后 keep_recent_for 输出一致。
"""
from __future__ import annotations

from prismclaw.engine.context import microcompact as mc
from prismclaw.infra.settings import load_settings


def test_subcaps_defaults():
    settings = load_settings(force_reload=True)
    caps = settings.agent.context.budget.subcaps
    assert caps.runtime_status == 500
    assert caps.l1_snapshot == 500
    assert caps.skill_attachment == 6000
    assert caps.doc_inline_min_chars == 2500
    assert caps.doc_inline_max_chars == 7500
    assert caps.max_constraints == 4

    budget = settings.agent.context.budget
    assert budget.history_floor_min == 4096
    assert budget.history_floor_divisor == 4

    compaction = settings.agent.context.compaction
    assert compaction.ptl_min_keep_tokens == 6400
    assert compaction.reinject_max_files == 5
    assert compaction.microcompact_keep_recent["read"] == 8
    assert compaction.microcompact_keep_recent["grep"] == 4

    assert settings.memory.intent_bias["prefers_tooling_l3"] == 3
    assert settings.memory.intent_bias["follow_up_l2"] == -2


def test_microcompact_tiers_from_config(monkeypatch):
    settings = load_settings(force_reload=True)
    tiers = settings.agent.context.compaction.microcompact_keep_recent
    assert tiers["read"] == 8
    assert mc.keep_recent_for("read") == 8
    assert mc.keep_recent_for("grep") == 4
    assert mc.keep_recent_for("web_search") == 1

    # 注入自定义分档表后输出跟随配置
    monkeypatch.setattr(mc, "_load_keep_recent_tiers", lambda: {"read": 2, "grep": 9})
    assert mc.keep_recent_for("read") == 2
    assert mc.keep_recent_for("grep") == 9
    assert mc.keep_recent_for("ls") == 3  # 未命中时回退默认 3