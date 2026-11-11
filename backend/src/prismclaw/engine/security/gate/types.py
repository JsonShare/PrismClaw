"""gate 类型定义 — actor_identity / runtime_context / protocols / stubs 合并。

Protocol definitions live in ``prismclaw.contracts.agent.security_store`` and
``prismclaw.contracts.agent.security``; this module re-exports them for
backward-compatibility and keeps the engine-internal stubs / data models.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# ── Re-exports from contracts ──────────────────────────────────────────────
from prismclaw.schema.agent.security import (
    AllowedToolsPolicy,
    RuntimeMode,
)
from prismclaw.schema.agent.security_store import (
    AggregateStoreProtocol,
    BreakGlassStoreProtocol,
    ConfirmationChannelProtocol,
    ConsentStoreProtocol,
    WormAuditProtocol,
)


# ── actor_identity ──────────────────────────────────────────────────────────

ActorIdentity = Literal["main", "subagent"]


# ── runtime_context ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskRuntimeContext:
    """Strongly-typed context for a single parallel task execution."""

    parallel_task_id: str | None = None
    managed_task_id: str | None = None
    is_fork: bool = False
    handoff_chain_id: str | None = None
    handoff_hop_index: int | None = None
    effective_hop_index: int | None = None
    progress_batch_id: str | None = None
    source_skill_origin: str | None = None
    source_preload_skills: tuple[str, ...] = ()
    merged_from_chains: tuple[str, ...] = ()


class SubagentProgressSink(Protocol):
    """Protocol for progress event emission (optional, may be None)."""

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...


@dataclass
class ParallelTaskExtras:
    """Mutable extras container for parallel task execution.

    This replaces the untyped ``extras`` dict pattern with explicit,
    well-defined attributes for task-level state.
    """

    task_runtime: TaskRuntimeContext = field(default_factory=TaskRuntimeContext)
    progress_emitter: SubagentProgressSink | None = None
    parallel_task_store: Any = None


# ── stubs (import Protocol base from contracts) ────────────────────────────

class InMemoryAggregateStore(AggregateStoreProtocol):
    """内存计数 SessionAggregateGuard stub。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, int]] = {}

    def increment(self, session_id: str, counter: str, delta: int = 1) -> int:
        if session_id not in self._data:
            self._data[session_id] = {}
        self._data[session_id][counter] = self._data[session_id].get(counter, 0) + delta
        return self._data[session_id][counter]

    def get(self, session_id: str, counter: str) -> int:
        return self._data.get(session_id, {}).get(counter, 0)

    def reset(self, session_id: str) -> None:
        self._data.pop(session_id, None)

    def check_and_incr(self, session_id: str, counter: str, threshold: int, window_seconds: int, delta: int = 1) -> tuple[int, bool]:
        new_count = self.increment(session_id, counter, delta)
        return new_count, new_count > threshold


@dataclass
class _BreakGlassToken:
    token: str
    session_id: str
    reason: str
    expires_at: float
    revoked: bool = False


class NullBreakGlassStore(BreakGlassStoreProtocol):
    """Break-Glass stub：token 功能可用但无持久化。"""

    def __init__(self) -> None:
        self._tokens: dict[str, _BreakGlassToken] = {}

    def create_token(self, session_id: str, reason: str, duration_seconds: int = 3600) -> str:
        token = f"bg-{secrets.token_urlsafe(16)}"
        self._tokens[token] = _BreakGlassToken(
            token=token,
            session_id=session_id,
            reason=reason,
            expires_at=time.time() + duration_seconds,
        )
        return token

    def validate_token(self, token: str) -> dict[str, Any] | None:
        t = self._tokens.get(token)
        if t is None or t.revoked or time.time() > t.expires_at:
            return None
        return {"session_id": t.session_id, "reason": t.reason, "expires_at": t.expires_at}

    def revoke_token(self, token: str) -> None:
        t = self._tokens.get(token)
        if t:
            t.revoked = True


class InMemoryWormAudit(WormAuditProtocol):
    """WORM 审计 stub：内存日志，带链哈希完整性校验。"""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def log(self, session_id: str, event_type: str, payload: dict[str, Any]) -> str:
        import hashlib
        import json

        prev_hash = self._entries[-1]["hash"] if self._entries else ""
        entry = {
            "event_id": f"evt-{len(self._entries)+1:06d}",
            "timestamp": time.time(),
            "session_id": session_id,
            "event_type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        self._entries.append(entry)
        return entry["event_id"]

    def query(self, session_id: str, since: str | None = None) -> list[dict[str, Any]]:
        results = [e for e in self._entries if e["session_id"] == session_id]
        if since is not None:
            since_ts = float(since) if since.replace(".", "").isdigit() else 0
            results = [e for e in results if e["timestamp"] >= since_ts]
        return results

    def verify_integrity(self, event_id: str) -> bool:
        entries = sorted(self._entries, key=lambda e: e["timestamp"])
        import hashlib
        import json

        for i, entry in enumerate(entries):
            expected_prev = entries[i - 1]["hash"] if i > 0 else ""
            if entry["prev_hash"] != expected_prev:
                return False
            computed = hashlib.sha256(json.dumps({
                k: v for k, v in entry.items() if k != "hash"
            }, sort_keys=True).encode()).hexdigest()
            if computed != entry["hash"]:
                return False
            if entry["event_id"] == event_id:
                return True
        return event_id == ""


class StubConfirmationChannel(ConfirmationChannelProtocol):
    """确认通道 stub：自动批准（Phase 2 改为 UI 确认）。"""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}

    def request(self, session_id: str, reason: str, payload: dict[str, Any]) -> str:
        request_id = f"conf-{secrets.token_urlsafe(8)}"
        self._pending[request_id] = {
            "session_id": session_id,
            "reason": reason,
            "payload": payload,
            "result": "approved",  # stub: auto-approve
        }
        return request_id

    def check(self, request_id: str) -> str:
        return self._pending.get(request_id, {}).get("result", "rejected")
