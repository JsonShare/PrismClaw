"""DefaultChatService — ChatService Protocol 的运行时实现。

唯一知道 AgentRunnerDeps 的 ChatService 实现。
runtime 层持有此实现并注入到 channels 层，channels 只依赖 ChatService Protocol。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from prismclaw.schema.agent.types import RunRequest
from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps
from prismclaw.engine.execution.loop.runner import run_agent_stream
from prismclaw.schema.agent.run_control_types import RunControl


class DefaultChatService:
    """ChatService 实现：持有 AgentRunnerDeps，委托 run_agent_stream。"""

    def __init__(self, deps: AgentRunnerDeps) -> None:
        self._deps = deps

    @property
    def session_store(self) -> Any:
        return self._deps.session_store

    @property
    def deps(self) -> AgentRunnerDeps:
        """暴露底层 deps 供内部使用（chat_runner 等深度耦合场景）。"""
        return self._deps

    def start_conversation(
        self,
        request: RunRequest,
        *,
        tool_workspace_path: Any,
        control: RunControl | None = None,
    ) -> AsyncIterator[Any]:
        return run_agent_stream(
            request,
            self._deps,
            tool_workspace_path=tool_workspace_path,
            control=control,
        )
