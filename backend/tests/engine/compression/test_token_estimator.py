"""Phase 1 基线: Token 估算器契约测试。

锁定不变量（重构过程必须持续通过）：
1. estimate_tokens 单一权威入口位于 prismclaw.schema.shared.utils
2. 中文 0.90 tokens/char 校准生效（v2 estimator，glm-53 复核）
3. 空文本 → 0；非空 → >= 1
4. estimate_message_tokens：8 token base + content + tool_calls
5. estimate_messages_tokens：sum(单条)
6. PRISMCLAW_COMPACTION_ESTIMATOR_V2 rollback 开关保留
"""
from __future__ import annotations



from prismclaw.schema.shared.utils import (
    _CJK_TOKENS_PER_CHAR,
    _OTHER_CHARS_PER_TOKEN,
    estimate_tokens,
)
from prismclaw.engine.compression.compaction import (
    estimate_message_tokens,
    estimate_messages_tokens,
)


# ──────────────────────────── 1. 基础契约 ────────────────────────────


def test_estimate_tokens_empty_returns_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0  # type: ignore[arg-type]


def test_estimate_tokens_non_empty_at_least_one():
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("你好") >= 1


def test_estimate_tokens_calibrated_constants():
    """校准常量保持稳定。

    2026-11-13 glm-53 drift 复核：原 CJK 0.75 / ASCII 4.0 持续低估 27-31%。
    调至 CJK 0.90（中文模型 CJK≈0.9-1.0 tok/char）、ASCII 3.4（2.72 高估与
    4.0 低估之间的中间值）。
    """
    assert _CJK_TOKENS_PER_CHAR == 0.90
    assert _OTHER_CHARS_PER_TOKEN == 3.4


# ──────────────────────────── 2. 中文校准契约（核心） ────────────────────────────


def test_estimate_tokens_chinese_higher_than_legacy_len_div_4():
    """中文消息 token 估算 ≈ 0.90 tokens/char，远高于 len//4。"""
    chinese_text = "液压油改进剂密度调整" * 20  # 220 中文字符
    estimated = estimate_tokens(chinese_text)
    legacy = len(chinese_text) // 4  # 55
    # v2 应至少是 legacy 的 2 倍（实际 165 ≈ 3x）
    assert estimated > legacy * 2, f"v2={estimated}, legacy={legacy}"


def test_estimate_tokens_chinese_english_mixed():
    """中英混合文本：CJK 部分按 0.90，其他按 1/3.4。"""
    text = "你好 world"  # 2 中文 + 6 非中文（含空格）
    estimated = estimate_tokens(text)
    # 至少大于 legacy len//4
    assert estimated > len(text) // 4


def test_estimate_tokens_chinese_long_text_within_budget():
    """长中文文本估算符合 0.90 tokens/char 比例。"""
    text = "中" * 1000  # 纯中文 1000 字符
    estimated = estimate_tokens(text)
    # 应接近 900 (1000 × 0.90)，允许 max(1, int(...)) 取整误差
    assert 850 <= estimated <= 950


# ──────────────────────────── 3. PRISMCLAW_COMPACTION_ESTIMATOR_V2 rollback 开关 ────────────────────────────


def test_estimate_tokens_v2_enabled_by_default(monkeypatch):
    """默认 v2 启用：CJK 校准生效。"""
    monkeypatch.delenv("PRISMCLAW_COMPACTION_ESTIMATOR_V2", raising=False)
    # 重新加载模块状态（_estimator_v2_enabled 读环境变量）
    # 直接调用 estimate_tokens 验证当前状态
    chinese_text = "中" * 100
    estimated = estimate_tokens(chinese_text)
    # 默认 v2 启用 → 应 ≈ 75
    assert estimated > 50  # 远大于 len//4=25


def test_estimate_tokens_v2_disabled_restores_legacy(monkeypatch):
    """rollback 开关 = false → 使用 legacy len//4 行为。"""
    monkeypatch.setenv("PRISMCLAW_COMPACTION_ESTIMATOR_V2", "false")
    # 需要重新 import 让 _estimator_v2_enabled 重读
    # 由于 _estimator_v2_enabled 是函数（每次调用读 env），直接调用即可
    from prismclaw.schema.shared.utils import _estimator_v2_enabled
    assert _estimator_v2_enabled() is False

    # legacy 路径：len//4
    chinese_text = "中" * 100
    estimated = estimate_tokens(chinese_text)
    assert estimated == 25  # 100 // 4


# ──────────────────────────── 4. estimate_message_tokens 契约 ────────────────────────────


def test_estimate_message_tokens_string_content():
    """content 为字符串 → 8 base + estimate_tokens(content)。"""
    msg = {"role": "user", "content": "你好 world"}
    tokens = estimate_message_tokens(msg)
    assert tokens >= 8
    assert tokens == 8 + estimate_tokens("你好 world")


def test_estimate_message_tokens_list_content_text_block():
    """content 为 list[dict] 含 text block → 8 + estimate_tokens(text)。"""
    msg = {
        "role": "assistant",
        "content": [{"type": "text", "text": "回答内容"}],
    }
    tokens = estimate_message_tokens(msg)
    assert tokens == 8 + estimate_tokens("回答内容")


def test_estimate_message_tokens_list_content_image_block():
    """content 为 list 含 image block → 8 + vision.tokens_per_image（配置唯一来源）。"""
    from prismclaw.infra.settings import load_settings

    msg = {
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": "data:..."}}],
    }
    tokens = estimate_message_tokens(msg)
    assert tokens == 8 + int(load_settings().vision.tokens_per_image)


def test_estimate_message_tokens_with_tool_calls():
    """带 tool_calls → name + arguments 都计入。"""
    msg = {
        "role": "assistant",
        "content": "calling tool",
        "tool_calls": [
            {
                "function": {
                    "name": "read",
                    "arguments": '{"file_path": "/tmp/test.txt"}',
                }
            }
        ],
    }
    tokens = estimate_message_tokens(msg)
    expected = (
        8
        + estimate_tokens("calling tool")
        + estimate_tokens("read")
        + estimate_tokens('{"file_path": "/tmp/test.txt"}')
        + 5  # tool_call id/type/function envelope
    )
    assert tokens == expected


def test_estimate_message_tokens_tool_call_dict_arguments():
    """tool_calls arguments 为 dict → 转 str 后估算。"""
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "read",
                    "arguments": {"file_path": "/tmp/test.txt"},
                }
            }
        ],
    }
    tokens = estimate_message_tokens(msg)
    expected = (
        8
        + estimate_tokens("read")
        + estimate_tokens(str({"file_path": "/tmp/test.txt"}))
        + 5  # tool_call id/type/function envelope
    )
    assert tokens == expected


# ──────────────────────────── 5. estimate_messages_tokens 求和契约 ────────────────────────────


def test_estimate_messages_tokens_empty_list():
    assert estimate_messages_tokens([]) == 0


def test_estimate_messages_tokens_sum_of_singles():
    """estimate_messages_tokens == sum(estimate_message_tokens(m) for m in messages)。"""
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，请问需要什么帮助？"},
        {"role": "user", "content": [{"type": "text", "text": "问题详情"}]},
    ]
    total = estimate_messages_tokens(messages)
    expected = sum(estimate_message_tokens(m) for m in messages)
    assert total == expected


# ──────────────────────────── 6. 唯一入口契约（Phase 4 后启用） ────────────────────────────
#
# Phase 4 重构后必须验证：所有调用方使用 prismclaw.schema.shared.estimate_tokens。
# 当前 Phase 1 阶段，compression/compaction/compaction.py 仍提供 wrapper。


def test_estimate_tokens_single_authoritative_source():
    """schema.shared.estimate_tokens 是唯一权威实现；
    compaction.compaction.estimate_tokens 是薄包装 delegate。"""
    from prismclaw.engine.compression.compaction import estimate_tokens as wrapper
    # wrapper 应与权威实现返回相同结果
    test_text = "测试 token 估算一致性 hello world"
    assert wrapper(test_text) == estimate_tokens(test_text)


# ──────────────────────────── 7. 独立插件契约 ────────────────────────────


def test_heuristic_counter_honors_custom_coefficients():
    """配置化系数生效：插件不再依赖散落的模块级魔法数。"""
    from prismclaw.token_counting import HeuristicTokenCounter

    counter = HeuristicTokenCounter(
        cjk_tokens_per_char=2.0,
        other_chars_per_token=1.0,
    )
    assert counter.count_text("中" * 10) == 20


def test_heuristic_counter_count_bytes_by_kind():
    """字节→token 按 kind 选择系数；空载荷为 0。"""
    from prismclaw.token_counting import HeuristicTokenCounter

    counter = HeuristicTokenCounter(wire_bytes_per_token=3.5, json_bytes_per_token=3.0)
    assert counter.count_bytes(0) == 0
    assert counter.count_bytes(350, kind="wire") == 100
    assert counter.count_bytes(300, kind="json") == 100


def test_active_counter_is_swappable():
    """注册表可替换全局实现，schema 门面立即跟随。"""
    from prismclaw.token_counting import (
        HeuristicTokenCounter,
        reset_token_counter,
        set_token_counter,
    )

    try:
        set_token_counter(
            HeuristicTokenCounter(cjk_tokens_per_char=1.0, other_chars_per_token=1.0)
        )
        assert estimate_tokens("中" * 10) == 10
    finally:
        reset_token_counter()
    assert estimate_tokens("中" * 100) == 90
