"""Bundle protocols for Dream, Plugin, and Persistence components."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ── Dream bundle ──

@runtime_checkable
class DreamReviewServiceProtocol(Protocol):
    async def review(self, session_id: str, candidates: list[Any]) -> list[Any]: ...


@runtime_checkable
class DreamBoundaryProtocol(Protocol):
    async def is_active(self) -> bool: ...
    async def claim(self, session_id: str) -> bool: ...


@runtime_checkable
class DreamAdminOpsProtocol(Protocol):
    async def trigger(self, session_id: str, phase: str) -> None: ...
    async def status(self, session_id: str) -> dict: ...


@runtime_checkable
class DreamComponents(Protocol):
    review_service: DreamReviewServiceProtocol
    boundary: DreamBoundaryProtocol
    ops: DreamAdminOpsProtocol


# ── Plugin bundle ──

@runtime_checkable
class ExtensionManagerProtocol(Protocol):
    async def load(self, path: str) -> None: ...
    async def reload(self) -> list[str]: ...


@runtime_checkable
class MCPManagerProtocol(Protocol):
    async def start(self, config: dict) -> None: ...
    async def stop(self, name: str) -> None: ...


@runtime_checkable
class PluginLifecycleProtocol(Protocol):
    async def initialize(self, settings: Any) -> None: ...
    async def get_bundle(self) -> Any: ...


@runtime_checkable
class PluginComponents(Protocol):
    extension_manager: ExtensionManagerProtocol
    runtime_mcp_manager: MCPManagerProtocol
    lifecycle_manager: PluginLifecycleProtocol


# ── Persistence bundle ──

@runtime_checkable
class RunSnapshotStoreProtocol(Protocol):
    async def save(self, session_id: str, snapshot: dict) -> None: ...
    async def load(self, session_id: str) -> dict | None: ...


@runtime_checkable
class RunOwnershipStoreProtocol(Protocol):
    async def claim(self, session_id: str, worker_id: str) -> bool: ...
    async def release(self, session_id: str) -> None: ...


@runtime_checkable
class TrajectoryPersistenceProtocol(Protocol):
    async def persist(self, session_id: str, cells: list[Any]) -> None: ...
    async def query(self, session_id: str) -> list[Any]: ...


@runtime_checkable
class PersistenceComponents(Protocol):
    run_snapshot_store: RunSnapshotStoreProtocol
    run_ownership_store: RunOwnershipStoreProtocol
    trajectory_persistence: TrajectoryPersistenceProtocol
