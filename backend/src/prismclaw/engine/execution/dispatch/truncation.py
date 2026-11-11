"""D9 截断保护 — 对齐 Pi 的 failToolCallsFromTruncatedMessage()。

当 LLM 输出因 max_tokens 被截断（finish_reason=="length"），流式工具调用参数
可能被 JSON salvage 解析器"修复"为有效但语义不完整的 JSON，然后被执行——这会
导致工具收到损坏的参数。

安全做法：全部标记为失败，让 LLM 下一轮重新生成。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.execution.dispatch.execution import ToolExecutionArtifacts

logger = logging.getLogger(__name__)


def fail_truncated_tool_calls(
    *,
    tool_calls: list[dict[str, Any]],
    session_id: str = "?",
) -> ToolExecutionArtifacts:
    """截断保护：当 LLM 输出被截断时，丢弃所有可能损坏的工具调用。

    对齐 Pi 的 failToolCallsFromTruncatedMessage()：
    - 截断时工具调用参数可能不完整
    - JSON salvage 可能将不完整参数"修复"为有效但语义错误的值
    - 安全做法：全部标记为失败，让 LLM 下一轮重新生成

    Returns:
        ToolExecutionArtifacts 包含所有失败的工具结果
    """
    from prismclaw.engine.execution.dispatch.execution import ToolExecutionArtifacts
    from prismclaw.schema.agent.data_types import TextBlock
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolResult

    logger.warning(
        "tool.truncated_response session_id=%s tool_count=%d "
        "finish_reason=length — failing all tool calls defensively",
        session_id,
        len(tool_calls),
    )
    results = [
        ToolResult(
            tool_call_id=tc.get("id", ""),
            content=[TextBlock(
                text=f"工具调用 \"{tc.get('function', {}).get('name', '?')}\" "
                     f"未被执行：本次响应触达输出 token 上限，"
                     f"其参数可能已被截断。请使用完整的参数重新发起该工具调用。"
            )],
            is_error=True,
        )
        for tc in tool_calls
    ]
    return ToolExecutionArtifacts(
        tool_calls=list(tool_calls),
        results=results,
        compaction_turn=None,
    )
