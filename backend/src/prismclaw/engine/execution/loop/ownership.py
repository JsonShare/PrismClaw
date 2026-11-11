from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps

from prismclaw.engine.execution.checkpoint import RunSnapshot
from prismclaw.schema.agent.task_runtime import ReviewRequirements
from prismclaw.schema.agent.task_types import TaskSpec

logger = logging.getLogger(__name__)


@dataclass
class _RunOwnershipLease:
    run_id: str
    owner_worker_id: str
    stop_event: asyncio.Event
    task: asyncio.Task[None]


async def _claim_run_ownership(snapshot: RunSnapshot, deps: AgentRunnerDeps) -> _RunOwnershipLease | None:
    store = deps.persistence.run_ownership_store if deps.persistence is not None else None
    run_id = snapshot.run_state.run_id or ""
    owner_worker_id = snapshot.worker_id or getattr(deps, "worker_id", None) or ""
    if store is None or not run_id or not owner_worker_id:
        return None
    snapshot.worker_id = owner_worker_id
    record = store.make_record(
        run_id=run_id,
        session_id=snapshot.run_state.session_id,
        owner_worker_id=owner_worker_id,
        snapshot_cursor=snapshot.snapshot_cursor,
        last_safe_boundary=str(snapshot.last_safe_boundary),
    )
    claimed = await store.claim(record)
    if not claimed:
        existing = await store.get(run_id)
        existing_owner = getattr(existing, "owner_worker_id", "unknown")
        raise RuntimeError(
            f"run ownership conflict for run_id={run_id}: existing_owner={existing_owner}"
        )
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _run_ownership_heartbeat_loop(store, snapshot=snapshot, stop_event=stop_event),
        name=f"run-ownership:{run_id}",
    )
    return _RunOwnershipLease(
        run_id=run_id,
        owner_worker_id=owner_worker_id,
        stop_event=stop_event,
        task=task,
    )


async def _takeover_run_ownership(
    snapshot: RunSnapshot,
    deps: AgentRunnerDeps,
    *,
    expected_owner_worker_id: str,
) -> _RunOwnershipLease | None:
    store = deps.persistence.run_ownership_store if deps.persistence is not None else None
    run_id = snapshot.run_state.run_id or ""
    owner_worker_id = snapshot.worker_id or getattr(deps, "worker_id", None) or ""
    if store is None or not run_id or not owner_worker_id:
        return None
    snapshot.worker_id = owner_worker_id
    record = store.make_record(
        run_id=run_id,
        session_id=snapshot.run_state.session_id,
        owner_worker_id=owner_worker_id,
        snapshot_cursor=snapshot.snapshot_cursor,
        last_safe_boundary=str(snapshot.last_safe_boundary),
    )
    taken = await store.takeover(record, expected_owner_worker_id=expected_owner_worker_id)
    if not taken:
        existing = await store.get(run_id)
        existing_owner = getattr(existing, "owner_worker_id", "unknown")
        raise RuntimeError(
            f"run takeover conflict for run_id={run_id}: existing_owner={existing_owner}"
        )
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _run_ownership_heartbeat_loop(store, snapshot=snapshot, stop_event=stop_event),
        name=f"run-ownership:{run_id}",
    )
    return _RunOwnershipLease(
        run_id=run_id,
        owner_worker_id=owner_worker_id,
        stop_event=stop_event,
        task=task,
    )


async def _run_ownership_heartbeat_loop(store: Any, *, snapshot: RunSnapshot, stop_event: asyncio.Event) -> None:
    interval = max(1, int(getattr(store, "heartbeat_interval_seconds", 30)))
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except TimeoutError:
            try:
                record = store.make_record(
                    run_id=snapshot.run_state.run_id or "",
                    session_id=snapshot.run_state.session_id,
                    owner_worker_id=snapshot.worker_id or "",
                    snapshot_cursor=snapshot.snapshot_cursor,
                    last_safe_boundary=str(snapshot.last_safe_boundary),
                )
                refreshed = await store.heartbeat(record)
                if not refreshed:
                    logger.warning(
                        "run ownership heartbeat lost ownership for run_id=%s owner=%s",
                        snapshot.run_state.run_id,
                        snapshot.worker_id,
                    )
                    return
            except Exception:
                logger.warning(
                    "run ownership heartbeat failed for run_id=%s owner=%s",
                    snapshot.run_state.run_id,
                    snapshot.worker_id,
                    exc_info=True,
                )


async def _release_run_ownership(lease: _RunOwnershipLease | None, deps: AgentRunnerDeps) -> None:
    if lease is None:
        return
    lease.stop_event.set()
    try:
        await lease.task
    except Exception:
        logger.warning("run ownership heartbeat task shutdown failed", exc_info=True)
    store = deps.persistence.run_ownership_store if deps.persistence is not None else None
    if store is None:
        return
    try:
        await store.release(lease.run_id, lease.owner_worker_id)
    except Exception:
        logger.warning(
            "failed to release run ownership for run_id=%s owner=%s",
            lease.run_id,
            lease.owner_worker_id,
            exc_info=True,
        )


def _restore_task_spec_from_snapshot(snapshot: RunSnapshot) -> TaskSpec | None:
    return snapshot.active_context.task_spec


def _restore_review_requirements_from_snapshot(snapshot: RunSnapshot) -> ReviewRequirements | None:
    return snapshot.active_context.review_requirements
