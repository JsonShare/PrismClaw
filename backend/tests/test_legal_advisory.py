"""法律域按需注入门控 —— 防止法律标注污染非法律对话。

分级匹配的取舍是"宁可漏判，不可误注入"：漏判只损失一段提示，
误注入会让每个技术对话都带上无关的法律要求。
"""
from __future__ import annotations

import asyncio

import pytest

from prismclaw.engine.hooks import _coerce_context
from prismclaw.engine.plugins.hooks.schema import HookContext
from subpackages.legal.integration.bundle import LegalAdvisoryHook, is_legal_task


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 技术语境：弱信号词单独出现不触发
        ("帮我看看 MCP 协议怎么配置", False),
        ("这个模块的管辖范围是什么", False),
        ("继承父类的这个属性", False),
        ("HTTP 协议版本不兼容", False),
        ("这个接口协议文档在哪", False),
        # 法律语境：强信号或弱信号+语境词触发
        ("离职协议的模板在哪", True),
        ("软件著作权登记流程", True),
        ("劳动合同到期续签", True),
        ("申请专利的技术方案", True),
        ("合同里的违约条款怎么认定", True),
        ("帮我看看这份协议，甲方违约了怎么办", True),
        # 空输入
        ("", False),
    ],
)
def test_is_legal_task(text: str, expected: bool) -> None:
    assert is_legal_task(text) is expected


def test_hook_skips_non_legal_task() -> None:
    hook = LegalAdvisoryHook()
    result = asyncio.run(
        hook.execute(
            HookContext(
                event="user_prompt_submit",
                session_id="s1",
                user_message="优化这段 Python 代码的性能",
            )
        )
    )
    assert result.additional_context is None


def test_hook_injects_for_legal_task() -> None:
    hook = LegalAdvisoryHook()
    result = asyncio.run(
        hook.execute(
            HookContext(
                event="user_prompt_submit",
                session_id="s1",
                user_message="劳动合同到期续签需要注意什么",
            )
        )
    )
    assert result.additional_context is not None
    assert "法律域要求" in result.additional_context


def test_hook_handles_missing_user_message() -> None:
    """USER_PROMPT_SUBMIT 之外的上下文没有 user_message，必须安全跳过。"""
    hook = LegalAdvisoryHook()
    result = asyncio.run(
        hook.execute(HookContext(event="session_start", session_id="s1"))
    )
    assert result.additional_context is None


def test_coerce_context_passes_user_message() -> None:
    """preparer 传入 dict payload 时不能丢掉 user_message。"""
    ctx = _coerce_context({"session_id": "s1", "user_message": "合同纠纷"})
    assert ctx.user_message == "合同纠纷"


def test_coerce_context_from_object() -> None:
    class _Payload:
        session_id = "s1"
        user_message = "诉讼时效"

    ctx = _coerce_context(_Payload())
    assert ctx.user_message == "诉讼时效"
