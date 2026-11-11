from __future__ import annotations

"""Storage Protocol 自建定义——让 contracts 不直接依赖 storage 包。

此处重新定义 SessionStore / MemoryStore / WorkspaceStore 的 Protocol 签名，
与 storage/*.base.py 中的原始定义通过 duck-typing 兼容，无需运行时继承。

只复制 Protocol 定义，不复制辅助函数（emit_commit_metrics 等）。
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

# 方法签名中引用的 kernel 类型 — TYPE_CHECKING to avoid kernel↔contracts circular import
if TYPE_CHECKING:
    from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.agent.session import (
    SessionEvent,
    SessionHistorySummary,
)


# ── 类型别名 + Pydantic 模型（来自 contracts.infra.memory_types）────
from prismclaw.schema.memory import (
    MemoryEntry,
    ArchiveEntry,
    L4ArtifactEntry,
)


# ── SessionStore Protocol ────────────────────────────────────────

@runtime_checkable
class SessionStore(Protocol):
    """与 prismclaw.persistence.session.base.SessionStore duck-typing 兼容。

    只定义 Protocol 方法签名，不包含 emit_commit_metrics 等辅助函数。
    """

    # ── Read Path ──────────────────────────────────────────────
    async def load(self, session_id: str) -> SessionTree | None: ...
    async def save_header(self, tree: SessionTree) -> None: ...

    # ── Unified Write Gateway ──────────────────────────────────
    async def commit_events(self, events: list[SessionEvent]) -> list[SessionEvent]: ...

    # ── Low-level Event Log ────────────────────────────────────
    async def append_event(self, event: SessionEvent) -> None: ...
    async def append_events_batch(self, events: list[SessionEvent]) -> None: ...

    # ── Event Log Queries ──────────────────────────────────────
    async def get_events(self, session_id: str, *, since_seq: int = 0) -> list[SessionEvent]: ...
    async def get_events_by_time(self, session_id: str, *, since_ms: int = 0, until_ms: int = 0) -> list[SessionEvent]: ...
    async def get_event_count(self, session_id: str) -> int: ...
    async def rebuild_tree(self, session_id: str) -> SessionTree | None: ...

    # ── Session Lifecycle ──────────────────────────────────────
    async def get_current_session_id(self, session_key: str) -> str | None: ...
    async def set_current_session_id(self, session_key: str, session_id: str) -> None: ...
    async def create_new_session(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        parent_session_id: str | None = None,
        title: str | None = None,
    ) -> SessionTree: ...
    async def list_session_history(
        self, session_key: str, limit: int = 20
    ) -> list[SessionHistorySummary]: ...
    async def delete_session(self, session_id: str) -> bool: ...


# ── MemoryStore Protocol ─────────────────────────────────────────

@runtime_checkable
class MemoryStore(Protocol):
    """与 prismclaw.persistence.memory.base.MemoryStore duck-typing 兼容。"""

    # ── Index Operations ───────────────────────────────────────
    async def index_get(self, session_key: str) -> list[MemoryEntry]: ...
    async def index_update(self, session_key: str, entry: MemoryEntry) -> None: ...
    async def index_remove(self, session_key: str, entry_id: str) -> None: ...

    # ── Search / Store / Delete ────────────────────────────────
    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
        per_layer_limits: dict[str, int] | None = None,
        domain: str = "general",
    ) -> list[MemoryEntry]: ...
    async def store(self, session_key: str, entry: MemoryEntry) -> None: ...
    async def delete(self, session_key: str, entry_id: str) -> None: ...

    # ── Archive Operations ─────────────────────────────────────
    async def archive_entry(self, session_key: str, entry_id: str, *, reason: str = "") -> bool: ...
    async def archive_session(
        self, session_key: str, session_id: str, summary: str, *,
        payload: dict[str, Any] | None = None,
        demote_entries: bool = True,
    ) -> None: ...
    async def delete_archive(self, session_key: str, session_id: str) -> bool: ...
    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[ArchiveEntry]: ...

    # ── L4 Artifact Operations ─────────────────────────────────
    async def store_l4_artifact(self, session_key: str, entry: L4ArtifactEntry) -> None: ...
    async def delete_l4_artifact(self, session_key: str, artifact_id: str) -> bool: ...
    async def search_l4_artifacts(
        self,
        session_key: str,
        query: str,
        *,
        artifact_kinds: list[str] | None = None,
        limit: int = 5,
        min_similarity: float = 0.0,
    ) -> list[L4ArtifactEntry]: ...

    # ── Stats / Queries ────────────────────────────────────────
    async def get_archive_storage_stats(self, session_key: str) -> dict[str, int]: ...
    async def count_by_layer(self, session_key: str) -> dict[str, int]: ...
    async def get_entry_by_id(
        self, session_key: str, entry_id: str, *, prefix_match: bool = False
    ) -> tuple[str | None, str | None]: ...
    async def get_contents_by_ids(
        self, session_key: str, entry_ids: list[str]
    ) -> dict[str, str]: ...

    async def get_entries_by_ids(
        self, session_key: str, entry_ids: list[str]
    ) -> list[MemoryEntry]: ...

    # ── References / Validation / Audit ────────────────────────
    async def record_memory_references(
        self, session_key: str, records: list[dict[str, Any]]
    ) -> int: ...
    async def writeback_use_counts(self, session_key: str, entry_ids: list[str]) -> int: ...
    async def record_validation_events(
        self, session_key: str, events: list[dict[str, Any]]
    ) -> int: ...
    async def record_arbitration_audit(
        self,
        session_key: str,
        *,
        candidate: dict[str, Any],
        near_neighbors: list[dict[str, Any]] | None,
        decision: str,
        decided_by: str,
        mode: str,
        executed: bool,
        target_id: str | None = None,
        reason: str | None = None,
        latency_ms: int | None = None,
    ) -> bool: ...
    async def count_validation_signals(
        self, session_key: str, entry_ids: list[str]
    ) -> dict[str, dict[str, int]]: ...
    async def list_entries_by_status(
        self,
        session_key: str,
        statuses: list[str],
        *,
        limit: int = 500,
    ) -> list[MemoryEntry]: ...
    async def aggregate_validation_signals(
        self, session_key: str, *, since: float | None = None
    ) -> dict[str, dict[str, int]]: ...
    async def aggregate_pending_validation_signals(
        self, session_key: str
    ) -> dict[str, dict[str, int]]: ...
    async def apply_entry_updates(
        self, session_key: str, updates: list[tuple[str, dict[str, Any]]]
    ) -> int: ...

    async def verify_integrity(
        self, session_key: str, *, limit: int = 10000
    ) -> dict[str, Any]: ...

    async def get_entry_id_by_entity_key(
        self, session_key: str, entity_key: str,
    ) -> str | None: ...

    # ── Lifecycle ──────────────────────────────────────────────
    async def close(self) -> None: ...


# ── WorkspaceStore Protocol ──────────────────────────────────────

@runtime_checkable
class WorkspaceStore(Protocol):
    """与 prismclaw.persistence.workspace.base.WorkspaceStore duck-typing 兼容。"""

    async def get_file(self, workspace_id: str, filename: str) -> str | None: ...
    async def put_file(self, workspace_id: str, filename: str, content: str) -> None: ...


# ── UserStore Protocol ───────────────────────────────────────────

from prismclaw.schema.services.user_types import User


@runtime_checkable
class UserStore(Protocol):
    """与 prismclaw.persistence.user.postgres.UserRepository duck-typing 兼容。"""

    async def get_by_id(self, user_id: str) -> User | None: ...
    async def list_all(self, *, include_disabled: bool = False) -> list[User]: ...
    async def count(self) -> int: ...
    async def authenticate(self, identifier: str, password: str) -> User | None: ...
    async def get_by_email(self, email: str) -> User | None: ...
    async def nickname_exists(self, nickname: str) -> bool: ...
    async def create(
        self,
        *,
        user_id: str,
        password: str,
        email: str | None = None,
        nickname: str | None = None,
    ) -> User: ...
    async def seed_insert(
        self,
        *,
        user_id: str,
        password: str,
        email: str | None = None,
        nickname: str | None = None,
    ) -> bool: ...
    async def set_password(self, user_id: str, password: str) -> bool: ...
    async def update_profile(self, user_id: str, **kwargs: Any) -> bool: ...
    async def delete(self, user_id: str) -> bool: ...


__all__ = ["SessionStore", "MemoryStore", "WorkspaceStore", "UserStore"]
