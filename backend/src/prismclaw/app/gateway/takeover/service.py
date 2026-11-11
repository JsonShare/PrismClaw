from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
from pathlib import Path
import time
from typing import Any

from prismclaw.schema.session.types import CustomEntry, generate_entry_id, now_iso
from prismclaw.runtime.facade.execution import RunRequest, run_agent_stream
from prismclaw.runtime.facade.execution import (
    evaluate_resume_decision,
    load_run_snapshot_for_tree,
    recovery_probe_required,
    is_recovery_probe_resumable,
)
from prismclaw.schema.session.event_logger import SessionEventLogger

logger = logging.getLogger(__name__)
_event_logger = SessionEventLogger()

_AUTO_TAKEOVER_BLOCK_SAMPLE_LIMIT = 10
_RUN_TAKEOVER_STATE_CUSTOM_TYPE = "run_takeover_state"
_TAKEOVER_REASON_POLICY: dict[str, dict[str, Any]] = {
    "eligible": {
        "category": "admission",
        "retryable": False,
        "operator_action": "Run is eligible for takeover.",
    },
    "app_state_unavailable": {
        "category": "runtime",
        "retryable": True,
        "operator_action": "Wait for app startup to complete or investigate shutdown/reload state.",
    },
    "takeover_runtime_unavailable": {
        "category": "runtime",
        "retryable": True,
        "operator_action": "Restore run ownership store and session store before retrying takeover.",
    },
    "ownership_not_found": {
        "category": "ownership",
        "retryable": True,
        "operator_action": "Confirm the run is still active and inspect ownership TTL before retrying.",
    },
    "ownership_missing_session_id": {
        "category": "ownership",
        "retryable": False,
        "operator_action": "Treat as corrupt ownership data and inspect ownership/snapshot write path.",
    },
    "session_tree_not_found": {
        "category": "session_data",
        "retryable": False,
        "operator_action": "Restore the session tree or recover the backing session store before takeover.",
    },
    "snapshot_run_id_mismatch": {
        "category": "snapshot_consistency",
        "retryable": False,
        "operator_action": "Investigate ownership/snapshot drift before allowing any takeover.",
    },
    "owner_still_active": {
        "category": "ownership",
        "retryable": True,
        "operator_action": "Do not take over while the previous owner is healthy; wait for stale/dead/missing.",
    },
    "resume_not_auto_resumable": {
        "category": "resume_policy",
        "retryable": False,
        "operator_action": "Inspect resume_disposition and resume_reason_code; human intervention may be required.",
    },
    "recovery_context_missing": {
        "category": "resume_context",
        "retryable": False,
        "operator_action": "Inspect snapshot payload and restore missing task/review context before takeover.",
    },
    "takeover_task_running": {
        "category": "coordination",
        "retryable": True,
        "operator_action": "Inspect current takeover task and avoid duplicate trigger while it is still running.",
    },
    "spawn_failed": {
        "category": "task_runtime",
        "retryable": True,
        "operator_action": "Inspect task manager, workspace resolution, and runtime logs before retrying.",
    },
}


def _require_runtime_sandbox_policy(state: Any) -> Any:
    sandbox_policy = getattr(state, "sandbox_policy", None)
    if sandbox_policy is None:
        raise RuntimeError(
            "takeover recovery requires app state sandbox_policy to be resolved"
        )
    return sandbox_policy


def worker_status_map(workers: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(item.get("id")): str(item.get("status"))
        for item in workers
        if isinstance(item, dict) and item.get("id") is not None
    }


def session_user_id(session_id: str) -> str | None:
    if not session_id.startswith("web:"):
        return None
    parts = session_id.split(":")
    if len(parts) < 2:
        return None
    user_id = parts[1].strip()
    return user_id or None


def resolve_takeover_workspace_path(state: Any, *, session_id: str, workspace_id: str) -> Path:
    workspace_base = getattr(state, "workspace_base", None)
    if not isinstance(workspace_base, Path):
        raise RuntimeError("workspace_base unavailable")
    user_id = session_user_id(session_id)
    if user_id is not None:
        from prismclaw.app.web.chat.chat_runner import _resolve_tool_workspace_path

        settings = getattr(state, "web_settings", None)
        if settings is None:
            raise RuntimeError("web settings unavailable")
        return _resolve_tool_workspace_path(
            workspace_base,
            user_id=user_id,
            workspace_id=workspace_id,
            settings=settings,
        )
    return workspace_base / workspace_id


def _parse_takeover_task_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("takeover:"):
        return None
    parts = name.split(":", 2)
    if len(parts) == 2:
        return ("manual", parts[1])
    if len(parts) == 3:
        return (parts[1] or "manual", parts[2])
    return None


def takeover_task_name(run_id: str, *, trigger_mode: str) -> str:
    return f"takeover:{trigger_mode}:{run_id}"


def auto_takeover_status_snapshot(*, enabled: bool, task_id: str | None = None) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "task_id": task_id,
        "last_scan_at_epoch_ms": None,
        "last_scan_duration_ms": None,
        "last_error": None,
        "runs_considered": 0,
        "runs_started": 0,
        "runs_assigned_to_other_workers": 0,
        "blocked_reason_counts": {},
        "blocked_samples": [],
        "started_runs": [],
    }


def takeover_reason_policy(reason_code: str) -> dict[str, Any]:
    base = _TAKEOVER_REASON_POLICY.get(
        reason_code,
        {
            "category": "unknown",
            "retryable": False,
            "operator_action": "Investigate the runtime state before retrying takeover.",
        },
    )
    return {"reason_code": reason_code, **base}


def takeover_reason_catalog() -> list[dict[str, Any]]:
    return [
        takeover_reason_policy(reason_code)
        for reason_code in sorted(_TAKEOVER_REASON_POLICY.keys())
        if reason_code != "eligible"
    ]


def _with_reason_policy(payload: dict[str, Any]) -> dict[str, Any]:
    reason_code = str(payload.get("reason_code") or "unknown")
    return {**payload, "reason_policy": takeover_reason_policy(reason_code)}


async def _persist_takeover_state(
    state: Any,
    *,
    run_id: str,
    session_id: str,
    status: str,
    trigger_mode: str,
    recovery_reason: str,
    task_id: str | None = None,
    owner_worker_id: str | None = None,
    previous_owner_worker_id: str | None = None,
    previous_owner_status: str | None = None,
    resume_from_boundary: str | None = None,
    terminated_by: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    session_store = getattr(state, "session_store", None)
    if session_store is None or not session_id or not run_id:
        return None
    tree = await session_store.load(session_id)
    if tree is None:
        return None
    payload = {
        "run_id": run_id,
        "session_id": session_id,
        "status": status,
        "trigger_mode": trigger_mode,
        "recovery_reason": recovery_reason,
        "task_id": task_id,
        "owner_worker_id": owner_worker_id,
        "previous_owner_worker_id": previous_owner_worker_id,
        "previous_owner_status": previous_owner_status,
        "resume_from_boundary": resume_from_boundary,
        "terminated_by": terminated_by,
        "error": error,
        "recorded_at_epoch_ms": int(time.time() * 1000),
    }
    entry = CustomEntry(
        id=generate_entry_id(set(getattr(tree, "entries", {}).keys())),
        parent_id=getattr(tree, "leaf_id", None),
        timestamp=now_iso(),
        custom_type=_RUN_TAKEOVER_STATE_CUSTOM_TYPE,
        data=payload,
    )
    event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
    await session_store.commit_events([event])
    return payload


def latest_persisted_takeover_state(tree: Any, run_id: str) -> dict[str, Any] | None:
    get_branch = getattr(tree, "get_branch", None)
    if not callable(get_branch):
        return None
    for entry in reversed(get_branch()):
        if getattr(entry, "type", None) != "custom":
            continue
        if getattr(entry, "custom_type", None) != _RUN_TAKEOVER_STATE_CUSTOM_TYPE:
            continue
        data = getattr(entry, "data", None)
        if not isinstance(data, dict):
            continue
        if str(data.get("run_id") or "") != run_id:
            continue
        return dict(data)
    return None


def persisted_takeover_history(tree: Any, run_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    get_branch = getattr(tree, "get_branch", None)
    if not callable(get_branch):
        return []
    items: list[dict[str, Any]] = []
    for entry in reversed(get_branch()):
        if getattr(entry, "type", None) != "custom":
            continue
        if getattr(entry, "custom_type", None) != _RUN_TAKEOVER_STATE_CUSTOM_TYPE:
            continue
        data = getattr(entry, "data", None)
        if not isinstance(data, dict):
            continue
        if str(data.get("run_id") or "") != run_id:
            continue
        items.append(dict(data))
        if len(items) >= max(1, int(limit)):
            break
    return items


async def latest_persisted_takeover_states_for_ownerships(
    state: Any,
    ownerships: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    session_store = getattr(state, "session_store", None)
    if session_store is None:
        return {}
    by_run_id: dict[str, dict[str, Any]] = {}
    for ownership in ownerships:
        run_id = str(ownership.get("run_id") or "")
        session_id = str(ownership.get("session_id") or "")
        if not run_id or not session_id:
            continue
        try:
            tree = await session_store.load(session_id)
        except Exception:
            logger.warning("failed to load session tree for takeover state run_id=%s", run_id, exc_info=True)
            continue
        if tree is None:
            continue
        latest = latest_persisted_takeover_state(tree, run_id)
        if latest is not None:
            by_run_id[run_id] = latest
    return by_run_id


async def persisted_takeover_histories_for_ownerships(
    state: Any,
    ownerships: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    session_store = getattr(state, "session_store", None)
    if session_store is None:
        return {}
    by_run_id: dict[str, list[dict[str, Any]]] = {}
    for ownership in ownerships:
        run_id = str(ownership.get("run_id") or "")
        session_id = str(ownership.get("session_id") or "")
        if not run_id or not session_id:
            continue
        try:
            tree = await session_store.load(session_id)
        except Exception:
            logger.warning(
                "failed to load session tree for takeover history run_id=%s",
                run_id,
                exc_info=True,
            )
            continue
        if tree is None:
            continue
        history = persisted_takeover_history(tree, run_id, limit=limit)
        if history:
            by_run_id[run_id] = history
    return by_run_id


def serialize_takeover_task(task: Any) -> dict[str, Any] | None:
    name = str(getattr(task, "name", "") or "")
    parsed = _parse_takeover_task_name(name)
    if parsed is None:
        return None
    trigger_mode, run_id = parsed
    return {
        "task_id": str(getattr(task, "task_id", "") or ""),
        "run_id": run_id,
        "name": name,
        "trigger_mode": trigger_mode,
        "state": str(getattr(task, "state", "unknown") or "unknown"),
        "owner": getattr(task, "owner", None),
        "duration_s": float(getattr(task, "duration_s", 0.0) or 0.0),
        "exception": getattr(task, "exception", None),
    }


def cluster_takeover_tasks(task_manager: Any) -> list[dict[str, Any]]:
    if task_manager is None:
        return []
    list_tasks = getattr(task_manager, "list_tasks", None)
    if not callable(list_tasks):
        return []
    items: list[dict[str, Any]] = []
    try:
        for task in list_tasks(category="generic", include_done=True):
            payload = serialize_takeover_task(task)
            if payload is not None:
                items.append(payload)
    except Exception:
        logger.warning("failed to inspect takeover tasks", exc_info=True)
        return []
    items.sort(key=lambda item: item["duration_s"], reverse=True)
    return items


def takeover_task_running(task_manager: Any, run_id: str) -> bool:
    list_tasks = getattr(task_manager, "list_tasks", None)
    if not callable(list_tasks):
        return False
    try:
        for task in list_tasks(category="generic", include_done=False):
            payload = serialize_takeover_task(task)
            if payload is not None and payload.get("run_id") == run_id:
                return True
    except Exception:
        logger.warning("failed to inspect running takeover tasks", exc_info=True)
    return False


def _update_auto_takeover_status(state: Any, **fields: Any) -> dict[str, Any]:
    current = getattr(state, "auto_takeover_status", None)
    if not isinstance(current, dict):
        current = auto_takeover_status_snapshot(
            enabled=bool(getattr(state, "auto_takeover_task_id", None)),
            task_id=getattr(state, "auto_takeover_task_id", None),
        )
    updated = dict(current)
    updated.update(fields)
    state.auto_takeover_status = updated
    return updated


def _append_blocked_sample(
    samples: list[dict[str, Any]],
    *,
    run_id: str,
    reason_code: str,
    detail: dict[str, Any],
) -> None:
    if len(samples) >= _AUTO_TAKEOVER_BLOCK_SAMPLE_LIMIT:
        return
    payload = {"run_id": run_id, "reason_code": reason_code}
    for key in (
        "reason_message",
        "previous_owner_worker_id",
        "previous_owner_status",
        "resume_reason_code",
        "resume_reason_message",
    ):
        value = detail.get(key)
        if value is not None:
            payload[key] = value
    samples.append(payload)


def _enum_value_or_str(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


async def evaluate_takeover_eligibility(
    state: Any,
    run_id: str,
    *,
    workers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if state is None:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "app_state_unavailable",
            "reason_message": "Application state is unavailable.",
            "http_status": 503,
            "run_id": run_id,
        })
    run_ownership_store = getattr(state, "run_ownership_store", None)
    session_store = getattr(state, "session_store", None)
    worker_registry = getattr(state, "worker_registry", None)
    if run_ownership_store is None or session_store is None:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "takeover_runtime_unavailable",
            "reason_message": "Run ownership store or session store is unavailable.",
            "http_status": 503,
            "run_id": run_id,
        })

    ownership = await run_ownership_store.get(run_id)
    if ownership is None:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "ownership_not_found",
            "reason_message": "Run ownership record does not exist.",
            "http_status": 404,
            "run_id": run_id,
        })
    if not ownership.session_id:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "ownership_missing_session_id",
            "reason_message": "Run ownership record does not carry session_id and cannot be resumed safely.",
            "http_status": 409,
            "run_id": run_id,
            "session_id": None,
            "previous_owner_worker_id": ownership.owner_worker_id,
        })

    tree = await session_store.load(ownership.session_id)
    if tree is None:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "session_tree_not_found",
            "reason_message": "Session tree referenced by the ownership record could not be loaded.",
            "http_status": 404,
            "run_id": run_id,
            "session_id": ownership.session_id,
            "previous_owner_worker_id": ownership.owner_worker_id,
        })
    snapshot = load_run_snapshot_for_tree(tree)
    if snapshot.run_state.run_id != run_id:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "snapshot_run_id_mismatch",
            "reason_message": "Latest session snapshot does not match the requested run_id.",
            "http_status": 409,
            "run_id": run_id,
            "session_id": ownership.session_id,
            "previous_owner_worker_id": ownership.owner_worker_id,
            "snapshot_run_id": snapshot.run_state.run_id,
        })

    if workers is None:
        workers = await worker_registry.active_workers() if worker_registry else []
    owner_status = worker_status_map(workers).get(ownership.owner_worker_id, "missing")
    if owner_status not in {"stale", "dead", "missing"}:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "owner_still_active",
            "reason_message": "Current run owner is still healthy enough and must not be taken over.",
            "http_status": 409,
            "run_id": run_id,
            "session_id": ownership.session_id,
            "previous_owner_worker_id": ownership.owner_worker_id,
            "previous_owner_status": owner_status,
        })

    resume_decision = evaluate_resume_decision(snapshot)
    if not is_recovery_probe_resumable(resume_decision):
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "resume_not_recovery_resumable",
            "reason_message": "Latest snapshot cannot enter recovery probe based resume/takeover.",
            "http_status": 409,
            "run_id": run_id,
            "session_id": ownership.session_id,
            "previous_owner_worker_id": ownership.owner_worker_id,
            "previous_owner_status": owner_status,
            "resume_disposition": resume_decision.disposition.value,
            "resume_reason_code": resume_decision.reason_code,
            "resume_reason_message": resume_decision.human_readable_reason,
        })

    if snapshot.active_context.task_spec is None or snapshot.active_context.review_requirements is None:
        return _with_reason_policy({
            "eligible": False,
            "reason_code": "recovery_context_missing",
            "reason_message": "Snapshot is missing task or review requirements context required for recovery.",
            "http_status": 409,
            "run_id": run_id,
            "session_id": ownership.session_id,
            "previous_owner_worker_id": ownership.owner_worker_id,
            "previous_owner_status": owner_status,
            "resume_disposition": resume_decision.disposition.value,
            "resume_reason_code": resume_decision.reason_code,
        })

    return _with_reason_policy({
        "eligible": True,
        "reason_code": "eligible",
        "reason_message": "Run is eligible for takeover.",
        "http_status": 200,
        "run_id": run_id,
        "session_id": ownership.session_id,
        "workspace_id": snapshot.run_state.workspace_id or "default",
        "previous_owner_worker_id": ownership.owner_worker_id,
        "previous_owner_status": owner_status,
        "snapshot_cursor": ownership.snapshot_cursor,
        "last_safe_boundary": _enum_value_or_str(ownership.last_safe_boundary),
        "resume_disposition": resume_decision.disposition.value,
        "resume_reason_code": resume_decision.reason_code,
        "resume_reason_message": resume_decision.human_readable_reason,
        "recovery_probe_required": recovery_probe_required(resume_decision),
        "resume_from_boundary": (
            resume_decision.resume_from_boundary.value
            if resume_decision.resume_from_boundary is not None
            else None
        ),
    })


def preferred_takeover_worker(run_id: str, healthy_workers: list[str]) -> str | None:
    candidates = sorted(worker_id for worker_id in healthy_workers if worker_id)
    if not candidates:
        return None
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(candidates)
    return candidates[index]


async def consume_takeover_recovery(
    run_request: RunRequest,
    *,
    state: Any,
    deps: Any,
    tool_workspace_path: Path,
    trigger_mode: str,
) -> None:
    terminated_by = "unknown"
    try:
        async for event in run_agent_stream(run_request, deps, tool_workspace_path=tool_workspace_path):
            if hasattr(event, "type"):
                terminated_by = str(getattr(event, "type"))
        await _persist_takeover_state(
            state,
            run_id=str(run_request.recovery_run_id or run_request.request_id or ""),
            session_id=run_request.session_id,
            status="completed",
            trigger_mode=trigger_mode,
            recovery_reason=run_request.recovery_reason or "unknown",
            owner_worker_id=str(getattr(getattr(state, "worker_registry", None), "worker_id", "") or ""),
            previous_owner_worker_id=run_request.recovery_owner_worker_id,
            resume_from_boundary=None,
            terminated_by=terminated_by,
        )
    except Exception:
        await _persist_takeover_state(
            state,
            run_id=str(run_request.recovery_run_id or run_request.request_id or ""),
            session_id=run_request.session_id,
            status="failed",
            trigger_mode=trigger_mode,
            recovery_reason=run_request.recovery_reason or "unknown",
            owner_worker_id=str(getattr(getattr(state, "worker_registry", None), "worker_id", "") or ""),
            previous_owner_worker_id=run_request.recovery_owner_worker_id,
            error="takeover_recovery_failed",
        )
        logger.exception(
            "%s takeover recovery failed run_id=%s",
            trigger_mode,
            run_request.recovery_run_id,
        )
        raise
    logger.info(
        "%s takeover recovery finished run_id=%s terminated_by=%s",
        trigger_mode,
        run_request.recovery_run_id,
        terminated_by,
    )


async def spawn_takeover_recovery(
    state: Any,
    eligibility: dict[str, Any],
    *,
    trigger_mode: str,
    recovery_reason: str,
) -> dict[str, Any]:
    runner_deps = getattr(state, "runner_deps", None)
    task_manager = getattr(state, "task_manager", None)
    worker_registry = getattr(state, "worker_registry", None)
    if runner_deps is None or task_manager is None:
        raise RuntimeError("takeover runtime unavailable")
    run_id = str(eligibility.get("run_id") or "")
    if not run_id:
        raise RuntimeError("run_id unavailable for takeover")
    if takeover_task_running(task_manager, run_id):
        raise RuntimeError(f"takeover task already running for run_id={run_id}")
    workspace_id = str(eligibility.get("workspace_id") or "default")
    tool_workspace_path = resolve_takeover_workspace_path(
        state,
        session_id=str(eligibility.get("session_id")),
        workspace_id=workspace_id,
    )
    deps = runner_deps
    if dataclasses.is_dataclass(runner_deps) and not isinstance(runner_deps, type):
        deps = dataclasses.replace(runner_deps)
    recovery_request = RunRequest(
        session_id=str(eligibility.get("session_id")),
        workspace_id=workspace_id,
        agent_id="default",
        user_message="",
        sandbox_policy=_require_runtime_sandbox_policy(state),
        request_id=run_id,
        recovery_run_id=run_id,
        recovery_owner_worker_id=str(eligibility.get("previous_owner_worker_id")),
        recovery_reason=recovery_reason,
    )
    task_id = task_manager.spawn(
        takeover_task_name(run_id, trigger_mode=trigger_mode),
        consume_takeover_recovery(
            recovery_request,
            state=state,
            deps=deps,
            tool_workspace_path=tool_workspace_path,
            trigger_mode=trigger_mode,
        ),
        category="generic",
        owner=getattr(worker_registry, "worker_id", None),
    )
    payload = {
        "status": "started",
        "task_id": task_id,
        "run_id": run_id,
        "session_id": eligibility.get("session_id"),
        "previous_owner_worker_id": eligibility.get("previous_owner_worker_id"),
        "previous_owner_status": eligibility.get("previous_owner_status"),
        "resume_from_boundary": eligibility.get("resume_from_boundary"),
        "trigger_mode": trigger_mode,
    }
    persisted = await _persist_takeover_state(
        state,
        run_id=run_id,
        session_id=str(eligibility.get("session_id") or ""),
        status="started",
        trigger_mode=trigger_mode,
        recovery_reason=recovery_reason,
        task_id=str(task_id),
        owner_worker_id=str(getattr(worker_registry, "worker_id", "") or ""),
        previous_owner_worker_id=str(eligibility.get("previous_owner_worker_id") or ""),
        previous_owner_status=str(eligibility.get("previous_owner_status") or ""),
        resume_from_boundary=str(eligibility.get("resume_from_boundary") or "") or None,
    )
    if persisted is not None:
        payload["latest_persisted_state"] = persisted
    return payload


async def auto_takeover_once(state: Any) -> list[dict[str, Any]]:
    run_ownership_store = getattr(state, "run_ownership_store", None)
    worker_registry = getattr(state, "worker_registry", None)
    task_manager = getattr(state, "task_manager", None)
    if run_ownership_store is None or worker_registry is None or task_manager is None:
        _update_auto_takeover_status(
            state,
            enabled=False,
            task_id=getattr(state, "auto_takeover_task_id", None),
            last_error="takeover runtime unavailable",
        )
        return []
    if not getattr(worker_registry, "available", False):
        _update_auto_takeover_status(
            state,
            enabled=False,
            task_id=getattr(state, "auto_takeover_task_id", None),
            last_error="worker registry unavailable",
        )
        return []
    current_worker = str(getattr(worker_registry, "worker_id", "") or "")
    if not current_worker:
        _update_auto_takeover_status(
            state,
            enabled=False,
            task_id=getattr(state, "auto_takeover_task_id", None),
            last_error="current worker id unavailable",
        )
        return []
    scan_started_at_ms = int(time.time() * 1000)
    workers = await worker_registry.active_workers()
    healthy_workers = [
        str(item.get("id"))
        for item in workers
        if isinstance(item, dict) and item.get("status") == "healthy" and item.get("id")
    ]
    if current_worker not in healthy_workers:
        healthy_workers.append(current_worker)
    affinity_settings = getattr(getattr(state, "settings", None), "affinity", None)
    stale_threshold = (
        float(getattr(affinity_settings, "stale_threshold", 0.0) or 0.0)
        if affinity_settings is not None
        else 0.0
    )
    max_age_seconds = max(
        float(getattr(run_ownership_store, "ttl_seconds", 0) or 0),
        stale_threshold,
    )
    records = await run_ownership_store.list_active(max_age_seconds=max_age_seconds)
    started: list[dict[str, Any]] = []
    blocked_reason_counts: dict[str, int] = {}
    blocked_samples: list[dict[str, Any]] = []
    assigned_to_other_workers = 0
    for record in records:
        run_id = str(getattr(record, "run_id", "") or "")
        if not run_id:
            continue
        if takeover_task_running(task_manager, run_id):
            blocked_reason_counts["takeover_task_running"] = (
                blocked_reason_counts.get("takeover_task_running", 0) + 1
            )
            _append_blocked_sample(
                blocked_samples,
                run_id=run_id,
                reason_code="takeover_task_running",
                detail={"reason_message": "Takeover task already running for this run."},
            )
            continue
        preferred_worker = preferred_takeover_worker(run_id, healthy_workers)
        if preferred_worker != current_worker:
            assigned_to_other_workers += 1
            continue
        eligibility = await evaluate_takeover_eligibility(state, run_id, workers=workers)
        if not eligibility.get("eligible"):
            reason_code = str(eligibility.get("reason_code") or "unknown")
            blocked_reason_counts[reason_code] = blocked_reason_counts.get(reason_code, 0) + 1
            _append_blocked_sample(
                blocked_samples,
                run_id=run_id,
                reason_code=reason_code,
                detail=eligibility,
            )
            continue
        try:
            payload = await spawn_takeover_recovery(
                state,
                eligibility,
                trigger_mode="auto",
                recovery_reason="automatic_takeover",
            )
        except Exception:
            logger.warning("automatic takeover spawn failed for run_id=%s", run_id, exc_info=True)
            blocked_reason_counts["spawn_failed"] = blocked_reason_counts.get("spawn_failed", 0) + 1
            _append_blocked_sample(
                blocked_samples,
                run_id=run_id,
                reason_code="spawn_failed",
                detail={"reason_message": "Automatic takeover task spawn failed."},
            )
            continue
        started.append(payload)
    _update_auto_takeover_status(
        state,
        enabled=bool(getattr(state, "auto_takeover_task_id", None)),
        task_id=getattr(state, "auto_takeover_task_id", None),
        last_scan_at_epoch_ms=scan_started_at_ms,
        last_scan_duration_ms=max(0, int(time.time() * 1000) - scan_started_at_ms),
        last_error=None,
        runs_considered=len(records),
        runs_started=len(started),
        runs_assigned_to_other_workers=assigned_to_other_workers,
        blocked_reason_counts=blocked_reason_counts,
        blocked_samples=blocked_samples,
        started_runs=[
            {
                "run_id": item.get("run_id"),
                "task_id": item.get("task_id"),
                "previous_owner_worker_id": item.get("previous_owner_worker_id"),
                "previous_owner_status": item.get("previous_owner_status"),
            }
            for item in started
        ],
    )
    return started


async def auto_takeover_loop(state: Any, *, interval_s: float = 10.0) -> None:
    try:
        _update_auto_takeover_status(
            state,
            enabled=True,
            task_id=getattr(state, "auto_takeover_task_id", None),
            last_error=None,
        )
        while True:
            try:
                started = await auto_takeover_once(state)
                for item in started:
                    logger.info(
                        "automatic takeover started run_id=%s task_id=%s owner=%s",
                        item.get("run_id"),
                        item.get("task_id"),
                        item.get("previous_owner_worker_id"),
                    )
            except Exception:
                _update_auto_takeover_status(
                    state,
                    enabled=True,
                    task_id=getattr(state, "auto_takeover_task_id", None),
                    last_error="automatic takeover scan failed",
                )
                logger.warning("automatic takeover scan failed", exc_info=True)
            await asyncio.sleep(max(1.0, float(interval_s)))
    except asyncio.CancelledError:
        return
