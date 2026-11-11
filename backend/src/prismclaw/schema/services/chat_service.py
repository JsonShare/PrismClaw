"""Chat service protocol — the single entry point for starting agent conversations.

delivery 层通过此 Protocol 启动 agent 运行，不依赖 engine 内部实现。
Pi 先例：agent-core 定义 AgentRunner 接口，coding-agent 注入实现。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.agent.run_control_types import RunControl
from prismclaw.schema.storage.storage_types import SessionStore

__all__ = ["ChatService"]


@runtime_checkable
class ChatService(Protocol):
    """对话启动服务——delivery 层通过此 Protocol 启动 agent 运行。

    B/S 架构下，每个 HTTP 请求 / WebSocket 帧通过 ChatService 启动
    一次 agent 运行，获得事件流。ChatService 实现类内部持有
    AgentRunnerDeps 并调用 run_agent_stream，delivery 层无需感知。
    """

    @property
    def session_store(self) -> SessionStore:
        """暴露 SessionStore 供 delivery 层持久化使用。"""
        ...

    def start_conversation(
        self,
        request: RunRequest,
        *,
        tool_workspace_path: str | Path,
        control: RunControl | None = None,
    ) -> AsyncIterator[Any]:
        """启动一次 agent 运行，返回事件流。"""
        ...
