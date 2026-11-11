"""Scheduled tasks API router."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.deps import get_web_deps
from prismclaw.schema.scheduler import ScheduledTask, ScheduledTaskRun, make_run_id
from prismclaw.schema.shared.cron_convert import cron_to_spec, spec_to_cron

scheduler_router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduler"])


# ── Request / Response schemas ─────────────────────────────────────────


class CreateTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=5000)
    schedule: dict  # ScheduleSpec from frontend
    workspace_id: str | None = None  # None = 从 request query/header 取；再兜底 "default"
    missed_policy: str = "skip"
    max_age_days: int = Field(default=30, ge=0, le=365)


class UpdateTaskRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    instruction: str | None = Field(default=None, min_length=1, max_length=5000)
    schedule: dict | None = None
    enabled: bool | None = None
    missed_policy: str | None = None
    max_age_days: int | None = Field(default=None, ge=0, le=365)
    workspace_id: str | None = None


def _resolve_workspace_id(request: Request, body_wid: str | None) -> str:
    """Resolve workspace_id for scheduled task creation/update.

    Precedence: explicit body field > query param > header > "default".
    Frontend may omit workspace_id when the user hasn't explicitly picked one,
    so we need multiple fallbacks instead of hardcoding "default" everywhere.
    """
    if body_wid:
        return body_wid
    for source in (request.query_params.get("workspace_id"), request.headers.get("x-workspace-id")):
        if source and source.strip():
            return source.strip()
    return "default"


class TaskResponse(BaseModel):
    id: str
    name: str
    instruction: str
    schedule: dict
    cron_expr: str
    workspace_id: str
    enabled: bool
    last_fired_at: float | None
    last_status: str
    consecutive_failures: int
    max_consecutive_failures: int
    conversation_id: str | None
    created_at: float
    max_age_days: int
    missed_policy: str


class RunResponse(BaseModel):
    id: str
    task_id: str
    fired_at: float
    started_at: float | None
    finished_at: float | None
    status: str
    session_id: str | None
    error: str | None
    duration_s: float | None


# ── Helpers ────────────────────────────────────────────────────────────


def _get_store(request: Request):
    web_deps = get_web_deps(request)
    store = web_deps.scheduler_store
    if store is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    return store


def _get_settings(request: Request):
    web_deps = get_web_deps(request)
    if web_deps.settings_full is not None:
        return web_deps.settings_full.scheduler
    return None


async def _invalidate_cache(app, task_id: str) -> None:
    """Invalidate the scheduler next-fire cache for a task, across workers.

    Local: pop the entry on this process's engine. Cross-worker (cloud): publish
    on INVALIDATE_CHANNEL so the leader — possibly another worker — reconciles.
    Mirrors the dialogue tool's invalidation path; both creation modes must
    reach the leader the same way.
    """
    web_deps = app.state.web_deps
    engine = web_deps.scheduler_engine
    if engine is not None:
        engine.invalidate_cache(task_id)
    redis = web_deps.redis_client
    if redis is not None:
        from prismclaw.schema.scheduler import INVALIDATE_CHANNEL

        try:
            await redis.publish(INVALIDATE_CHANNEL, task_id)
        except Exception:  # noqa: BLE001 — invalidation is best-effort
            pass


def _task_to_response(task: ScheduledTask, request: Request) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "instruction": task.instruction,
        "schedule": task.schedule_spec or cron_to_spec(task.cron_expr),
        "cron_expr": task.cron_expr,
        "workspace_id": task.workspace_id,
        "enabled": task.enabled,
        "last_fired_at": task.last_fired_at,
        "last_status": task.last_status,
        "consecutive_failures": task.consecutive_failures,
        "max_consecutive_failures": _get_settings(request).max_consecutive_failures,
        "conversation_id": task.conversation_id,
        "created_at": task.created_at,
        "max_age_days": task.max_age_days,
        "missed_policy": task.missed_policy,
    }


def _run_to_response(run: ScheduledTaskRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "task_id": run.task_id,
        "fired_at": run.fired_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "session_id": run.session_id,
        "error": run.error,
        "duration_s": run.duration_s,
    }


# ── Endpoints ──────────────────────────────────────────────────────────


@scheduler_router.get("")
async def list_tasks(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    store = _get_store(request)
    tasks = await store.list_tasks(user_id=user_id, workspace_id=workspace_id)
    return [_task_to_response(t, request) for t in tasks]


@scheduler_router.post("", status_code=201)
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    store = _get_store(request)
    settings = _get_settings(request)

    if await store.count_user_tasks(user_id) >= settings.max_tasks_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {settings.max_tasks_per_user} tasks per user",
        )

    try:
        cron_expr = spec_to_cron(body.schedule)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid schedule: {e}")

    task = ScheduledTask(
        user_id=user_id,
        workspace_id=_resolve_workspace_id(request, body.workspace_id),
        name=body.name.strip(),
        instruction=body.instruction.strip(),
        cron_expr=cron_expr,
        schedule_spec=body.schedule,
        missed_policy=body.missed_policy,  # type: ignore[arg-type]
        max_age_days=body.max_age_days,
        created_at=time.time(),
    )
    await store.put_task(task)

    await _invalidate_cache(request.app, task.id)

    return _task_to_response(task, request)


@scheduler_router.patch("/{task_id}")
async def update_task(
    task_id: str,
    body: UpdateTaskRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    store = _get_store(request)
    task = await store.get_task(task_id)
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.name is not None:
        task.name = body.name.strip()
    if body.instruction is not None:
        task.instruction = body.instruction.strip()
    if body.schedule is not None:
        try:
            task.cron_expr = spec_to_cron(body.schedule)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid schedule: {e}")
        task.schedule_spec = body.schedule
    if body.enabled is not None:
        task.enabled = body.enabled
        if body.enabled:
            task.consecutive_failures = 0
            task.last_status = "idle"
    if body.missed_policy is not None:
        task.missed_policy = body.missed_policy  # type: ignore[assignment]
    if body.max_age_days is not None:
        task.max_age_days = body.max_age_days
    if body.workspace_id is not None:
        task.workspace_id = body.workspace_id

    await store.put_task(task)

    await _invalidate_cache(request.app, task.id)

    return _task_to_response(task, request)


@scheduler_router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> Response:
    store = _get_store(request)
    task = await store.get_task(task_id)
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    await store.delete_task(task_id)

    await _invalidate_cache(request.app, task_id)
    return Response(status_code=204)


@scheduler_router.post("/{task_id}/trigger")
async def trigger_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Manually trigger a task execution (bypasses schedule)."""
    store = _get_store(request)
    task = await store.get_task(task_id)
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    web_deps = get_web_deps(request)
    dispatch_queue = web_deps.scheduler_dispatch_queue
    if dispatch_queue is None:
        raise HTTPException(status_code=503, detail="Scheduler dispatch not available")

    from prismclaw.schema.scheduler import SchedulerDispatchJob

    fire_slot = now = time.time()
    run = ScheduledTaskRun(
        id=make_run_id(task.id, fire_slot),
        task_id=task.id,
        fired_at=now,
        status="pending",
    )
    won = await store.add_run(run, fired_task_id=task.id, fired_at=now)
    if not won:
        raise HTTPException(status_code=409, detail="该触发已存在")

    job = SchedulerDispatchJob(
        run_id=run.id,
        task_id=task.id,
        task_name=task.name,
        instruction=task.instruction,
        user_id=task.user_id,
        workspace_id=task.workspace_id,
        fired_at=now,
    )
    try:
        await dispatch_queue.enqueue(job)
    except Exception:
        await store.update_run(
            run.id, status="failed", error="dispatch_enqueue_failed"
        )
        raise

    return {"run_id": run.id, "status": "pending"}


@scheduler_router.get("/{task_id}/runs")
async def list_runs(
    task_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    store = _get_store(request)
    task = await store.get_task(task_id)
    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    runs = await store.list_runs(task_id, limit=min(limit, 100), offset=offset)
    return [_run_to_response(r) for r in runs]
