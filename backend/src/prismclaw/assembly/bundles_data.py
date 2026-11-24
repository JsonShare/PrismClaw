from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.execution.checkpoint import RunSnapshotStore
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.agent.bundles import RunOwnershipStoreProtocol
from prismclaw.schema.services.lock import LockManagerProtocol
from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore, WorkspaceStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismclaw.engine.dream.dispatch.dispatch import (
        DreamDispatchConsumer,
        RedisDreamDispatchQueue,
    )
    from prismclaw.engine.mcp.client_manager import MCPClientManager


@dataclass
class AppInfrastructureBundle:
    redis_client: Any  # redis.Redis | None — avoid importing redis at schema level
    lock_manager: LockManagerProtocol
    session_store: SessionStore
    run_snapshot_store: RunSnapshotStore | None
    run_ownership_store: RunOwnershipStoreProtocol | None
    task_manager: TaskManager
    worker_registry: Any  # WorkerRegistry — avoid app.gateway import at assembly level
    worker_id: str
    dream_dispatch_queue: RedisDreamDispatchQueue | None = None
    workspace_store: WorkspaceStore | None = None
    workspace_base: Path | None = None
    memory_store: MemoryStore | None = None
    mcp_manager: MCPClientManager | None = None
    mcp_death_handler: Callable[[str], Awaitable[None]] | None = None
    external_tool_registrar: Callable[..., Any] | None = None
    domain_stores: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppRuntimeBundle:
    runner_deps: Any  # AgentRunnerDeps — avoid circular import
    web_deps: Any = None  # WebDeps
    dream_dispatch_consumer: DreamDispatchConsumer | None = None
    dream_dispatch_health_task_id: str | None = None
    domain_governance_reducers: list[Any] = field(default_factory=list)


@dataclass
class AppLifecycleBundle:
    gateway_router: Any = None  # GatewayRouter
    forward_consumer: Any = None  # ForwardConsumer
    auto_takeover_task_id: str | None = None
    domain_routers: list[tuple[Any, str]] = field(default_factory=list)
