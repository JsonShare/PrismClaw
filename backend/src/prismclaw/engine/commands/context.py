from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps


@dataclass
class CommandContext:
    session_id: str
    session_key: str
    workspace_id: str
    user_id: str
    channel: str

    deps: AgentRunnerDeps
    session_router: Any  # SessionRouter instance, injected from channels
    workspace_base: Path

    reply: Callable[[str], Awaitable[None]]
    dispatch_user_message: Callable[[str], Awaitable[None]]

    raw: dict[str, Any]

    settings: Any

    redis_client: Any = None
    memory_store: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    agent_settings: Any = None
    command_timeout: float = 30.0
    queue_registry: Any = None
    session_queue: Any = None
    last_usage: dict[str, int] | None = None
    worker_registry: Any = None
    gateway_router: Any = None
    mcp_manager: Any = None
    runtime_mode_snapshot: dict[str, Any] | None = None
    user_profile_store: Any = None  # UserProfileStore, injected by wiring

    # Injected callbacks — wiring layer provides implementations so engine
    # does not need to import app/wiring (P2/P9: engine→app decoupling).
    plugin_reloader: Callable[..., Awaitable[Any]] | None = None
    capability_refresher: Callable[..., Any] | None = None
    # 命令身份投影回调: dispatch 前将命令名设为 active_skill_invocation,
    # 使 hook matcher 可见。wiring 层实现调用 project_active_skill_invocation()。
    # 签名: (skill_name: str, plugin_name: str | None) -> None
    project_command_identity: Callable[..., Any] | None = None
