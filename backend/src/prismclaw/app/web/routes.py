from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.session_router import SessionRouter
from prismclaw.app.web._routes_helpers import (
    _clean_workspace_id,
    _get_settings_full,
    _resolve_workspace_root,
)
from prismclaw.app.web.chat._chat_helpers import _build_run_projection_payload
from prismclaw.app.web.deps import get_web_deps, get_web_deps_from_app
from prismclaw.app.web.routers._message_helpers import _build_display_messages
from prismclaw.app.web.routers.config import config_router
from prismclaw.app.web.routers.domains import router as domains_router
from prismclaw.app.web.routers.experts import (
    ExpertStateResponse,
    experts_router,
    get_expert_registries,
    raise_expert_http_error,
)
from prismclaw.app.web.routers.files import files_router
from prismclaw.app.web.routers.office import office_router
from prismclaw.app.web.routers.settings import settings_router
from prismclaw.app.web.routers.skills import session_skills_router, skills_router
from prismclaw.app.web.routers.speech import speech_router
from prismclaw.app.web.routers.workspace import workspace_router
from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
from prismclaw.engine.experts.codes import ExpertError
from prismclaw.engine.experts.selection import (
    get_session_expert_state,
    select_session_expert,
)
from prismclaw.schema.services.skills import SkillManifest
from prismclaw.schema.session.event_logger import SessionEventLogger

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from prismclaw.app.web.chat.chat_protocol import SessionQueue
    from prismclaw.app.web.chat.run_presence import RunPresence
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.hooks.memory_nudge_hook import MemoryNudgeHook
    from prismclaw.engine.services.prompt_optimizer import PromptOptimizer
    from prismclaw.infra.settings import Settings
    from prismclaw.infra.settings._dream import EvolutionSettings
    from prismclaw.infra.task_manager import TaskManager
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.storage.storage_types import MemoryStore
    from prismclaw.schema.trajectory.trajectory import TrajectoryPersistence
from prismclaw.schema.session.ids import build_web_session_key
from prismclaw.schema.session.types import (
    EXECUTION_TIMELINE_CUSTOM_TYPE,
    FEEDBACK_CUSTOM_TYPE,
    FILE_CHANGES_CUSTOM_TYPE,
    OUTPUT_FILES_CUSTOM_TYPE,
    REFERENCE_SOURCES_CUSTOM_TYPE,
    CustomEntry,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)

_event_logger = SessionEventLogger()

web_router = APIRouter(prefix="/api", tags=["web"])

web_router.include_router(files_router)
web_router.include_router(experts_router)
web_router.include_router(skills_router)
web_router.include_router(session_skills_router)
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
    memory_store: MemoryStore | None = None,
    task_manager: TaskManager | None = None,
    redis_client: Redis | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    llm_client: LLMClient | None = None,
    evolution_settings: EvolutionSettings | None = None,
    nudge_hook: MemoryNudgeHook | None = None,
    trajectory_persistence: TrajectoryPersistence | None = None,
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
    expert_state: ExpertStateResponse


class CreateSessionRequest(BaseModel):
    workspace_id: str = "default"
    title: str | None = None
    expert_id: str | None = None


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


async def _get_workspace_runner_deps(
    app: FastAPI, *, user_id: str, workspace_root: Path
) -> AgentRunnerDeps | None:
    web_deps = get_web_deps_from_app(app)
    fn = web_deps.workspace_runner_deps_fn
    if fn is not None:
        return await fn(app, user_id=user_id, workspace_root=workspace_root, wait_for_ready=True)
    return None


async def _list_runtime_skills(
    request: Request,
    *,
    user_id: str,
    workspace_root: Path,
    settings_full: Settings,
) -> tuple[list[SkillManifest], ExtensionManager | None]:
    _ = settings_full
    runner_deps = await _get_workspace_runner_deps(
        request.app,
        user_id=user_id,
        workspace_root=workspace_root,
    )
    if runner_deps is None:
        raise RuntimeError("workspace runner deps are required for skill listing")
    raw_extension_manager = runner_deps.expert.extension_manager
    extension_manager = (
        raw_extension_manager if isinstance(raw_extension_manager, ExtensionManager) else None
    )
    capability_catalog = (
        getattr(runner_deps, "capability_catalog", None) if runner_deps is not None else None
    )
    if capability_catalog is None:
        raise RuntimeError(
            "workspace runner deps must provide capability_catalog for skill listing"
        )
    return [
        entry.manifest for entry in capability_catalog.list_skill_entries()
    ], extension_manager


async def _load_and_verify(session_id: str, user_id: str) -> SessionTree:
    store = _get_store()
    tree = await store.load(session_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # 必须带结尾冒号：``web:admin`` 是 ``web:admin2`` 的前缀，缺冒号会越权。
    if not tree.header.session_key.startswith(f"web:{user_id}:"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tree


def _resolve_conversation_identity(
    *, user_id: str, workspace_id: str, session_id: str
) -> tuple[str, list[str]]:
    """归一化会话 id 并给出判活候选（/run 与 /sync 共用同一口径）。

    裸 id 拼成 ``web:{uid}:{workspace}:{sid}``；完整 id 原样使用。候选额外
    补 ``s:`` 变体——SessionRouter 生成的树 id 带随机段，前端可能拿裸 id 查询。
    """
    base_session_key = build_web_session_key(user_id, _clean_workspace_id(workspace_id))
    if not session_id.startswith("web:"):
        resolved_session_id = f"{base_session_key}:{session_id}"
    else:
        resolved_session_id = session_id

    candidates = [resolved_session_id]
    if not resolved_session_id.startswith(f"{base_session_key}:s:"):
        candidates.append(
            resolved_session_id.replace(
                f"{base_session_key}:",
                f"{base_session_key}:s:",
                1,
            )
        )
    return resolved_session_id, candidates


def _get_session_queue(request: Request) -> SessionQueue | None:
    web_deps = getattr(getattr(request, "app", None), "state", None)
    web_deps = getattr(web_deps, "web_deps", None)
    return getattr(web_deps, "session_queue", None)


def _get_run_presence(request: Request) -> RunPresence | None:
    web_deps = getattr(getattr(request, "app", None), "state", None)
    web_deps = getattr(web_deps, "web_deps", None)
    return getattr(web_deps, "run_presence", None)


async def _resolve_run_state(
    request: Request, candidates: list[str], *, turn_only: bool = False
) -> tuple[str, str | None]:
    """判定候选会话的运行态：active / idle / unknown。

    先查本进程（权威、零延迟）；本地没有这条 run 时用 Redis presence 兜底——
    sync 落到非 owner worker 也能得到确定答案，不再把进行中的一轮误判成
    idle。只有 Redis 不可读时才返回 unknown（保守，客户端不 seal）。

    ``turn_only``：消息面用。只看本轮是否仍在产出（RunControl.active），
    不把"done 之后仍在跑的后台子代理/工具痕迹"算作本轮未完成，否则这条
    assistant 消息会一直被标 in_progress，直到子代理结束才收尾。
    """
    session_queue = _get_session_queue(request)
    if session_queue is None:
        return "unknown", None
    is_running = (
        session_queue.is_turn_running if turn_only else session_queue.is_conversation_running
    )
    for candidate in candidates:
        if is_running(candidate):
            return "active", candidate
    presence = _get_run_presence(request)
    if presence is not None:
        unknown = False
        for candidate in candidates:
            state = await presence.read(candidate)
            if state == "active":
                return "active", candidate
            if state == "unknown":
                unknown = True
        if unknown:
            return "unknown", None
    return "idle", None


def _mark_in_progress(messages: list[dict[str, Any]], run_state: str) -> None:
    """把运行中的最后一轮 assistant 消息标记为 in_progress。

    这一轮是分步落库的，当作定稿会在流式气泡之外再长出一个半成品气泡；
    同时客户端不得推进水位越过它，否则该轮后续更新会被自己的 after_seq 跳过。

    只认"最后一条 user 消息之后"的 assistant：用户刚发出新一轮、assistant
    尚未落库时，上一条已定稿答案不能被误标为进行中（那会把历史答案藏掉）。

    unknown（无法确认运行态）保守标记，避免提前 seal。
    """
    target: dict[str, Any] | None = None
    if run_state in ("active", "unknown"):
        # 用 seq 而不是列表下标定位"最后一条 user 之后"：展示列表里可能混入
        # 正文为空、但被 retain 的时间线回合，位置不可靠。
        last_user_seq = max(
            (int(item.get("seq") or 0) for item in messages if item.get("role") == "user"),
            default=0,
        )
        candidates = [
            item
            for item in messages
            if item.get("role") == "assistant" and int(item.get("seq") or 0) > last_user_seq
        ]
        if candidates:
            target = max(candidates, key=lambda item: int(item.get("seq") or 0))
    for item in messages:
        item["in_progress"] = item is target


def _timeline_retain_ids(tree: SessionTree) -> set[str]:
    """挂着执行时间线的 message_id 集合。

    /sessions/{id} 的 message_count 与 /messages 的返回必须同源，否则分页
    切片会把"正文为空但有时间线"的回合裁掉（其 seq 已被水位越过，增量也
    补不回来）。
    """
    retain: set[str] = set()
    for entry in tree.entries.values():
        if not isinstance(entry, CustomEntry):
            continue
        if entry.custom_type != EXECUTION_TIMELINE_CUSTOM_TYPE:
            continue
        data = entry.data or {}
        mid = data.get("message_id")
        if isinstance(mid, str) and data.get("nodes"):
            retain.add(mid)
    return retain


def _build_conversation_messages(
    request: Request, tree: SessionTree, *, user_id: str
) -> list[dict[str, Any]]:
    """装配展示消息及其元数据（/messages 与 /sync 唯一实现，避免口径漂移）。"""
    deps = get_web_deps(request)
    # 工作区根目录解析带创建副作用，且与归属校验同源：这里必须用调用方的
    # 已认证 user_id，不能从 session_key 反解（用户名可能含分隔符）。
    workspace_root = _resolve_workspace_root(
        request=request,
        user_id=user_id,
        workspace_id=tree.header.workspace_id,
    )
    review_state = None
    review_registry_fn = getattr(deps, "review_registry_fn", None)
    if review_registry_fn is not None:
        try:
            review_state = review_registry_fn(workspace_root).get_or_open(tree.header.id)
        except OSError:
            logger.warning(
                "review registry unavailable for session=%s; skipping review state",
                tree.header.id,
            )

    feedback_by_message: dict[str, str] = {}
    output_files_by_message: dict[str, list[str]] = {}
    file_changes_by_message: dict[str, list[dict[str, Any]]] = {}
    execution_timeline_by_message: dict[str, list[dict[str, Any]]] = {}
    reference_sources_by_message: dict[str, list[dict[str, Any]]] = {}
    # 单条消息可能挂着多条 CustomEntry（反馈、交付物、时间线各自独立落库）；
    # 取挂靠条目的最大 seq，才保证"后到的交付物/时间线"不被增量同步跳过。
    meta_seq_by_message: dict[str, int] = {}
    # 注意：按 entries 全集扫描而非 tree.order——order 仅由消息类事件
    # （user/assistant/tool）重建，CustomEntry 走 entry_updated 落库
    # （不进 order）。按 order 扫描会让 execution_timeline 等元数据
    # 在历史回放中永远不可见。
    for entry in tree.entries.values():
        if not isinstance(entry, CustomEntry):
            continue
        data = entry.data or {}
        mid = data.get("message_id")
        if entry.custom_type == FEEDBACK_CUSTOM_TYPE:
            vote = data.get("vote")
            if isinstance(mid, str) and isinstance(vote, str) and vote in ("up", "down", "none"):
                feedback_by_message[mid] = vote
        elif entry.custom_type == OUTPUT_FILES_CUSTOM_TYPE:
            files = data.get("files")
            if isinstance(mid, str) and isinstance(files, list):
                cleaned = [f.strip() for f in files if isinstance(f, str) and f.strip()]
                if cleaned:
                    output_files_by_message[mid] = cleaned
        elif entry.custom_type == FILE_CHANGES_CUSTOM_TYPE:
            changes = data.get("changes")
            if isinstance(mid, str) and isinstance(changes, list):
                clean_changes = [c for c in changes if isinstance(c, dict)]
                if clean_changes:
                    file_changes_by_message[mid] = clean_changes
        elif entry.custom_type == EXECUTION_TIMELINE_CUSTOM_TYPE:
            nodes = data.get("nodes")
            if isinstance(mid, str) and isinstance(nodes, list):
                execution_timeline_by_message[mid] = nodes
        elif entry.custom_type == REFERENCE_SOURCES_CUSTOM_TYPE:
            sources = data.get("sources")
            if isinstance(mid, str) and isinstance(sources, list):
                clean_sources = [s for s in sources if isinstance(s, dict)]
                if clean_sources:
                    reference_sources_by_message[mid] = clean_sources
        else:
            continue
        if isinstance(mid, str):
            meta_seq_by_message[mid] = max(meta_seq_by_message.get(mid, 0), entry.seq)

    # 正文为空、但挂着执行时间线的回合（纯推理 / 仅交付物 / 空答案）必须保留：
    # 时间线指向它的 entry id，丢掉就等于刷新后执行过程永久消失。保留在
    # _build_display_messages 内部完成，保证消息顺序与 message_count 同源。
    display = _build_display_messages(
        tree,
        review_utils=deps.review_utils,
        retain_ids=_timeline_retain_ids(tree),
    )
    for item in display:
        mid = item.get("id")
        if not isinstance(mid, str):
            continue
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
        # 结构化参考来源：与实时 event:reference 同字段名，刷新后来源面板不丢。
        item["reference_sources"] = reference_sources_by_message.get(mid, [])
        item["seq"] = max(int(item.get("seq") or 0), meta_seq_by_message.get(mid, 0))
    return display


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
    resolved_session_id, candidates = _resolve_conversation_identity(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
    )

    # Step 1: 内存判活（含 "s:" 前缀回退候选）——非运行中直接 null。
    run_state, running_id = await _resolve_run_state(request, candidates)
    if run_state != "active" or running_id is None:
        return {"run": None}
    session_queue = _get_session_queue(request)
    resolved_session_id = running_id

    # Step 2: 运行中——归属校验 + 树加载（task_runtime 构建需要），内存
    # 直读构建。构建失败（投影缺失，理论不可达）回落 null，宁缺毋陈旧。
    tree = await _load_and_verify(resolved_session_id, user_id)
    if session_queue is None:
        return {"run": None}
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
    细分态 OR RunControl 生命周期信号，与 WS 层同一对象）；其他 worker 上
    运行的会话由 Redis presence 补齐。Web 端刷新/重连后把它并入 `/sync` 的
    候选集合（运行态与正文都来自 sync 的同一份服务端投影）；`/run` 仍保留
    给移动端。

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
    prefix = f"web:{user_id}:"
    running: set[str] = set()
    if session_queue is not None:
        running.update(
            cid for cid in session_queue.list_running_conversations() if cid.startswith(prefix)
        )
    # 其他 worker 上运行的会话由 Redis presence 补齐，否则本 worker 只看到自己
    # 内存里的 run，前端重连后不会去 sync 这些会话。
    presence = _get_run_presence(request)
    if presence is not None:
        running.update(await presence.list_active(prefix))
    return {"conversations": sorted(running)}


@web_router.get("/sessions")
async def list_sessions(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    limit: int = Query(10, ge=1, le=200),
    offset: int = Query(0, ge=0),
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
    summaries = await store.list_session_history(
        session_key, limit=limit + 1, offset=max(offset, 0)
    )
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
                user_id=user_id,
                workspace_id=wid,
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
    user_id: Annotated[str, Depends(get_current_user)],
    body: CreateSessionRequest | None = None,
) -> CreateSessionResponse:
    router = _get_router()
    if body is None:
        body = CreateSessionRequest()
    wid = _clean_workspace_id(body.workspace_id)
    session_key = build_web_session_key(user_id, wid)
    # 创建会话即写入标题，避免首条消息后的 PATCH 竞态导致列表刷新后回退"新对话"。
    title: str | None = None
    if body.title is not None:
        cleaned = body.title.strip()
        if len(cleaned) > 200:
            raise HTTPException(status_code=400, detail="Title too long (max 200 chars)")
        title = cleaned or None
    session_id, _tree = await router.rotate(session_key, wid, title=title)
    # 不变量：会话一旦存在，其目录布局（inbox/outbox/context_blocks/...）就存在。
    # 否则前端在首条消息前列 inbox/outbox 会拿到 404，而这不是错误状态。
    from prismclaw.infra.workspace_paths import ensure_session_layout

    ensure_session_layout(
        _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid),
        session_id,
    )
    store = _get_store()
    builtin, states = get_expert_registries(request)
    if body.expert_id is None:
        state = await get_session_expert_state(
            session_id,
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
    else:
        try:
            state = await select_session_expert(
                session_id,
                body.expert_id,
                session_store=store,
                expert_registry=builtin,
                capability_policy_registry=states,
            )
        except ExpertError as exc:
            raise_expert_http_error(exc)
    return CreateSessionResponse(
        session_id=session_id,
        expert_state=ExpertStateResponse.model_validate(state),
    )


@web_router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    tree = await _load_and_verify(session_id, user_id)
    h = tree.header
    # 与 /messages 的列表同源：正文为空但有时间线的回合也计入，否则分页
    # 取最后一页时会被裁掉。
    msg_count = len(_build_display_messages(tree, retain_ids=_timeline_retain_ids(tree)))
    builtin, states = get_expert_registries(request)
    return {
        "id": h.id,
        "workspace_id": h.workspace_id,
        "agent_id": h.agent_id,
        "created_at": h.created_at,
        "last_interaction_at": h.last_interaction_at,
        "message_count": msg_count,
        "expert_state": await get_session_expert_state(
            session_id,
            session_store=_get_store(),
            expert_registry=builtin,
            capability_policy_registry=states,
        ),
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
            raise HTTPException(
                status_code=400, detail=f"Unknown configured model: {target_override}"
            )
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
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user_id: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    try:
        tree = await _load_and_verify(session_id, user_id)
        messages = _build_conversation_messages(request, tree, user_id=user_id)
        # 打开一个正在运行的会话时，历史里的半成品消息必须与 run 叠加层同口径
        # 标记，否则会出现「定稿气泡 + 流式气泡」两份正文。
        _, candidates = _resolve_conversation_identity(
            user_id=user_id,
            workspace_id=tree.header.workspace_id,
            session_id=session_id,
        )
        run_state, _ = await _resolve_run_state(request, candidates, turn_only=True)
        _mark_in_progress(messages, run_state)
        return messages[offset : offset + limit]
    except Exception:
        logger.exception("get_messages failed for session=%s user=%s", session_id, user_id)
        raise


def _settled_watermark(messages: list[dict[str, Any]], after_seq: int) -> int:
    """本次响应可推进到的客户端水位。

    有 in_progress 消息时，水位必须停在它之前——否则客户端下次不会再取回
    这一轮，而它恰恰是唯一还没定稿、最需要更新的那条。运行中追加的 steering
    user 消息（排在 in_progress 之后）照常返回，但不抬高水位。
    """
    in_progress_seq = next(
        (int(m.get("seq") or 0) for m in messages if m.get("in_progress")),
        None,
    )
    if in_progress_seq is None:
        return max((int(m.get("seq") or 0) for m in messages), default=after_seq)
    return max(
        (
            int(m.get("seq") or 0)
            for m in messages
            if not m.get("in_progress") and int(m.get("seq") or 0) < in_progress_seq
        ),
        default=after_seq,
    )


@web_router.get("/conversations/{session_id}/sync")
async def sync_conversation(
    request: Request,
    session_id: str,
    workspace_id: str = "default",
    after_seq: int = 0,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """补差 + 运行态的单一入口，替代 /conversations/running + /run 的 N+1 组合。

    - 只读、幂等：任意时刻调用结果一致（除 seq 推进）。
    - 只交付 ``seq > after_seq`` 的展示消息；user 与 assistant 同处 seq 体系，
      客户端按 seq 排序，user 消息自带 ``client_message_id`` 供本地乐观气泡
      re-key。``seq=0`` 是改造前的历史 entry，只经 /messages 提供。
      ``after_seq > watermark`` 说明客户端本地水位已失效（服务端会话被清理后
      同名复用），返回 history_reset 让客户端清零重拉。
    - 单页上限 100 条，超出置 has_more；settled_seq 只按本次返回的消息计算，
      否则客户端会把没拿到正文的 seq 当成已定稿而永久跳过。
    """
    resolved_session_id, candidates = _resolve_conversation_identity(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
    )
    run_state, running_id = await _resolve_run_state(request, candidates, turn_only=True)
    # 判活命中的可能是 "s:" 回退候选，以它为准加载，保证运行态与树同源。
    if running_id is not None:
        resolved_session_id = running_id

    tree = await _load_and_verify(resolved_session_id, user_id)
    messages = _build_conversation_messages(request, tree, user_id=user_id)
    watermark = max((entry.seq for entry in tree.entries.values()), default=0)
    history_reset = after_seq > watermark

    _mark_in_progress(messages, run_state)
    # 只交付 seq 严格大于水位的消息。``seq=0`` 是改造前的历史 entry（没有 seq
    # 字段），它们只经 /messages 提供：若允许它们在 after_seq=0 时进入同步，
    # 超过 100 条的老会话首页会全是 seq=0，settled_seq 永远停在 0，之后的新回答
    # 永远取不到。因此 after_seq=0 也不做"兼容 seq=0"的放行。
    messages = [m for m in messages if int(m.get("seq") or 0) > after_seq]
    messages.sort(key=lambda m: int(m.get("seq") or 0))

    has_more = len(messages) > 100
    if has_more:
        messages = messages[:100]

    settled_seq = _settled_watermark(messages, after_seq)

    run: dict[str, Any] | None = None
    session_queue = _get_session_queue(request)
    if run_state == "active" and session_queue is not None:
        payload = _build_run_projection_payload(
            tree,
            session_queue,
            resolved_session_id,
        )
        if payload is not None:
            run = payload.model_dump(mode="json")

    return {
        "conversation_id": resolved_session_id,
        "after_seq": after_seq,
        "settled_seq": settled_seq,
        "watermark": watermark,
        "run_state": run_state,
        "run": run,
        "messages": messages,
        "has_more": has_more,
        "history_reset": history_reset,
    }


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
    tree = await _load_and_verify(session_id, user_id)
    store = _get_store()
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    # Redis 记录删除后，磁盘上的会话目录（inbox/outbox/tool_results/.work）也要
    # 收掉：否则删会话只清元数据，文件会永久残留。
    import shutil

    from prismclaw.infra.workspace_paths import resolve_session_dir

    session_key = tree.header.session_key
    parts = session_key.split(":")
    workspace_id = parts[2] if len(parts) > 2 else "default"
    targets = {resolve_session_dir(user_id, workspace_id, session_key)}
    targets.add(resolve_session_dir(user_id, workspace_id, session_id))
    for target in targets:
        shutil.rmtree(target, ignore_errors=True)
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
    request: Request,
    session_id: str,
    limit: int = Query(default=50, ge=0, le=1000),
    ascending: bool = False,
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
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
    except HTTPException as exc:
        # 只有「不存在」才值得换 s: 前缀重试；403/500 必须原样抛出。
        if exc.status_code != 404:
            raise
        if not resolved_session_id.startswith(f"{base_session_key}:s:"):
            alt_session_id = resolved_session_id.replace(
                f"{base_session_key}:",
                f"{base_session_key}:s:",
                1,
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
            session_id=resolved_session_id,
            records=[],
            meta=TrajectorySnapshotMeta(
                record_count=0,
                turn_count_estimate=0,
                gap_count=0,
                gap_ratio=0.0,
                orphan_count=0,
                gap_warning=False,
                zstd_compressed_record_count=0,
            ),
        )
        object.__setattr__(snap, "_parse_warning", "no_trajectory_backend")
    else:
        try:
            snap = await _persistence.read_snapshot(
                resolved_session_id,
                limit=limit,
                ascending=ascending,
            )
        except Exception as exc:  # SDD A2: return empty, never 500
            import logging as _log

            _log.getLogger(__name__).warning(
                "web trajectory endpoint read_snapshot_v2_pg unexpected exc: %s",
                exc,
            )
            snap = TrajectorySnapshot(
                session_id=resolved_session_id,
                records=[],
                meta=TrajectorySnapshotMeta(
                    record_count=0,
                    turn_count_estimate=0,
                    gap_count=0,
                    gap_ratio=0.0,
                    orphan_count=0,
                    gap_warning=False,
                    zstd_compressed_record_count=0,
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
    request: Request,
    session_id: str,
    after_seq: int = 0,
    workspace_id: str = "default",
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
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
    except HTTPException as exc:
        # 只有「不存在」才值得换 s: 前缀重试；403/500 必须原样抛出。
        if exc.status_code != 404:
            raise
        if not resolved_session_id.startswith(f"{base_session_key}:s:"):
            alt_session_id = resolved_session_id.replace(
                f"{base_session_key}:",
                f"{base_session_key}:s:",
                1,
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
            session_id=resolved_session_id,
            cells=[],
            latest_seq=after_seq,
        ).model_dump(mode="json")

    try:
        cells = await _persistence.read_cells(
            resolved_session_id,
            after_seq=after_seq,
        )
    except Exception as exc:
        import logging as _log

        _log.getLogger(__name__).warning(
            "web trajectory cells endpoint read_cells_pg unexpected exc: %s",
            exc,
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
) -> dict[str, Any]:
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


_UPLOAD_LIST_MAX = 500


@web_router.get("/uploads", response_model=UploadResponse)
async def list_uploads(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    session_id: str | None = Query(default=None),
) -> UploadResponse:
    """List uploaded files in a workspace.

    - 传 ``session_id``：只列该会话的私有上传（sessions/<sid>/inbox/）。
    - 不传：聚合当前工作区所有会话的 inbox —— 咨询框「引用文件」需要跨会话
      选历史上传；只列当前会话会让旧文件在列表里"消失"。
    """
    wid = _clean_workspace_id(workspace_id)
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    root_abs = root.expanduser().resolve()

    from prismclaw.infra.workspace_paths import resolve_session_inbox_dir

    if session_id:
        inbox_dirs = [resolve_session_inbox_dir(user_id, wid, session_id)]
    else:
        sessions_dir = root_abs / "sessions"
        inbox_dirs = (
            [p / "inbox" for p in sessions_dir.iterdir() if p.is_dir()]
            if sessions_dir.is_dir()
            else []
        )

    items: list[tuple[float, UploadItem]] = []
    for inbox in inbox_dirs:
        if not inbox.is_dir():
            continue
        for p in inbox.iterdir():
            if len(items) >= _UPLOAD_LIST_MAX:
                break
            if not p.is_file():
                continue
            if p.name in (".git", ".DS_Store"):
                continue
            try:
                st = p.stat()
                rel_path = str(p.relative_to(root_abs))
            except (OSError, ValueError):
                continue
            items.append(
                (
                    st.st_mtime,
                    UploadItem(
                        original_name=p.name,
                        path=rel_path,
                        size_bytes=int(st.st_size),
                        mime_type=mimetypes.guess_type(p.name)[0],
                    ),
                )
            )
        if len(items) >= _UPLOAD_LIST_MAX:
            break
    items.sort(key=lambda t: t[0], reverse=True)
    return UploadResponse(files=[i[1] for i in items[:_UPLOAD_LIST_MAX]])


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


def _get_prompt_optimizer(request: Request) -> PromptOptimizer:
    web_deps = get_web_deps(request)
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
        raise HTTPException(500, "提示词优化失败，请稍后重试") from None


async def _do_extract(request: Request, user_id: str, workspace_id: str) -> ExtractResponse:
    try:
        router = _get_router()
        wid = _clean_workspace_id(workspace_id)
        session_key = build_web_session_key(user_id, wid)
        session_id = await router.store.get_current_session_id(session_key)
        if session_id is None:
            return ExtractResponse(
                spawned=False,
                message=(
                    "当前会话还没有 tool 调用。先让 bot 执行一些实际操作"
                    "（bash/read/write 等），再试 /extract。"
                ),
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
