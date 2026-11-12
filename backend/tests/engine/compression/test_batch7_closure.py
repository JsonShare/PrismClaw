"""批 7 收尾闭环测试：G1 附件预算 / G3 l1 截断 / G4 埋点 / G5 T5 不落库。"""
from __future__ import annotations

import json


from prismclaw.engine.execution.request.attachments import (
    _compute_attachment_char_budget,
)
from prismclaw.engine.execution.request.system_prompt import (
    _l1_rough_token_count,
    _truncate_l1_snapshot,
)
from prismclaw.schema.config.config import ContextBudgetConfig


# ---------------------------------------------------------------------------
# G1：附件预算（absolute）
# ---------------------------------------------------------------------------

def test_g1_absolute_zero_drift():
    budget = ContextBudgetConfig()
    # 老公式：history=106496 → 106496*0.6=63897 → *1.5=95845
    assert _compute_attachment_char_budget(budget) == 95_845


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
    import logging

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
