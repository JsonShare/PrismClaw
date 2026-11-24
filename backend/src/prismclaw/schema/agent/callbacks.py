"""Loop behavioral callbacks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AgentCallbacks:
    """Agent loop 行为回调容器，可按需替换单个回调。"""

    on_compaction_node: Callable[..., None] | None = None
    on_mcp_death: Callable[[str], Awaitable[None]] | None = None
    on_subagent_progress: Callable[[Any], Awaitable[None]] | None = None
    # 后台子代理终态回调：参数为 ParallelTaskRuntimeRecord。
    # 仅在 dispatch_mode='background' 且进程内有投递通道时触发；
    # 无回调（CLI / headless）时结果留在会话树，由下一轮 note 呈现。
    on_subagent_terminal: Callable[[Any], Awaitable[None]] | None = None
