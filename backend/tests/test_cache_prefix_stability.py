"""D2/F3 缓存前缀稳定性：legacy_bridge reminder 恒尾部追加。

改动前 reminder 以 insert(-1) 插在当前 user 消息之前，位置逐轮漂移，
使其后全部内容失去前缀可缓存性；改动后恒 append 到尾部，跨轮共享
前缀最大化（仅 reminder 本身逐轮更新）。
"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.services.llm.client import _PROVIDER_REQUEST_ADAPTER

_PAYLOAD = {"blocks": [{"id": "runtime_context", "content": "时间上下文"}]}


def _request(messages: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        messages=messages,
        system=None,
        context_payload=dict(_PAYLOAD),
    )


def _adapt(messages: list[dict]) -> list[dict]:
    return _PROVIDER_REQUEST_ADAPTER._adapt_with_legacy_context_bridge(
        request=_request(messages),
        llm_provider=None,
        prompt_cache_runtime=None,
    )


def _is_reminder(msg: dict) -> bool:
    content = msg.get("content")
    return (
        msg.get("role") == "user"
        and isinstance(content, list)
        and "<system-context>" in str(content[0].get("text", ""))
    )


def test_reminder_appended_after_current_user_message() -> None:
    out = _adapt([
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ])
    assert _is_reminder(out[-1])
    assert out[2]["content"] == "u2"


def test_reminder_appended_after_assistant_message() -> None:
    out = _adapt([
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ])
    assert _is_reminder(out[-1])


def test_reminder_appended_to_empty_messages() -> None:
    out = _adapt([])
    assert len(out) == 1 and _is_reminder(out[0])


def test_cross_turn_shared_prefix_is_maximal() -> None:
    """turn N 输出（除尾部 reminder 外）必须是 turn N+1 输出的前缀。"""
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    turn_n = _adapt(history)
    turn_n1 = _adapt([
        *history,
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
    ])
    # turn N 的最后一条是 reminder；其前的全部内容必须逐字节等于
    # turn N+1 的同位前缀——即历史部分零漂移。
    assert turn_n[:-1] == turn_n1[: len(turn_n) - 1]
    assert _is_reminder(turn_n[-1]) and _is_reminder(turn_n1[-1])
