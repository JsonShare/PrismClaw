"""批 7 收尾闭环测试：G1 附件预算 / G2 降级阶梯 / G3 l1 截断 / G4 埋点 / G5 T5 不落库。"""
from __future__ import annotations

import json
import logging

from prismclaw.engine.compression.budget_orchestrator import (
    DegradationPlan,
    DegradationStep,
    degrade_context,
    log_degradation_steps,
)
from prismclaw.engine.compression.context_zones import resolve_runtime_zones
from prismclaw.engine.execution.request import attachments as attachments_mod
from prismclaw.engine.execution.request.attachments import (
    _compute_attachment_char_budget,
    _compute_ratio_attachment_token_cap,
)
from prismclaw.engine.execution.request.system_prompt import (
    _l1_rough_token_count,
    _truncate_l1_snapshot,
)
from prismclaw.schema.config.config import ContextBudgetConfig, SubcapsSettings


# ---------------------------------------------------------------------------
# G1：附件预算 mode 感知
# ---------------------------------------------------------------------------

def test_g1_ratio_attachment_cap_200k():
    cap = _compute_ratio_attachment_token_cap(170_000, SubcapsSettings())
    assert cap == 24_000
    budget = ContextBudgetConfig(mode="ratio", window_tokens=200_000)
    assert _compute_attachment_char_budget(budget) == 24_000 * 1.5  # 36000


def test_g1_ratio_attachment_cap_32k():
    budget = ContextBudgetConfig(mode="ratio", window_tokens=32_768)
    # 0.25 × input_budget(=27853) = 6963 < 24000 生效，字符 = 6963 × 1.5
    char_budget = _compute_attachment_char_budget(budget)
    assert char_budget == int(round(0.25 * 27_853) * 1.5)
    assert char_budget < 36_000


def test_g1_absolute_zero_drift():
    budget = ContextBudgetConfig()  # absolute 默认，零漂移走老公式
    assert budget.mode == "absolute"
    # 老公式：history=106496 → 106496*0.6=63897 → *1.5=95845
    assert _compute_attachment_char_budget(budget) == 95_845


# ---------------------------------------------------------------------------
# G2：降级阶梯
# ---------------------------------------------------------------------------

def _ratio_zones() -> "object":
    return resolve_runtime_zones(
        budget=ContextBudgetConfig(mode="ratio"),
        window=200_000,
        model_max_output=30_000,
        tools_wire=6588,
        image_tokens=0,
    )


def test_g2_d2_before_d4():
    zones = _ratio_zones()
    plan = degrade_context(
        zones=zones,
        mode="ratio",
        is_code_task=False,
        system_actual=25_000,
        dynamic_actual=0,
        actuals={"subagents": 9_000, "repository_map": 6_000},
    )
    names = [s.step for s in plan.steps]
    assert names[0] == "D2"
    assert "D4" in names
    assert names.index("D2") < names.index("D4")
    d2 = next(s for s in plan.steps if s.step == "D2")
    assert d2.zone == "system"
    assert d2.dropped_tokens == 9_000
    assert d2.cache_invalidated is True
    assert d2.pressure_before > d2.pressure_after
    assert plan.total_dropped_tokens == 15_000
    assert plan.error_code is None


def test_g2_d11_error_when_compaction_disabled():
    zones = _ratio_zones()
    plan = degrade_context(
        zones=zones,
        mode="ratio",
        system_actual=30_000,
        dynamic_actual=0,
        actuals={},
        compaction_disabled=True,
    )
    assert plan.exceeded is True
    assert plan.error_code == "context_window_exceeded"
    assert plan.steps[-1].step == "D11"


def test_g2_d11_force_compaction_without_error():
    zones = _ratio_zones()
    plan = degrade_context(
        zones=zones,
        mode="ratio",
        system_actual=30_000,
        dynamic_actual=0,
        actuals={},
        compaction_disabled=False,
        consecutive_failures=0,
    )
    assert plan.exceeded is False
    assert plan.error_code is None
    assert any(s.step == "D11" and s.note == "force_compaction" for s in plan.steps)


def test_g2_absolute_noop():
    zones = _ratio_zones()
    plan = degrade_context(
        zones=zones,
        mode="absolute",
        system_actual=99_999,
        dynamic_actual=99_999,
    )
    assert plan.steps == ()
    assert plan.total_dropped_tokens == 0
    assert plan.error_code is None


def test_g2_log_fields(caplog):
    caplog.set_level(logging.INFO)
    plan = DegradationPlan(
        steps=(
            DegradationStep(
                step="D2",
                zone="system",
                dropped_tokens=9000,
                pressure_before=2.0,
                pressure_after=1.3,
                cache_invalidated=True,
            ),
        ),
        total_dropped_tokens=9000,
    )
    log_degradation_steps(logger_=logging.getLogger("test_g2"), plan=plan, iteration=3)
    text = caplog.text
    assert "degradation_summary" in text
    assert "degradation_step" in text
    assert "step=D2" in text and "zone=system" in text
    assert "dropped_tokens=9000" in text
    assert "pressure_before=2.000" in text
    assert "pressure_after=1.300" in text
    assert "cache_invalidated=True" in text


# ---------------------------------------------------------------------------
# G3：l1_snapshot token 截断
# ---------------------------------------------------------------------------

def test_g3_l1_snapshot_truncation():
    long_line = "- " + ("记忆片段内容" * 30)  # 每行约 180 字符，超 500 token 限额
    lines = ["<memory_index>"] + [long_line for _ in range(20)] + ["</memory_index>"]
    text = "\n".join(lines)
    assert _l1_rough_token_count(text) > 500
    out = _truncate_l1_snapshot(text, 500)
    assert _l1_rough_token_count(out) <= 500
    assert "另有" in out
    assert "使用记忆检索工具获取" in out


def test_g3_l1_snapshot_short_untouched():
    text = "<memory_index>\n- 一条记忆\n- 另一条记忆\n</memory_index>"
    assert _truncate_l1_snapshot(text, 500) == text


# ---------------------------------------------------------------------------
# G4：埋点（caplog 验证超窗计数 1 处）
# ---------------------------------------------------------------------------

def test_g4_context_window_pressure_warning(caplog):
    from prismclaw.engine.services.llm.stream_fn import _report_context_window_pressure

    caplog.set_level(logging.WARNING)
    # 估算输入 100 > input_budget 50 → over_budget
    assert (
        _report_context_window_pressure(
            est_input=100,
            input_budget=50,
            context_window=1000,
            model_max_tokens=100,
        )
        is True
    )
    assert "context_window_pressure" in caplog.text
    assert "over_budget=yes" in caplog.text


def test_g4_compute_max_tokens_clamp_smoke():
    from prismclaw.engine.services.llm.stream_fn import compute_max_tokens_clamp

    # 不变量：max_tokens ≤ window − 估算输入 − headroom；仅向下钳制。
    clamped, est = compute_max_tokens_clamp(
        model_max_tokens=32768,
        context_window=131072,
        estimated_input_tokens=100_000,
    )
    assert clamped == 131072 - 100_000 - 1024
    assert est == 100_000
    # 输入远低于窗口时不钳制（常规情形零漂移）。
    clamped2, _ = compute_max_tokens_clamp(
        model_max_tokens=32768,
        context_window=131072,
        estimated_input_tokens=20_000,
    )
    assert clamped2 == 32768


# ---------------------------------------------------------------------------
# G5：T5（context_payload）不落库
# ---------------------------------------------------------------------------

def test_g5_context_payload_stays_out_of_message_tree():
    from prismclaw.schema.llm.request import PackedLLMRequest
    from prismclaw.schema.llm.request_format import render_context_payload_text
    from prismclaw.engine.services.llm.stream_fn import _PROVIDER_REQUEST_ADAPTER
    from prismclaw.schema.agent.session import MessageEntry

    payload = {
        "type": "prism_context_payload",
        "schema_version": "prism_context_payload.v2",
        "blocks": [{"id": "b1", "source": "runtime_context", "content": "SENTINEL_T5_PAYLOAD"}],
    }
    req = PackedLLMRequest(
        messages=[{"role": "user", "content": "用户真实消息"}],
        system="sys",
        context_payload=payload,
        cache_strategy={},
    )

    # 1) payload 是独立字段，不进 messages（树投影的唯一来源）。
    assert req.context_payload is payload
    assert "SENTINEL_T5_PAYLOAD" not in json.dumps(req.messages, ensure_ascii=False)

    # 2) wire 适配注入 reminder 是独立副本，不改写 req.messages。
    prepared = _PROVIDER_REQUEST_ADAPTER.adapt(
        request=req, llm_provider="vllm", prompt_cache_runtime=None
    )
    assert req.messages == [{"role": "user", "content": "用户真实消息"}]
    # wire 层确实带上了 payload（证明分离设计），但被告知不可落树。
    assert "SENTINEL_T5_PAYLOAD" in json.dumps(prepared.messages, ensure_ascii=False)

    # 3) 树持久化节点无 context_payload 槽位——结构上不可能落库。
    assert "context_payload" not in MessageEntry.model_fields
    assert "SENTINEL_T5_PAYLOAD" in render_context_payload_text(payload)