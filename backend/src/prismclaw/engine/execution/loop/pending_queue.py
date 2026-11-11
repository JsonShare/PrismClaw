"""PendingMessageQueue — 替代 appended_messages_override 的消息注入队列。

核心改进：
- 封装 `_append_control_message_override` 的列表管理模式
- 明确的 consume vs peek 语义，消除 `= None` 重置的隐式行为

注：用户中途纠偏（steering）不走本通道——pending/appended 注入不落树，
压缩重启即丢；steering 由通道层 SessionQueue 标记法 + 引擎闭包落树
（role="user" MessageEntry）实现，见 docs/architecture/sdd-steering-channel.md。
"""
from __future__ import annotations

from prismclaw.schema.agent.session import AgentMessageDict, ProjectionInputMessage, SystemReminderProjection
from typing import cast

__all__ = ["PendingMessageQueue"]


def _copy_message(message: ProjectionInputMessage) -> ProjectionInputMessage:
    """深拷贝一条消息，防止外部修改影响队列。"""
    if isinstance(message, SystemReminderProjection):
        return message
    return cast(AgentMessageDict, dict(message))


class PendingMessageQueue:
    """消息注入队列，替代 appended_messages_override 变量。

    内部持有 list[ProjectionInputMessage]，提供 append / snapshot / replace / clear 操作。

    典型用法（runner.py 主循环）：
        queue = PendingMessageQueue()
        queue.append(msg)           # 替代 _append_control_message_override(amo, msg)
        msgs = queue.snapshot()     # 替代 messages_override=appended_messages_override
        queue.replace(result.msgs)  # 替代 appended_messages_override = result.appended_messages
        queue.clear()               # 替代 appended_messages_override = None
    """

    def __init__(self) -> None:
        self._messages: list[ProjectionInputMessage] = []

    def append(self, message: ProjectionInputMessage) -> None:
        """追加一条消息。等价于 _append_control_message_override(current, msg)。"""
        self._messages.append(_copy_message(message))

    def extend(self, messages: list[ProjectionInputMessage] | None) -> None:
        """追加多条消息。"""
        if messages is None:
            return
        for msg in messages:
            self._messages.append(_copy_message(msg))

    def snapshot(self) -> list[ProjectionInputMessage] | None:
        """返回消息列表的快照（不清空）。

        返回 None 如果队列为空——与下游函数签名 `list | None` 兼容。
        """
        return self._messages[:] if self._messages else None

    def replace(self, messages: list[ProjectionInputMessage] | None) -> None:
        """替换整个消息列表。

        用于下游函数（skill_dispatch）返回新的消息列表时。
        """
        self._messages.clear()
        if messages is not None:
            self._messages.extend(messages)

    def clear(self) -> None:
        """清空所有消息。替代 appended_messages_override = None。"""
        self._messages.clear()

    @property
    def has_messages(self) -> bool:
        return len(self._messages) > 0