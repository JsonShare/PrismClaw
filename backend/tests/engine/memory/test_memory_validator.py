"""SDD A8: memory validator 单元 + 集成验证方案.

覆盖范围：
  - parse_validation / build_validation_prompt 解析正确性
  - extractor + validator 管线在主语归属错误场景上的拦截能力
  - store_validated_chat_facts 的 confidence 覆盖 + rejected 丢弃
  - validation_events 写入（ledger 正常 / ledger=None / 写入异常）

运行：conda run -n pyclaw python -m pytest tests/engine/memory/test_memory_validator.py -v
"""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismclaw.engine.memory.capture import (
    AUTO_EXTRACTION_CONFIDENCE,
    build_extraction_prompt,
    build_validation_prompt,
    parse_facts,
    parse_validation,
    store_validated_chat_facts,
    validate_facts,
)
from prismclaw.schema.memory.memory_types import MemoryEntry


# ═══════════════════════════════════════════════════════════════════
# Part 1: parse_validation 单元测试
# ═══════════════════════════════════════════════════════════════════


class TestParseValidation:
    """Validator LLM JSON 输出 → 结构化 per-candidate 结果。"""

    def test_golden_path_all_pass(self):
        raw = json.dumps([
            {"index": 0, "validation_label": "pass", "confidence": 0.95, "reason": "用户原话明确确认"},
            {"index": 1, "validation_label": "pass", "confidence": 0.8, "reason": "有对话证据"},
        ])
        result = parse_validation(raw, 2)
        assert len(result) == 2
        assert result[0]["validation_label"] == "pass"
        assert result[0]["confidence"] == 0.95
        assert result[1]["validation_label"] == "pass"
        assert result[1]["confidence"] == 0.8

    def test_golden_path_mixed(self):
        raw = json.dumps([
            {"index": 0, "validation_label": "rejected", "confidence": 0.0, "reason": "主语归属错误"},
            {"index": 1, "validation_label": "flagged", "confidence": 0.35, "reason": "证据来自 assistant 转述"},
            {"index": 2, "validation_label": "pass", "confidence": 0.9, "reason": "用户明确说"},
        ])
        result = parse_validation(raw, 3)
        assert result[0]["validation_label"] == "rejected"
        assert result[1]["validation_label"] == "flagged"
        assert result[2]["validation_label"] == "pass"

    def test_unparseable_output_defaults_all_rejected(self):
        """Fail-safe：validator 输出不可解析 → 全部 rejected（宁可丢也不脏）。"""
        result = parse_validation("garbage output no json", 3)
        assert len(result) == 3
        assert all(r["validation_label"] == "rejected" for r in result)

    def test_empty_output_defaults_all_rejected(self):
        result = parse_validation("", 2)
        assert len(result) == 2
        assert all(r["validation_label"] == "rejected" for r in result)

    def test_missing_index_defaults_to_rejected(self):
        """Validator 只审查了部分 candidate → 没审查到的默认 rejected。"""
        raw = json.dumps([
            {"index": 0, "validation_label": "pass", "confidence": 0.9, "reason": "ok"},
        ])
        result = parse_validation(raw, 3)
        assert result[0]["validation_label"] == "pass"
        assert result[1]["validation_label"] == "rejected"
        assert result[2]["validation_label"] == "rejected"

    def test_out_of_range_index_skipped(self):
        raw = json.dumps([
            {"index": 99, "validation_label": "pass", "confidence": 0.9},
            {"index": -1, "validation_label": "pass", "confidence": 0.9},
        ])
        result = parse_validation(raw, 2)
        assert all(r["validation_label"] == "rejected" for r in result)

    def test_invalid_label_defaults_rejected(self):
        raw = json.dumps([
            {"index": 0, "validation_label": "maybe", "confidence": 0.5},
        ])
        result = parse_validation(raw, 1)
        assert result[0]["validation_label"] == "rejected"

    def test_confidence_bounds_clamped(self):
        raw = json.dumps([
            {"index": 0, "validation_label": "pass", "confidence": 2.0},
            {"index": 1, "validation_label": "flagged", "confidence": -0.5},
        ])
        result = parse_validation(raw, 2)
        assert result[0]["confidence"] == 1.0
        assert result[1]["confidence"] == 0.0

    def test_confidence_none_uses_label_default(self):
        raw = json.dumps([
            {"index": 0, "validation_label": "pass"},
            {"index": 1, "validation_label": "flagged"},
        ])
        result = parse_validation(raw, 2)
        assert result[0]["confidence"] == 1.0   # pass 默认 1.0
        assert result[1]["confidence"] == 0.4   # flagged 默认 0.4


# ═══════════════════════════════════════════════════════════════════
# Part 2: build_validation_prompt — Never Infer 规则必须在 prompt 里
# ═══════════════════════════════════════════════════════════════════


class TestBuildValidationPrompt:
    def test_contains_sensitive_attributes(self):
        turns = [{"user": "帮我查一下糖尿病", "assistant": "糖尿病是..."}]
        candidates = [{"fact": "用户患有糖尿病", "type": "fact", "time_scope": "permanent"}]
        prompt = build_validation_prompt(turns, candidates)
        # Never Infer 敏感属性名单必须出现在 prompt 里
        assert "健康状态" in prompt
        assert "法律状态" in prompt
        # 候选条目必须带 index，validator 靠 index 对号入座
        assert "[0]" in prompt
        assert "用户患有糖尿病" in prompt

    def test_build_extraction_prompt_also_has_never_infer(self):
        """Extractor prompt 也必须有 Never Infer（双重保险）。"""
        turns = [{"user": "帮我查一下我母亲的糖尿病", "assistant": "..."}]
        prompt = build_extraction_prompt(turns)
        assert "Never Infer" in prompt
        assert "敏感属性" in prompt


# ═══════════════════════════════════════════════════════════════════
# Part 3: store_validated_chat_facts — 接受/拒绝/标记 三路分流
# ═══════════════════════════════════════════════════════════════════


class TestStoreValidatedChatFacts:
    @pytest.fixture
    def mock_store(self):
        return AsyncMock()

    @pytest.fixture
    def facts(self):
        return [
            {"fact": "用户关注糖尿病用药", "type": "preference", "time_scope": "permanent"},
            {"fact": "用户患有糖尿病", "type": "fact", "time_scope": "permanent"},  # 主语归属错误
            {"fact": "项目技术栈是 Python 3.12", "type": "fact", "time_scope": "permanent"},
        ]

    async def test_rejected_not_stored(self, mock_store, facts):
        validation = [
            {"validation_label": "flagged", "confidence": 0.5, "reason": "ok"},
            {"validation_label": "rejected", "confidence": 0.0, "reason": "主语归属错误：用户只是询问"},
            {"validation_label": "pass", "confidence": 0.95, "reason": "用户原话确认"},
        ]
        stored = await store_validated_chat_facts(
            mock_store,
            session_key="sk_test",
            session_id="sid_test",
            facts=facts,
            validation_results=validation,
        )
        assert stored == 2  # flagged + pass，rejected 被丢
        assert mock_store.store.await_count == 2
        # 验证 rejected 的那条（index 1）没被 store
        stored_contents = [
            call.args[1].content
            for call in mock_store.store.await_args_list
        ]
        assert "用户患有糖尿病" not in stored_contents

    async def test_confidence_overrides_default(self, mock_store, facts):
        """validator 给的 confidence 必须覆盖 AUTO_EXTRACTION_CONFIDENCE (0.5)。"""
        validation = [
            {"validation_label": "pass", "confidence": 0.95, "reason": "ok"},
            {"validation_label": "pass", "confidence": 0.7, "reason": "ok"},
            {"validation_label": "pass", "confidence": 1.0, "reason": "ok"},
        ]
        await store_validated_chat_facts(
            mock_store, session_key="sk", session_id="sid",
            facts=facts, validation_results=validation,
        )
        assert mock_store.store.await_count == 3
        confidences = [
            call.args[1].confidence
            for call in mock_store.store.await_args_list
        ]
        assert confidences == [0.95, 0.7, 1.0]

    async def test_origin_stage_reflects_label(self, mock_store, facts):
        validation = [
            {"validation_label": "pass", "confidence": 0.9, "reason": "ok"},
            {"validation_label": "flagged", "confidence": 0.4, "reason": "ok"},
            {"validation_label": "rejected", "confidence": 0.0, "reason": "ok"},
        ]
        await store_validated_chat_facts(
            mock_store, session_key="sk", session_id="sid",
            facts=facts, validation_results=validation,
        )
        origins = [
            call.args[1].origin_stage
            for call in mock_store.store.await_args_list
        ]
        assert origins == ["auto_validated", "auto_flagged"]

    async def test_validation_events_written_for_all_outcomes(self, mock_store, facts):
        """pass + flagged + rejected 三条路都写 validation_events。"""
        ledger = AsyncMock()
        validation = [
            {"validation_label": "pass", "confidence": 0.9, "reason": "ok"},
            {"validation_label": "flagged", "confidence": 0.4, "reason": "ok"},
            {"validation_label": "rejected", "confidence": 0.0, "reason": "ok"},
        ]
        await store_validated_chat_facts(
            mock_store, session_key="sk", session_id="sid",
            facts=facts, validation_results=validation,
            validation_ledger=ledger,
        )
        assert ledger.record_validation.await_count == 3
        labels = [
            call.kwargs["validation_label"]
            for call in ledger.record_validation.await_args_list
        ]
        assert labels == ["pass", "flagged", "rejected"]

    async def test_ledger_none_doesnt_crash(self, mock_store, facts):
        """validation_ledger=None 时不写 events，但也不报错。"""
        validation = [
            {"validation_label": "pass", "confidence": 0.9, "reason": "ok"},
        ]
        # Should not raise
        await store_validated_chat_facts(
            mock_store, session_key="sk", session_id="sid",
            facts=facts, validation_results=validation,
            validation_ledger=None,
        )
        assert mock_store.store.await_count == 1

    async def test_length_mismatch_safety(self, mock_store, facts):
        """facts 和 validation_results 长度不匹配 → 缺失条目默认 rejected。"""
        validation = [
            {"validation_label": "pass", "confidence": 0.9, "reason": "ok"},
            # 只有 2 条，facts 有 3 条
        ]
        await store_validated_chat_facts(
            mock_store, session_key="sk", session_id="sid",
            facts=facts, validation_results=validation,
        )
        # 第 3 条默认 rejected → 只存了 1 条
        assert mock_store.store.await_count == 1


# ═══════════════════════════════════════════════════════════════════
# Part 4: validate_facts — LLM 调用层的 fail-safe
# ═══════════════════════════════════════════════════════════════════


class TestValidateFactsFailSafe:
    async def test_llm_none_returns_flagged_low_confidence(self):
        """没 LLM 时跳过 validator，但 confidence 保守设 0.4。"""
        facts = [{"fact": "test", "type": "fact", "time_scope": "permanent"}]
        result = await validate_facts(None, [], facts)
        assert len(result) == 1
        assert result[0]["validation_label"] == "flagged"
        assert result[0]["confidence"] == 0.4

    async def test_llm_exception_returns_all_rejected(self):
        """LLM 调用异常 → 全部 rejected（fail-safe：宁可丢也不脏）。"""
        mock_llm = AsyncMock()
        mock_llm.complete.side_effect = RuntimeError("LLM 挂了")
        facts = [
            {"fact": "a", "type": "fact", "time_scope": "permanent"},
            {"fact": "b", "type": "fact", "time_scope": "permanent"},
        ]
        result = await validate_facts(mock_llm, [], facts)
        assert len(result) == 2
        assert all(r["validation_label"] == "rejected" for r in result)

    async def test_empty_candidates_returns_empty(self):
        result = await validate_facts(AsyncMock(), [], [])
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# Part 5: 主语归属错误 — 场景级集成测试（mock LLM 输出）
# ═══════════════════════════════════════════════════════════════════


class TestSubjectMisattributionScenarios:
    """用 mock LLM 输出模拟 extractor + validator 的完整行为。

    这是我们最关心的场景——用户"询问 X"不应该变成"用户有 X"。
    """

    @pytest.mark.asyncio
    async def test_scenario_medical_inquiry_not_disease(self):
        """用户：帮我查一下糖尿病 → 不能存 fact="用户患有糖尿病"。"""
        turns = [
            {
                "user": "帮我查一下二甲双胍的用药禁忌",
                "assistant": "二甲双胍主要禁忌是肾功能不全...",
            },
        ]
        # Extractor mock（假设它错误地提取了主语归属）
        facts = [
            {"fact": "用户正在使用二甲双胍治疗糖尿病", "type": "fact", "time_scope": "permanent"},
        ]
        # Validator mock（正确识别主语归属错误）
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = MagicMock(
            text=json.dumps([
                {"index": 0, "validation_label": "rejected", "confidence": 0.0,
                 "reason": "主语归属错误：用户只是询问二甲双胍禁忌，没有确认自己在使用或患有糖尿病"},
            ])
        )
        validation = await validate_facts(mock_llm, turns, facts)
        assert validation[0]["validation_label"] == "rejected"
        assert validation[0]["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_scenario_legal_inquiry_not_lawsuit(self):
        """用户：公司法最新修改 → 不能存 fact="用户涉及公司法诉讼"。"""
        turns = [
            {
                "user": "2024 年新公司法对注册资本有什么变化",
                "assistant": "新公司法...",
            },
        ]
        facts = [
            {"fact": "用户正在进行公司注册相关的法律事务", "type": "fact", "time_scope": "permanent"},
        ]
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = MagicMock(
            text=json.dumps([
                {"index": 0, "validation_label": "rejected", "confidence": 0.0,
                 "reason": "主语归属错误：用户只是询问公司法修改，不等于自己有法律事务"},
            ])
        )
        validation = await validate_facts(mock_llm, turns, facts)
        assert validation[0]["validation_label"] == "rejected"

    @pytest.mark.asyncio
    async def test_scenario_user_explicit_disease_is_valid(self):
        """反例：用户明确说"我有糖尿病" → 可以存 fact，confidence 高。"""
        turns = [
            {
                "user": "我有二型糖尿病，帮我查一下 GLP-1 受体激动剂",
                "assistant": "GLP-1 受体激动剂是...",
            },
        ]
        facts = [
            {"fact": "用户患有二型糖尿病", "type": "fact", "time_scope": "permanent"},
        ]
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = MagicMock(
            text=json.dumps([
                {"index": 0, "validation_label": "pass", "confidence": 0.95,
                 "reason": "用户原话明确确认自己有二型糖尿病"},
            ])
        )
        validation = await validate_facts(mock_llm, turns, facts)
        assert validation[0]["validation_label"] == "pass"
        assert validation[0]["confidence"] >= 0.9

    @pytest.mark.asyncio
    async def test_scenario_friend_inquiry_not_user(self):
        """用户：帮我朋友问糖尿病 → 不能存 fact="用户患有糖尿病"，可以存 fact="用户朋友有糖尿病" 或 preference。"""
        turns = [
            {
                "user": "帮我朋友问一下糖尿病的最新治疗方案",
                "assistant": "糖尿病治疗方案包括...",
            },
        ]
        facts = [
            {"fact": "用户朋友的父亲患有糖尿病", "type": "fact", "time_scope": "permanent"},
            {"fact": "用户关注糖尿病治疗方案", "type": "preference", "time_scope": "permanent"},
        ]
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = MagicMock(
            text=json.dumps([
                {"index": 0, "validation_label": "flagged", "confidence": 0.4,
                 "reason": "不是用户本人的健康信息，证据来自转述"},
                {"index": 1, "validation_label": "pass", "confidence": 0.9,
                 "reason": "用户明确在帮朋友询问糖尿病，说明关心这个主题"},
            ])
        )
        validation = await validate_facts(mock_llm, turns, facts)
        assert validation[0]["validation_label"] == "flagged"
        assert validation[0]["confidence"] == 0.4
        assert validation[1]["validation_label"] == "pass"

    @pytest.mark.asyncio
    async def test_scenario_project_fact_without_user_explicit(self):
        """项目背景事实（非敏感属性）即使不是用户原话，也可以 flagged 存。"""
        turns = [
            {
                "user": "我们要做一个医疗 AI agent，用 Python 写",
                "assistant": "好的，那技术栈建议用...",
            },
        ]
        facts = [
            {"fact": "项目技术栈是 Python", "type": "fact", "time_scope": "permanent"},
        ]
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = MagicMock(
            text=json.dumps([
                {"index": 0, "validation_label": "pass", "confidence": 0.9,
                 "reason": "用户原话明确说用 Python"},
            ])
        )
        validation = await validate_facts(mock_llm, turns, facts)
        assert validation[0]["validation_label"] == "pass"
        assert validation[0]["confidence"] == 0.9
