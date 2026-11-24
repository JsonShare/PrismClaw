"""WebSocket message protocol definitions for the Web channel."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal

from prismclaw.schema.agent.task_runtime import ConfirmationDecision
from prismclaw.schema.protocol.protocol_types import PROTOCOL_VERSION


@dataclass
class IdentifyMessage:
    type: Literal["identify"] = "identify"
    token: str = ""
    protocol_version: int = PROTOCOL_VERSION


@dataclass
class ChatSendMessage:
    type: Literal["chat.send"] = "chat.send"
    conversation_id: str = ""
    workspace_id: str | None = None
    content: str = ""
    # 风险确认决策：由前端风险确认按钮设置，任务恢复执行时由 preparer 消费。
    confirmation_decision: ConfirmationDecision | None = None
    attachments: list[Any] = field(default_factory=list)
    trajectory: str | None = None  # "full", "dry", or None (use env default)
    # 前端生成的消息唯一标识：排队期间可被 chat.steer（提升为 steering 注入
    # 当前 run）与 chat.cancel（取消排队）按 id 引用。None 时不可操作（兼容
    # 不带 id 的调用方）。
    client_message_id: str | None = None


@dataclass
class ChatSteerMessage:
    """把一条已排队消息提升为 steering——注入当前活跃 run（中途纠偏）。

    消息本体温留排队队列，由 consumer 取出时按 _steered 标记转移进
    steering 队列（见 SessionQueue.mark_steered）。
    """

    type: Literal["chat.steer"] = "chat.steer"
    conversation_id: str = ""
    client_message_id: str = ""


@dataclass
class ChatCancelMessage:
    """取消一条已排队消息——consumer 取出时跳过执行（不注入、不执行）。"""

    type: Literal["chat.cancel"] = "chat.cancel"
    conversation_id: str = ""
    client_message_id: str = ""


@dataclass
class ChatAbortMessage:
    type: Literal["chat.abort"] = "chat.abort"
    conversation_id: str = ""


@dataclass
class PongMessage:
    type: Literal["pong"] = "pong"


@dataclass
class ToolClarifyMessage:
    """AskUserQuestion 澄清回答——前端通过 tool.clarify 消息回传。"""

    type: Literal["tool.clarify"] = "tool.clarify"
    conversation_id: str = ""
    approval_id: str = ""
    tool_call_id: str = ""
    answer: str = ""


ClientMessage = (
    IdentifyMessage
    | ChatSendMessage
    | ChatSteerMessage
    | ChatCancelMessage
    | ChatAbortMessage
    | PongMessage
    | ToolClarifyMessage
)

SERVER_HELLO = "hello"
SERVER_READY = "ready"
SERVER_CHAT_START = "chat.start"
SERVER_CHAT_DELTA = "chat.delta"
SERVER_CHAT_DONE = "chat.done"
SERVER_CHAT_QUEUED = "chat.queued"
SERVER_CHAT_STEERING_ACK = "chat.steering_ack"
SERVER_REFERENCE = "event:reference"
SERVER_QUESTIONS = "event:questions"
SERVER_OUTBOX = "event:outbox"
SERVER_FILE_CHANGES = "event:file_changes"
SERVER_TASK_RUNTIME = "task.runtime"
SERVER_TASK_TIMELINE = "task.timeline"
SERVER_RUN_SNAPSHOT = "run.snapshot"
SERVER_RUN_KEEPALIVE = "run.keepalive"
SERVER_TOOL_CLARIFY_REQUEST = "tool.clarify_request"
SERVER_PING = "ping"
SERVER_ERROR = "error"

_CLIENT_MESSAGE_MAP: dict[str, type[ClientMessage]] = {
    "identify": IdentifyMessage,
    "chat.send": ChatSendMessage,
    "chat.steer": ChatSteerMessage,
    "chat.cancel": ChatCancelMessage,
    "chat.abort": ChatAbortMessage,
    "pong": PongMessage,
    "tool.clarify": ToolClarifyMessage,
}


def parse_client_message(data: dict[str, Any]) -> ClientMessage | None:
    """Route a raw JSON dict to the appropriate client message dataclass.

    Returns ``None`` if ``type`` is missing or unrecognised. Unknown fields
    (including fields from older clients) are filtered out by
    :func:`dataclasses.fields`.
    """
    msg_type = data.get("type")
    if msg_type is None:
        return None
    cls = _CLIENT_MESSAGE_MAP.get(msg_type)
    if cls is None:
        return None
    known = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in known}
    return cls(**kwargs)
