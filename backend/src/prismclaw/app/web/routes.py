from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from prismclaw.app.session_router import SessionRouter
from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.chat._chat_helpers import _build_run_projection_payload
from prismclaw.schema.session.types import (
    CustomEntry,
    EXECUTION_TIMELINE_CUSTOM_TYPE,
    FEEDBACK_CUSTOM_TYPE,
    FILE_CHANGES_CUSTOM_TYPE,
    OUTPUT_FILES_CUSTOM_TYPE,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.storage.storage_types import SessionStore
from prismclaw.schema.services.skills import SkillManifest
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.ids import build_web_session_key

from prismclaw.app.web.routers.files import files_router
from prismclaw.app.web.routers.skills import skills_router
from prismclaw.app.web.routers.speech import speech_router
from prismclaw.app.web.routers.settings import settings_router
from prismclaw.app.web.routers.workspace import workspace_router
from prismclaw.app.web.routers.office import office_router
from prismclaw.app.web.routers.domains import router as domains_router
from prismclaw.app.web.routers.config import config_router
from prismclaw.app.web._routes_helpers import _get_settings_full, _resolve_workspace_root, _clean_workspace_id
from prismclaw.app.web.routers._message_helpers import _build_display_messages
from prismclaw.app.web.deps import get_web_deps

logger = logging.getLogger(__name__)

_event_logger = SessionEventLogger()

web_router = APIRouter(prefix="/api", tags=["web"])

web_router.include_router(files_router)
web_router.include_router(skills_router)
web_router.include_router(speech_router)
web_router.include_router(settings_router)
web_router.include_router(workspace_router)
web_router.include_router(office_router)
web_router.include_router(domains_router)
web_router.include_router(config_router)

# Module-level globals are set once via set_web_deps() at lifespan startup.
# Production-safe (single-process per uvicorn worker), but causes flakiness in
# pytest-xdist parallel test runs. Future: refactor to FastAPI app.state DI.
# Tracked in: harden-self-evolution-extraction (deferred — architectural smell, no production bug).
_store: SessionStore | None = None
_session_router: SessionRouter | None = None
_memory_store: Any = None
_task_manager: Any = None
_redis_client: Any = None
_session_factory: Any = None
_llm_client: Any = None
_evolution_settings: Any = None
_nudge_hook: Any = None
_trajectory_persistence: Any = None

# Upload filtering moved to upload_paths (extension blocklist + noise filter);
# the old MIME/extension whitelist is gone — it rejected legitimate types
# (``.tiff`` / ``.csv`` / ``.heic``) and could not defend against deliberate
# spoofing anyway. See upload_pipeline.upload_files for the single source.


def set_web_deps(
    store: SessionStore,
    session_router: SessionRouter,
    *,
    memory_store: Any = None,
    task_manager: Any = None,
    redis_client: Any = None,
    session_factory: Any = None,
    llm_client: Any = None,
    evolution_settings: Any = None,
    nudge_hook: Any = None,
    trajectory_persistence: Any = None,
) -> None:
    global _store, _session_router, _memory_store, _task_manager
    global _redis_client, _session_factory, _llm_client, _evolution_settings, _nudge_hook
    global _trajectory_persistence
    _store = store
    _session_router = session_router
    _memory_store = memory_store
    _task_manager = task_manager
    _redis_client = redis_client
    _session_factory = session_factory
    _llm_client = llm_client
    _evolution_settings = evolution_settings
    _nudge_hook = nudge_hook
    _trajectory_persistence = trajectory_persistence


def _get_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("Web deps not initialised")
    return _store


def _get_router() -> SessionRouter:
    if _session_router is None:
        raise RuntimeError("Web deps not initialised")
    return _session_router


class SessionListItem(BaseModel):
    id: str
    created_at: str
    message_count: int
    last_interaction_at: str | None
    parent_session_id: str | None
    title: str | None = None
    # 会话来源标签：'normal' = 人工/前端创建；'scheduled' = 由 scheduler 触发。
    # 通过在 list_sessions 中反查 pc_scheduled_task_runs.session_id 填充，
    # 不修改 SessionHeader，避免 schema 迁移。旧会话（无 run 关联）一律回退 'normal'。
    type: Literal["normal", "scheduled"] = "normal"


class CreateSessionResponse(BaseModel):
    session_id: str


class CreateSessionRequest(BaseModel):
    workspace_id: str = "default"
    title: str | None = None


class ExtractResponse(BaseModel):
    spawned: bool
    message: str


class OptimizePromptRequest(BaseModel):
    prompt: str


class OptimizePromptResponse(BaseModel):
    optimized: str


class DefaultModelOptionResponse(BaseModel):
    id: str
    provider: str
    supports_image: bool = False


class SessionModelSettingsResponse(BaseModel):
    session_id: str
    default_model: str
    model_override: str | None = None
    effective_model: str
    available_models: list[DefaultModelOptionResponse]


class SessionModelUpdateRequest(BaseModel):
    model_override: str | None = None


def _available_model_options() -> list[DefaultModelOptionResponse]:
    """可选模型清单 —— 取自模型注册表（backend/configs/models.yaml），只列已启用条目。"""
    from prismclaw.infra.config.models_registry import list_models
    return [
        DefaultModelOptionResponse(
            id=row["model"],
            provider=row["provider"],
            supports_image=bool(row["supports_image"]),
        )
        for row in list_models()
        if row["enabled"]
    ]


async def _get_workspace_runner_deps(app: Any, *, user_id: str, workspace_root: Path) -> Any | None:
    web_deps = app.state.web_deps
    fn = web_deps.workspace_runner_deps_fn
    if fn is not None:
        return await fn(app, user_id=user_id, workspace_root=workspace_root, wait_for_ready=True)
    return None


async def _list_runtime_skills(
    request: Request,
    *,
    user_id: str,
    workspace_root: Path,
    settings_full: Any,
) -> tuple[list[SkillManifest], Any | None]:
    _ = settings_full
    runner_deps = await _get_workspace_runner_deps(
        request.app,
        user_id=user_id,
        workspace_root=workspace_root,
    )
    if runner_deps is None:
        raise RuntimeError(
            "workspace runner deps are required for skill listing"
        )
    extension_manager = getattr(runner_deps, "extension_manager", None) if runner_deps is not None else None
    capability_catalog = (
        getattr(runner_deps, "capability_catalog", None) if runner_deps is not None else None
    )
    if capability_catalog is None:
        raise RuntimeError(
            "workspace runner deps must provide capability_catalog for skill listing"
        )
    return [
        entry.manifest
        for entry in capability_catalog.list_skill_entries(workspace_root)
    ], extension_manager


async def _load_and_verify(session_id: str, user_id: str) -> SessionTree:
    store = _get_store()
    tree = await store.load(session_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not tree.header.session_key.startswith(f"web:{user_id}"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tree


@web_router.get("/conversations/{session_id}/run")
async def get_conversation_run(
    request: Request,
    session_id: str,
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """返回进行中 run 的实时运行态快照，供刷新/重连后水合（hydrateRun）。

    - 入参 session_id 可为裸 conversation_id 或带 web: 前缀的树 id（与
      trajectory 等兄弟端点一致，自动归一化，含 "s:" 前缀回退候选）。
    - 内存判活前置（is_conversation_running：投影细分态 OR RunControl
      生命周期信号——LLM 流式生成阶段投影四字段全空，但 run 仍在进行）：
      非运行中直接 {"run": null}，**不加载树**——判活是纯内存操作，历史/
      已结束/不存在的会话不必为返回 null 支付整棵树加载，也不以 404/403
      泄漏会话存在性（前端对非 200 与 null 同为跳过）。
    - 运行中 → 归属校验 + 树加载（task_runtime 构建需要）后从内存投影
      实时构建 payload 返回，与 WS run.snapshot 共用
      _build_run_projection_payload，零滞后。
    - 树里的 run_projection 条目仅是 flusher 周期落盘的审计记录，不再是本
      端点数据源；陈旧 active 条目不得复活为「第二段执行过程」。
    """
    # Step 0: 归一化 session_id（与 trajectory 等兄弟端点同逻辑）。
    base_session_key = build_web_session_key(user_id, _clean_workspace_id(workspace_id))
    if not session_id.startswith("web:"):
        resolved_session_id = f"{base_session_key}:{session_id}"
    else:
        resolved_session_id = session_id

    # Step 1: 内存判活（含 "s:" 前缀回退候选）——非运行中直接 null。
    web_deps = getattr(request.app.state, "web_deps", None)
    session_queue = getattr(web_deps, "session_queue", None) if web_deps is not None else None
    running_id: str | None = None
    if session_queue is not None:
        candidates = [resolved_session_id]
        if not resolved_session_id.startswith(f"{base_session_key}:s:"):
            candidates.append(
                resolved_session_id.replace(
                    f"{base_session_key}:", f"{base_session_key}:s:", 1,
                )
            )
        running_id = next(
            (c for c in candidates if session_queue.is_conversation_running(c)),
            None,
        )
    if running_id is None:
        return {"run": None}
    resolved_session_id = running_id

    # Step 2: 运行中——归属校验 + 树加载（task_runtime 构建需要），内存
    # 直读构建。构建失败（投影缺失，理论不可达）回落 null，宁缺毋陈旧。
    tree = await _load_and_verify(resolved_session_id, user_id)
    payload = _build_run_projection_payload(tree, session_queue, resolved_session_id)
    if payload is not None:
        return {"run": payload.model_dump(mode="json")}
    return {"run": None}


@web_router.get("/conversations/running")
async def get_running_conversations(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict[str, list[str]]:
    """返回当前用户仍处于活跃执行态的 conversation 列表（用户级，跨工作区）。

    权威源是 app 级 SessionQueue 的内存判活（is_conversation_running：投影
    细分态 OR RunControl 生命周期信号，与 WS 层同一对象）。前端刷新/重连后
    据此逐会话拉取 GET /run 重放运行态。

    此前实现按 ``workspace_id`` 过滤会话历史并逐树扫描 TaskRuntimeState 判活，
    有两个缺陷：
    1. 前端刷新后 ``activeWorkspaceId`` 尚未恢复（初始值 'default'），非默认
       工作区（如「测试A」）的运行中会话被漏出列表 → GET /run 从未被调用 →
       前端不水合 → 「刷新后长任务从 UI 消失」（run 实际仍在后台执行）。
       运行态是用户级事实，不该要求前端先知道工作区才能查询。
    2. 逐树扫描把树内残留的 executing 态（崩溃 run 未写终态）也判为活跃，
       且每会话一次全树加载开销大。
    """
    session_queue = getattr(request.app.state.web_deps, "session_queue", None)
    if session_queue is None:
        return {"conversations": []}
    prefix = f"web:{user_id}:"
    running = [
        cid
        for cid in session_queue.list_running_conversations()
        if cid.startswith(prefix)
    ]
    return {"conversations": running}


@web_router.get("/sessions")
async def list_sessions(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """返回会话列表（分页），消除 N+1 查询。

    - limit：每页数量（默认 10）
    - offset：偏移量（默认 0）
    - 返回格式：{"items": [SessionListItem], "has_more": bool}
    - 每条 item 额外带 ``type`` 字段（``normal`` | ``scheduled``），
      通过反查 ``pc_scheduled_task_runs.session_id`` 填充。
    """
    store = _get_store()
    wid = _clean_workspace_id(workspace_id)
    session_key = build_web_session_key(user_id, wid)

    # 多取 1 条来判断是否还有更多数据
    summaries = await store.list_session_history(session_key, limit=limit + 1, offset=max(offset, 0))
    has_more = len(summaries) > limit
    if has_more:
        summaries = summaries[:limit]

    # 反查：哪些 session 是被 scheduler 触发创建的？
    # 仅在 scheduler 可用时执行；不可用则全部回退 'normal'。
    # 返回 {session_id: task_name}，用于：
    #   1) type 标记（'scheduled'）
    #   2) 历史会话标题兜底——executor 在创建时会写入任务名，但升级前的
    #      旧会话 header.title 仍为空，此处用任务名填充，保证列表与任务名一致。
    scheduled_task_names: dict[str, str] = {}
    try:
        web_deps = get_web_deps(request)
        scheduler_store = getattr(web_deps, "scheduler_store", None)
        if scheduler_store is not None:
            scheduled_task_names = await scheduler_store.list_user_scheduled_session_task_names(
                user_id=user_id, workspace_id=wid,
            )
    except RuntimeError:
        # web_deps 尚未初始化（理论不应在请求路径上发生）
        pass

    items: list[SessionListItem] = []
    for s in summaries:
        title = s.title
        if title is None or not title.strip():
            title = scheduled_task_names.get(s.session_id)
        items.append(
            SessionListItem(
                id=s.session_id,
                created_at=s.created_at,
                message_count=s.message_count,
                last_interaction_at=s.last_message_at,
                parent_session_id=s.parent_session_id,
                title=title,  # 优先 header.title；scheduled 会话缺失时回退任务名
                type="scheduled" if s.session_id in scheduled_task_names else "normal",
            )
        )
    items.reverse()
    return {"items": items, "has_more": has_more}


@web_router.post("/sessions")
async def create_session(
    request: Request,
    user_id: str = Depends(get_current_user),
    body: CreateSessionRequest = CreateSessionRequest(),
) -> CreateSessionResponse:
    router = _get_router()
    wid = _clean_workspace_id(body.workspace_id)
    session_key = build_web_session_key(user_id, wid)
    # 创建会话即写入标题，避免首条消息后的 PATCH 竞态导致列表刷新后回退"新对话"。
    title: str | None = None
    if body.title is not None:
        cleaned = body.title.strip()
        if len(cleaned) > 200:
            raise HTTPException(status_code=400, detail="Title too long (max 200 chars)")
        title = cleaned or None
    session_id, tree = await router.rotate(session_key, wid, title=title)
    return CreateSessionResponse(session_id=session_id)


@web_router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> dict:
    tree = await _load_and_verify(session_id, user_id)
    h = tree.header
    msg_count = len(_build_display_messages(tree))
    return {
        "id": h.id,
        "workspace_id": h.workspace_id,
        "agent_id": h.agent_id,
        "created_at": h.created_at,
        "last_interaction_at": h.last_interaction_at,
        "message_count": msg_count,
    }


@web_router.get("/sessions/{session_id}/model", response_model=SessionModelSettingsResponse)
async def get_session_model_settings(
    request: Request,
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> SessionModelSettingsResponse:
    tree = await _load_and_verify(session_id, user_id)
    settings_full = _get_settings_full(request)
    if settings_full is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    default_model = str(settings_full.models.default_model or "")
    override = (
        tree.header.model_override.strip()
        if isinstance(tree.header.model_override, str) and tree.header.model_override.strip()
        else None
    )
    effective = override or default_model
    return SessionModelSettingsResponse(
        session_id=session_id,
        default_model=default_model,
        model_override=override,
        effective_model=effective,
        available_models=_available_model_options(),
    )


@web_router.put("/sessions/{session_id}/model", response_model=SessionModelSettingsResponse)
async def update_session_model_settings(
    request: Request,
    session_id: str,
    payload: SessionModelUpdateRequest,
    user_id: str = Depends(get_current_user),
) -> SessionModelSettingsResponse:
    from prismclaw.schema.session.types import ModelChangeEntry, generate_entry_id, now_iso

    resolve_provider_for_model = request.app.state.web_deps.resolve_provider_fn

    tree = await _load_and_verify(session_id, user_id)
    settings_full = _get_settings_full(request)
    if settings_full is None:
        raise HTTPException(status_code=500, detail="Settings not configured")

    default_model = str(settings_full.models.default_model or "")
    available_models = _available_model_options()
    available_ids = {m.id for m in available_models}

    raw_override = payload.model_override
    target_override = (raw_override or "").strip() if isinstance(raw_override, str) else ""

    if not target_override:
        tree.header.model_override = None
        effective = default_model
        provider = "default"
        model_id = default_model
    else:
        if target_override not in available_ids:
            raise HTTPException(status_code=400, detail=f"Unknown configured model: {target_override}")
        provider, _ps = resolve_provider_for_model(
            target_override,
            settings_full.models.providers,
            default_provider=settings_full.models.default_provider,
            unknown_prefix_policy="fail",
        )
        tree.header.model_override = target_override
        effective = target_override
        model_id = target_override

    store = _get_store()
    await store.save_header(tree)
    entry = ModelChangeEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        provider=provider,
        model_id=model_id,
    )
    event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
    await store.commit_events([event])

    return SessionModelSettingsResponse(
        session_id=session_id,
        default_model=default_model,
        model_override=tree.header.model_override,
        effective_model=effective,
        available_models=available_models,
    )


@web_router.get("/sessions/{session_id}/messages")
async def get_messages(
    request: Request,
    session_id: str,
    offset: int = 0,
    limit: int = 50,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    try:
        tree = await _load_and_verify(session_id, user_id)
        workspace_root = _resolve_workspace_root(
            request=request,
            user_id=user_id,
            workspace_id=tree.header.workspace_id,
        )
        try:
            review_registry_fn = request.app.state.web_deps.review_registry_fn
            review_state = review_registry_fn(workspace_root).get_or_open(session_id) if review_registry_fn else None
        except OSError:
            logger.warning("review registry unavailable for session=%s; skipping review state", session_id)
            review_state = None
        feedback_by_message: dict[str, str] = {}
        output_files_by_message: dict[str, list[str]] = {}
        file_changes_by_message: dict[str, list[dict]] = {}
        execution_timeline_by_message: dict[str, list[dict]] = {}
        # 注意：按 entries 全集扫描而非 tree.order——order 仅由消息类事件
        # （user/assistant/tool）重建，CustomEntry 走 entry_updated 落库
        # （不进 order）。按 order 扫描会让 execution_timeline 等元数据
        # 在历史回放中永远不可见。
        for entry in tree.entries.values():
            if not isinstance(entry, CustomEntry):
                continue
            data = entry.data or {}
            if entry.custom_type == FEEDBACK_CUSTOM_TYPE:
                mid = data.get("message_id")
                vote = data.get("vote")
                if isinstance(mid, str) and isinstance(vote, str) and vote in ("up", "down", "none"):
                    feedback_by_message[mid] = vote
                continue
            if entry.custom_type == OUTPUT_FILES_CUSTOM_TYPE:
                mid = data.get("message_id")
                files = data.get("files")
                if isinstance(mid, str) and isinstance(files, list):
                    cleaned = [f.strip() for f in files if isinstance(f, str) and f.strip()]
                    if cleaned:
                        output_files_by_message[mid] = cleaned
                continue
            if entry.custom_type == FILE_CHANGES_CUSTOM_TYPE:
                mid = data.get("message_id")
                changes = data.get("changes")
                if isinstance(mid, str) and isinstance(changes, list):
                    cleaned = [c for c in changes if isinstance(c, dict)]
                    if cleaned:
                        file_changes_by_message[mid] = cleaned
                continue
            if entry.custom_type == EXECUTION_TIMELINE_CUSTOM_TYPE:
                mid = data.get("message_id")
                nodes = data.get("nodes")
                if isinstance(mid, str) and isinstance(nodes, list):
                    execution_timeline_by_message[mid] = nodes
                continue
        display = _build_display_messages(tree, review_utils=get_web_deps(request).review_utils)
        page = display[offset : offset + limit]
        for item in page:
            mid = item.get("id")
            if isinstance(mid, str):
                item["feedback"] = feedback_by_message.get(mid)
                item["output_files"] = output_files_by_message.get(mid, [])
                changes = file_changes_by_message.get(mid, [])
                for change in changes:
                    path = change.get("path")
                    change["message_id"] = mid
                    if isinstance(path, str) and review_state is not None:
                        change["reviewed"] = review_state.decision.get(path)
                item["file_changes"] = changes
                # 执行时间线终态 nodes 附着（与 output_files 同构），供前端历史回放（StepTimeline）。
                item["timeline"] = execution_timeline_by_message.get(mid, [])
        return page
    except Exception:
        logger.exception("get_messages failed for session=%s user=%s", session_id, user_id)
        raise


class FeedbackRequest(BaseModel):
    message_id: str
    vote: str


class FeedbackResponse(BaseModel):
    ok: bool = True


@web_router.post("/sessions/{session_id}/feedback", response_model=FeedbackResponse)
async def set_message_feedback(
    session_id: str,
    payload: FeedbackRequest,
    user_id: str = Depends(get_current_user),
) -> FeedbackResponse:
    tree = await _load_and_verify(session_id, user_id)
    message_id = (payload.message_id or "").strip()
    vote = (payload.vote or "").strip().lower()
    if not message_id:
        raise HTTPException(status_code=400, detail="message_id required")
    if vote not in ("up", "down", "none"):
        raise HTTPException(status_code=400, detail="invalid vote")
    if message_id not in tree.entries:
        raise HTTPException(status_code=404, detail="message not found")

    entry = CustomEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=FEEDBACK_CUSTOM_TYPE,
        data={"message_id": message_id, "vote": vote},
    )
    store = _get_store()
    event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
    await store.commit_events([event])
    return FeedbackResponse(ok=True)


class FileReviewMarkRequest(BaseModel):
    """Persist a single file's review decision to the review registry."""
    message_id: str
    path: str
    decision: str  # 'accepted' | 'rejected'


class FileReviewMarkResponse(BaseModel):
    ok: bool = True


@web_router.post(
    "/sessions/{session_id}/file-review-mark",
    response_model=FileReviewMarkResponse,
)
async def mark_file_review(
    request: Request,
    session_id: str,
    payload: FileReviewMarkRequest,
    user_id: str = Depends(get_current_user),
) -> FileReviewMarkResponse:
    tree = await _load_and_verify(session_id, user_id)
    workspace_root = _resolve_workspace_root(
        request=request,
        user_id=user_id,
        workspace_id=tree.header.workspace_id,
    )
    message_id = (payload.message_id or "").strip()
    path = (payload.path or "").strip()
    decision = (payload.decision or "").strip().lower()
    if not message_id:
        raise HTTPException(status_code=400, detail="message_id required")
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    if decision not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="invalid decision")
    if message_id not in tree.entries:
        raise HTTPException(status_code=404, detail="message not found")
    review_registry_fn = request.app.state.web_deps.review_registry_fn
    if review_registry_fn is not None:
        review_registry_fn(workspace_root).set_decision(session_id, path, decision)
    return FileReviewMarkResponse(ok=True)


@web_router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> Response:
    await _load_and_verify(session_id, user_id)
    store = _get_store()
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)


# ================================================================
# [Trajectory] Audit endpoint: 4-layer side-by-side snapshot
# Returns TrajectorySnapshot serialised JSON for the given session.
#
# Accepts both raw conversation_id ("abc123") or full session_id
# ("web:{user_id}:abc123") and auto-resolves to the correct session_id.
# Empty records list if no trajectory captured (L0 OFF) or PostgreSQL data missing.
# ================================================================

@web_router.get("/sessions/{session_id}/trajectory")
async def get_session_trajectory_audit(
    session_id: str,
    limit: int = Query(default=50, ge=0, le=1000),
    ascending: bool = False,
    workspace_id: str = "default",
    request: Request = ...,
    user_id: str = Depends(get_current_user),
) -> dict:
    # Step 0: Resolve full session_id from raw conversation_id.
    # Canonical session ids are "web:{user_id}:{workspace_id}:{conversation_id}".
    base_session_key = build_web_session_key(user_id, _clean_workspace_id(workspace_id))
    if not session_id.startswith("web:"):
        resolved_session_id = f"{base_session_key}:{session_id}"
    else:
        resolved_session_id = session_id

    # Step 1: ownership gate — verify session exists and user owns it.
    # Some sessions are stored with an "s:" prefix (server-side generated
    # session ids like "web:admin:s:abc123"). If the first lookup fails,
    # retry with the "s:" prefix before giving up.
    try:
        await _load_and_verify(resolved_session_id, user_id)
    except Exception:
        if not resolved_session_id.startswith(f"{base_session_key}:s:"):
            alt_session_id = resolved_session_id.replace(
                f"{base_session_key}:", f"{base_session_key}:s:", 1,
            )
            await _load_and_verify(alt_session_id, user_id)
            resolved_session_id = alt_session_id
        else:
            raise

    # Step 2: resolve persistence (via Protocol, no raw session_factory).
    from prismclaw.schema.trajectory import TrajectorySnapshot, TrajectorySnapshotMeta

    web_deps = request.app.state.web_deps
    _persistence = web_deps.trajectory_persistence or _trajectory_persistence

    snap: TrajectorySnapshot
    if _persistence is None:
        # Degrade: return empty snapshot with clear diagnostic flag
        snap = TrajectorySnapshot(
            session_id=resolved_session_id, records=[],
            meta=TrajectorySnapshotMeta(
                record_count=0, turn_count_estimate=0,
                gap_count=0, gap_ratio=0.0, orphan_count=0,
                gap_warning=False, zstd_compressed_record_count=0,
            ),
        )
        object.__setattr__(snap, "_parse_warning", "no_trajectory_backend")
    else:
        try:
            snap = await _persistence.read_snapshot(
                resolved_session_id,
                limit=limit, ascending=ascending,
            )
        except Exception as exc:  # SDD A2: return empty, never 500
            import logging as _log
            _log.getLogger(__name__).warning(
                "web trajectory endpoint read_snapshot_v2_pg unexpected exc: %s", exc,
            )
            snap = TrajectorySnapshot(
                session_id=resolved_session_id, records=[],
                meta=TrajectorySnapshotMeta(
                    record_count=0, turn_count_estimate=0,
                    gap_count=0, gap_ratio=0.0, orphan_count=0,
                    gap_warning=False, zstd_compressed_record_count=0,
                ),
            )
            object.__setattr__(snap, "_parse_warning", f"read_error: {type(exc).__name__}")

    # Step 3: model_dump JSON → includes layer 0.5/①/②/③/④ fields.
    try:
        data = snap.model_dump(mode="json")
    except Exception:
        # Last-resort: fall back to manual dict construction
        data = {
            "session_id": snap.session_id,
            "records": [r.model_dump(mode="json") for r in snap.records],
            "meta": snap.meta.model_dump(mode="json"),
        }
    # Surface internal _parse_warning as top-level read_warning so UI
    # can render it without breaking schema validation.
    try:
        warn = object.__getattribute__(snap, "_parse_warning")
        if isinstance(warn, str) and warn:
            data["read_warning"] = warn
    except Exception:
        pass
    return data


# ================================================================
# 对齐 per-conversation-event cell trajectory endpoint.
# Returns TrajectoryCell rows (one per tree node) for the timeline/ledger
# to render as cells (NOT per-LLM-iteration records).
# ================================================================

@web_router.get("/sessions/{session_id}/trajectory/cells")
async def get_session_trajectory_cells(
    session_id: str,
    after_seq: int = 0,
    workspace_id: str = "default",
    request: Request = ...,
    user_id: str = Depends(get_current_user),
) -> dict:
    """per-conversation-event cells (one cell per tree node).

    Distinct from /sessions/{id}/trajectory (per-LLM-iteration records).
    This endpoint powers the timeline + ledger's cell view.

    ``after_seq > 0`` switches to the incremental poll mode: only rows
    newer than the cursor are returned (cells are written complete in one
    INSERT — there is no post-insert enrichment to re-read).
    """
    # Step 0: Resolve full session_id (same logic as trajectory endpoint)
    base_session_key = build_web_session_key(user_id, _clean_workspace_id(workspace_id))
    if not session_id.startswith("web:"):
        resolved_session_id = f"{base_session_key}:{session_id}"
    else:
        resolved_session_id = session_id

    # Step 1: ownership gate — try alt "s:" prefix if first fails
    try:
        await _load_and_verify(resolved_session_id, user_id)
    except Exception:
        if not resolved_session_id.startswith(f"{base_session_key}:s:"):
            alt_session_id = resolved_session_id.replace(
                f"{base_session_key}:", f"{base_session_key}:s:", 1,
            )
            await _load_and_verify(alt_session_id, user_id)
            resolved_session_id = alt_session_id
        else:
            raise

    # Step 2: resolve PostgreSQL session_factory
    from prismclaw.schema.trajectory import TrajectoryCellSnapshot

    web_deps = request.app.state.web_deps
    _persistence = web_deps.trajectory_persistence or _trajectory_persistence

    if _persistence is None:
        return TrajectoryCellSnapshot(
            session_id=resolved_session_id, cells=[], latest_seq=after_seq,
        ).model_dump(mode="json")

    try:
        cells = await _persistence.read_cells(
            resolved_session_id, after_seq=after_seq,
        )
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning(
            "web trajectory cells endpoint read_cells_pg unexpected exc: %s", exc,
        )
        cells = []

    snap = TrajectoryCellSnapshot(
        session_id=resolved_session_id,
        cells=cells,
        latest_seq=max((c.seq for c in cells), default=after_seq),
    )
    return snap.model_dump(mode="json")


class PatchSessionRequest(BaseModel):
    title: str | None = None


@web_router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: PatchSessionRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    tree = await _load_and_verify(session_id, user_id)
    store = _get_store()
    if body.title is not None:
        title = body.title.strip()
        if len(title) > 200:
            raise HTTPException(status_code=400, detail="Title too long (max 200 chars)")
        tree.header.title = title or None
        await store.save_header(tree)
    return {
        "id": tree.header.id,
        "title": tree.header.title,
    }


class UploadItem(BaseModel):
    original_name: str
    path: str
    size_bytes: int
    mime_type: str | None = None


class SkippedItem(BaseModel):
    """A file the upload pipeline rejected, with a machine-readable ``code``.

    Codes (single source of truth in ``upload_paths`` / ``upload_pipeline``):
      ``blocked_type``    — extension is in the executable blocklist
      ``noise``           — OS metadata artifact (``.DS_Store`` / ``__MACOSX`` / …)
      ``disk_full``       — workspace cannot hold the file (sink pre-check)
      ``invalid_path``    — sanitized path escapes the destination root
      ``transfer_failed`` — chunk RPC failure / decode error / unexpected error
    """

    name: str
    code: str


class UploadResponse(BaseModel):
    files: list[UploadItem]
    skipped: list[SkippedItem] = []


@web_router.get("/uploads", response_model=UploadResponse)
async def list_uploads(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    session_id: str | None = Query(default=None),
    dir: str = "",
) -> UploadResponse:
    """List uploaded files in a workspace.

    零兼容代码：必须传 session_id 或 dir。
    - session_id → session 私有（sessions/<sid>/uploads/）
    - dir → workspace 级共享（inputs/<dir>/）
    两者都没传 → 空列表（不再扫已废弃的 workspace root uploads/）。
    """
    wid = _clean_workspace_id(workspace_id)
    rel_dir = (dir or "").lstrip("/")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)

    if session_id:
        from prismclaw.infra.workspace_paths import resolve_session_uploads_dir
        target = resolve_session_uploads_dir(user_id, wid, session_id)
    elif rel_dir:
        target = root / "inputs" / rel_dir
    else:
        return UploadResponse(files=[])

    if not target.is_dir():
        return UploadResponse(files=[])
    root_abs = root.expanduser().resolve()
    items: list[tuple[float, UploadItem]] = []
    for p in target.iterdir():
        if not p.is_file():
            continue
        if p.name in (".git", ".DS_Store"):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        items.append(
            (
                st.st_mtime,
                UploadItem(
                    original_name=p.name,
                    path=str(p.relative_to(root_abs)),
                    size_bytes=int(st.st_size),
                    mime_type=None,
                ),
            )
        )
    items.sort(key=lambda t: t[0], reverse=True)
    return UploadResponse(files=[i[1] for i in items])


@web_router.post("/extract", response_model=ExtractResponse)
async def trigger_extract(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> ExtractResponse:
    return await _do_extract(request, user_id, workspace_id)


@web_router.post("/learn", response_model=ExtractResponse)
async def trigger_learn(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> ExtractResponse:
    return await _do_extract(request, user_id, workspace_id)


def _get_prompt_optimizer(request: Request) -> Any:
    web_deps = request.app.state.web_deps
    factory = web_deps.prompt_optimizer_factory
    if factory is None:
        raise HTTPException(500, "Prompt optimizer not configured")
    llm_client = web_deps.llm_client
    if llm_client is None:
        raise HTTPException(500, "LLM client not configured")
    return factory(llm_client)


@web_router.post("/optimize-prompt", response_model=OptimizePromptResponse)
async def optimize_prompt(
    body: OptimizePromptRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> OptimizePromptResponse:
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    try:
        optimizer = _get_prompt_optimizer(request)
        optimized = await optimizer.optimize(prompt)
        return OptimizePromptResponse(optimized=optimized)
    except HTTPException:
        raise
    except Exception:
        logger.exception("optimize_prompt failed for user %s", user_id)
        raise HTTPException(500, "提示词优化失败，请稍后重试")


async def _do_extract(request: Request, user_id: str, workspace_id: str) -> ExtractResponse:
    try:
        router = _get_router()
        wid = _clean_workspace_id(workspace_id)
        session_key = build_web_session_key(user_id, wid)
        session_id = await router.store.get_current_session_id(session_key)
        if session_id is None:
            return ExtractResponse(
                spawned=False,
                message="当前会话还没有 tool 调用。先让 bot 执行一些实际操作（bash/read/write 等），再试 /extract。",
            )

        web_deps = request.app.state.web_deps
        run_extract = web_deps.extract_fn
        format_fn = web_deps.format_extraction_fn

        if run_extract is None or format_fn is None:
            return ExtractResponse(
                spawned=False,
                message="⚠️ 提取功能不可用。",
            )

        result = await run_extract(
            redis_client=web_deps.redis_client,
            memory_store=web_deps.memory_store,
            session_store=router.store,
            llm_client=web_deps.llm_client,
            session_id=session_id,
            settings=web_deps.evolution_settings,
            nudge_hook=web_deps.nudge_hook,
        )

        if result is None:
            return ExtractResponse(
                spawned=False,
                message="⏳ 学习超时（>15 秒）已中止，候选数据已保留，1 分钟后可再次 /extract。",
            )
        if result.skip_reason == "disabled":
            return ExtractResponse(spawned=False, message="⚠️ 自我进化功能未启用。")
        if result.skip_reason == "rate_limited":
            return ExtractResponse(
                spawned=False,
                message="⏱ 学习触发过于频繁，请 1 分钟后再试。",
            )

        return ExtractResponse(
            spawned=result.spawned and result.skip_reason is None,
            message=format_fn(result),
        )
    except Exception:
        logger.exception("extract endpoint failed for user %s", user_id)
        return ExtractResponse(
            spawned=False,
            message="⚠️ 命令执行失败，请稍后重试。",
        )
