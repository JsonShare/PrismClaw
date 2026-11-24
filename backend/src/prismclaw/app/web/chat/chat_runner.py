from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.app.web.chat.chat_protocol import SessionQueue
    from prismclaw.engine.subagent.runtime.parallel import ParallelTaskRuntimeRecord

from prismclaw.app.auth import resolve_profile
from prismclaw.app.web.chat._chat_helpers import (
    _REASONING_PUSH_INTERVAL_S,
    _SOFT_LANDING_CODES,
    _extract_tool_result_text,
    _get_runner_deps,
    _get_workspace_runner_deps,
    _load_tree_for_write,
    _persist_execution_timeline,
    _persist_partial_assistant_message,
    _persist_run_projection,
    _push_run_snapshot,
    _push_task_runtime_event,
    _push_timeline_event,
    _resolve_tool_workspace_path,
    _sanitize_workspace_id,
    _spawn_background,
    invalidate_tree_cache,
)
from prismclaw.app.web.chat.chat_adapters import (
    _await_questions_and_push,
    _generate_followup_questions_for_request,
    _push_reference_event,
)
from prismclaw.app.web.chat.chat_mentions import extract_mentions
from prismclaw.app.web.chat.chat_office import (
    _dedupe_simple,
    _extract_presented_paths,
    _filter_existing_output_files,
    _persist_file_changes_metadata,
)
from prismclaw.app.web.chat.chat_protocol import (
    _get_session_queue,
    _set_loading_active,
)
from prismclaw.app.web.connection import ConnectionState, send_event
from prismclaw.app.web.protocol.messages import (
    SERVER_CHAT_DELTA,
    SERVER_CHAT_DONE,
    SERVER_CHAT_START,
    SERVER_ERROR,
    SERVER_FILE_CHANGES,
    SERVER_OUTBOX,
    SERVER_RUN_KEEPALIVE,
    SERVER_TOOL_CLARIFY_REQUEST,
    ChatSendMessage,
)
from prismclaw.engine.execution.control.artifacts import ArtifactScanner, Scope
from prismclaw.infra.settings import load_settings
from prismclaw.runtime.facade.trajectory import (
    HeartbeatPayload,
    SetPhasePatch,
    TaskExecutionProjector,
)
from prismclaw.schema.agent import (
    Done,
    ErrorEvent,
    HeartbeatEvent,
    IterationBoundary,
    ReasoningChunk,
    SubagentProgressBatch,
    TextChunk,
    ToolCallEnd,
    ToolCallStart,
    ToolClarifyRequest,
)
from prismclaw.schema.agent.types import AgentDeps, RunRequest
from prismclaw.schema.content.streaming import SubagentProgressItem
from prismclaw.schema.protocol.protocol_types import ProtocolErrorCode
from prismclaw.schema.session.ids import build_web_session_key
from prismclaw.schema.session.output_files_metadata import persist_output_files_metadata
from prismclaw.schema.shared.path_sanitizer import StreamingPathRedactor
from prismclaw.schema.shared.tool_markup import MarkupStreamFilter

logger = logging.getLogger(__name__)


async def _lifecycle_broadcaster(
    state: ConnectionState,
    deps: AgentDeps,
    session_queue: SessionQueue,
    conversation_id: str,
    session_id: str,
) -> None:
    """独立生命周期心跳 + 周期 run.snapshot——与 start_conversation 事件流解耦。

    事件流在 HITL 长阻塞（wait_for_clarification 600s）期间不 yield 任何事件，
    前端若仅依赖事件流判断后端死活，会在等待期误判「已断」而空白。此 task 每
    15s 读取共享 phase 状态（由 agent_loop 进入/退出澄清等待时更新）并广播
    phase + elapsed_s，前端据此持续显示「等待你的回答…」。同时在该相位窗口
    内下发一帧全量 run.snapshot，让前端在事件流静默期也能整体替换 streaming
    运行态自愈（P3，取代脆弱的序号增量同步）。

    executing 期（phase 为 None）推 run.keepalive 极轻帧（2026-10-26，P0 修复
    「看门狗误杀长任务」）：单个长工具执行期间事件流零帧（仅 Start/End 两帧），
    若后端也静默，前端看门狗的 5min 静默判定会把仍在跑的任务 finalize——
    「任务突然没了」的第二触发路径。keepalive 不携带数据、不占 eventSeq，
    仅刷新前端静默计时（其 dispatch 对任何带 conversation_id 的帧先于类型
    分发刷新计时）；数据同步仍归 task.runtime / task.timeline，全量快照仍
    仅 phase 窗口下发，避免无谓带宽。
    """
    try:
        prev_phase: str | None = None
        while True:
            await asyncio.sleep(15.0)
            phase, phase_started = session_queue.get_phase()
            if phase is None:
                # 关键修复：phase 从非 None → None 的过渡点（如 awaiting_clarification
                # 解除后），不能只发 keepalive——keepalive 不带 run.snapshot，
                # 前端 hydrateRun 拿不到 phase=None，会卡在旧状态（"等待你的回答…"）。
                # 必须推一条 run.snapshot，让前端同时清 phase 和 clarifyRequest。
                if prev_phase is not None:
                    await _push_run_snapshot(
                        state,
                        deps,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        session_queue=session_queue,
                    )
                prev_phase = None
                await send_event(
                    state,
                    SERVER_RUN_KEEPALIVE,
                    conversation_id,
                    {"state": "executing"},
                )
                continue
            elapsed = time.monotonic() - phase_started
            await _push_timeline_event(
                state,
                conversation_id=conversation_id,
                session_id=session_id,
                patch=SetPhasePatch(phase=HeartbeatPayload(state=phase, elapsed_s=elapsed)),
            )
            await _push_run_snapshot(
                state,
                deps,
                session_id=session_id,
                conversation_id=conversation_id,
                session_queue=session_queue,
            )
            prev_phase = phase
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("web.chat.lifecycle_broadcaster.failed", exc_info=True)


async def _run_projection_flusher(
    deps: AgentDeps,
    *,
    session_id: str,
    conversation_id: str,
    session_queue: SessionQueue,
) -> None:
    """周期把后端运行态投影落盘为 run_projection custom entry（审计留档）。

    与 _lifecycle_broadcaster 各自独立：它只负责 phase 心跳广播，本协程
    只负责 ~5s 周期 flush，互不耦合。投影被回收（get 返回 None）即退出。
    周期从 2s 放宽到 5s：run_projection 落盘是「尽力而为」的后台持久化，
    不必高频；且 commit_events 抢的是 session 写锁，与主循环工具写树竞争，
    降频可显著缩小撞锁窗口（配合 commit_events 的短退避重试根治竞争）。
    """
    try:
        while True:
            await asyncio.sleep(5.0)
            projection = session_queue.get_web_runtime_projection(conversation_id)
            if projection is None:
                return
            try:
                await _persist_run_projection(
                    deps,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    session_queue=session_queue,
                )
            except Exception:
                logger.warning("web.chat.run_projection.flusher_failed", exc_info=True)
    except asyncio.CancelledError:
        raise


async def _run_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
) -> None:
    user_id = state.user_id
    if not user_id:
        await send_event(
            state,
            SERVER_ERROR,
            msg.conversation_id,
            {
                "message": "未认证的连接不能发起对话",
                "code": ProtocolErrorCode.AUTH_FAILED.value,
            },
        )
        return
    session_queue = _get_session_queue(state)
    rc = session_queue.get_run_control(msg.conversation_id)

    from prismclaw.app.web.workspace_ids import InvalidWorkspaceIdError

    try:
        requested_workspace_id = _sanitize_workspace_id(getattr(msg, "workspace_id", None))
    except InvalidWorkspaceIdError:
        await send_event(
            state,
            SERVER_ERROR,
            msg.conversation_id,
            {
                "message": "工作区 ID 非法，请重新选择或新建工作区后重试。",
                "code": ProtocolErrorCode.INVALID_REQUEST.value,
            },
        )
        return

    if msg.conversation_id.startswith("web:"):
        expected_prefix = f"web:{state.user_id}:"
        if not msg.conversation_id.startswith(expected_prefix):
            await send_event(
                state,
                SERVER_ERROR,
                msg.conversation_id,
                {
                    "message": "Access denied: invalid conversation_id",
                    "code": ProtocolErrorCode.INVALID_REQUEST.value,
                },
            )
            return
        session_id = msg.conversation_id
    else:
        session_key = build_web_session_key(user_id, requested_workspace_id)
        session_id = f"{session_key}:{msg.conversation_id}"

    web_deps = state.ws.app.state.web_deps
    redis_client = web_deps.redis_client
    user_profile = await resolve_profile(
        channel="web",
        user_id=user_id,
        redis_client=redis_client,
    )

    try:
        base_deps = _get_runner_deps(state)
    except AttributeError:
        base_deps = None

    if base_deps is None:
        await send_event(
            state,
            SERVER_ERROR,
            msg.conversation_id,
            {
                "message": "Agent runner not configured",
                "code": ProtocolErrorCode.INTERNAL_ERROR.value,
            },
        )
        return

    effective_workspace_id = requested_workspace_id
    try:
        tree = await _load_tree_for_write(base_deps, session_id)
        if tree is not None and getattr(tree, "header", None) is not None:
            try:
                session_workspace_id = _sanitize_workspace_id(
                    getattr(tree.header, "workspace_id", None)
                )
            except InvalidWorkspaceIdError:
                await send_event(
                    state,
                    SERVER_ERROR,
                    msg.conversation_id,
                    {
                        "message": "当前会话绑定的工作区 ID 非法，请新建会话后重试。",
                        "code": ProtocolErrorCode.INVALID_REQUEST.value,
                    },
                )
                return
            if requested_workspace_id != session_workspace_id:
                await send_event(
                    state,
                    SERVER_ERROR,
                    msg.conversation_id,
                    {
                        "message": (
                            "当前会话绑定的工作区与已选工作区不一致，请切换到对应工作区后继续，"
                            "或新建会话再执行。"
                        ),
                        "code": ProtocolErrorCode.INVALID_REQUEST.value,
                    },
                )
                return
            effective_workspace_id = session_workspace_id
    except Exception:
        logger.warning("web.chat.session_load_failed: session_id=%s", session_id, exc_info=True)
        await send_event(
            state,
            SERVER_ERROR,
            msg.conversation_id,
            {
                "message": "加载当前会话失败，请稍后重试或新建会话。",
                "code": ProtocolErrorCode.INTERNAL_ERROR.value,
            },
        )
        return
    # 显式标注为 AgentDeps：后续闭包（进度/终态 sink）会捕获它，
    # 若沿用 Optional 推断会在嵌套函数里丢失收窄。
    deps: AgentDeps = await _get_workspace_runner_deps(
        state,
        workspace_id=effective_workspace_id,
    )

    sandbox_policy = web_deps.sandbox_policy if web_deps is not None else None
    if sandbox_policy is None:
        raise RuntimeError(
            "web chat runtime requires sandbox_policy before constructing RunRequest"
        )
    # 输入框的内联卡片随正文以标记串发来，这里还原成模型可读文本并拆出结构化字段。
    # 正文是唯一真相源：协议不再接受客户端单独下发技能/插件/文件数组。
    user_text, mention_skills, mention_plugins, mention_files = extract_mentions(msg.content)
    request = RunRequest(
        session_id=session_id,
        workspace_id=effective_workspace_id,
        agent_id="default",
        user_message=user_text,
        # 正文原样落树（含内联卡片标记），刷新后的历史气泡仍按卡片渲染；
        # 模型上下文只拿 user_message（标记已还原成 @标题）。
        display_user_message=msg.content,
        client_message_id=msg.client_message_id,
        confirmation_decision=msg.confirmation_decision,
        attachments=msg.attachments,
        user_id=user_id,
        user_profile=user_profile,
        sandbox_policy=sandbox_policy,
        trajectory_audit_header=msg.trajectory,
        designated_skills=mention_skills,
        designated_plugins=mention_plugins,
        referenced_files=mention_files or [],
        conversation_id=msg.conversation_id,
    )

    if web_deps is not None and web_deps.scheduler_context is not None:
        request.tool_context_extras.update(
            scheduler_store=web_deps.scheduler_context.get("scheduler_store"),
            scheduler_engine=web_deps.scheduler_context.get("scheduler_engine"),
            scheduler_dispatch=web_deps.scheduler_context.get("scheduler_dispatch"),
            scheduler_broadcast=web_deps.scheduler_context.get("scheduler_broadcast"),
            redis_client=web_deps.scheduler_context.get("redis_client"),
            scheduler_max_tasks_per_user=load_settings().scheduler.max_tasks_per_user,
        )

    # 命令身份传递: command_adapter 注入的 _command_identity → RunRequest
    _cmd_identity = getattr(msg, "_command_identity", None)
    if _cmd_identity is not None:
        request.tool_context_extras["pending_command_identity"] = _cmd_identity

    async def _subagent_progress_sink(batch: SubagentProgressBatch) -> None:
        session_queue.record_subagent_progress(msg.conversation_id, batch)
        try:
            await _push_task_runtime_event(
                state,
                deps=deps,
                conversation_id=msg.conversation_id,
                session_id=session_id,
            )
        except Exception:
            logger.warning("web.subagent_progress.send_failed", exc_info=True)

    async def _subagent_terminal_sink(record: ParallelTaskRuntimeRecord) -> None:
        # 后台任务在父 run 之外结束：终态已落库，用前端已有的 subagent_trace
        # 契约推一条终态条目，避免任务面板永久停在「进行中」。
        partial_status = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "aborted",
        }.get(getattr(record, "status", ""), "failed")
        started = (
            getattr(record, "started_at_epoch_ms", None)
            or getattr(record, "created_at_epoch_ms", 0)
            or 0
        )
        finished = getattr(record, "completed_at_epoch_ms", None) or started
        batch = SubagentProgressBatch(
            progress_batch_id=getattr(record, "run_id", None) or record.task_id,
            batch_seq=1,
            items=[
                SubagentProgressItem(
                    sub_session_id=f"background:{record.task_id}",
                    subagent_name=record.agent_name or "subagent",
                    turn=1,
                    max_turns=1,
                    partial_status=partial_status,
                    duration_so_far=max(0.0, (finished - started) / 1000.0),
                )
            ],
        )
        session_queue.record_subagent_progress(msg.conversation_id, batch)
        invalidate_tree_cache(session_id)
        try:
            await _push_task_runtime_event(
                state,
                deps=deps,
                conversation_id=msg.conversation_id,
                session_id=session_id,
            )
        except Exception:
            logger.warning("web.subagent_terminal.send_failed", exc_info=True)

    if web_deps is not None:
        deps = deps.with_request_context(
            progress_sink=_subagent_progress_sink,
            terminal_sink=_subagent_terminal_sink,
            channel="web",
        )
    else:
        deps = deps.with_request_context(
            progress_sink=_subagent_progress_sink,
            terminal_sink=_subagent_terminal_sink,
        )

    tool_workspace_path = _resolve_tool_workspace_path(
        user_id=str(state.user_id),
        workspace_id=effective_workspace_id,
    )

    base_questions_task: asyncio.Task[list[str]] | None = None
    refined_questions_task: asyncio.Task[list[str]] | None = None
    streamed_assistant_preview = ""
    tool_call_meta: dict[str, tuple[str, dict[str, Any]]] = {}
    active_tool_call_ids: set[str] = set()
    output_files: list[str] = []
    # session 级 outbox 扫描（只看本 session 的产出）
    from prismclaw.infra.workspace_paths import ensure_session_layout

    session_root = ensure_session_layout(tool_workspace_path, session_id)
    _outbox_scope = Scope(kind="session", root=str(session_root), prefixes=("outbox",))
    outbox_baseline = ArtifactScanner(session_root).scan(_outbox_scope)
    file_changes: list[dict[str, Any]] = []
    projector = TaskExecutionProjector(workspace_root=str(tool_workspace_path))
    last_reasoning_push = 0.0
    session_queue.get_or_create_web_runtime_projection(msg.conversation_id).timeline_snapshot = None
    assistant_message_id: str | None = None
    terminal_done_sent = False

    # —— chat.start：新任务的第一个事件，早于任何 task.timeline / task.runtime / chat.delta。
    # 前端收到后无条件清掉上一轮残留的 timeline / activeTaskRuntime / phase，
    # 避免"新任务开始时先渲染旧任务的执行过程，等第一帧 task.timeline 到达才被覆盖"。
    try:
        await send_event(
            state,
            SERVER_CHAT_START,
            msg.conversation_id,
            {"conversation_id": msg.conversation_id},
        )
    except Exception:
        logger.warning("chat.start send failed (non-fatal)", exc_info=True)

    run_presence = getattr(web_deps, "run_presence", None)
    presence_task: asyncio.Task[Any] | None = None

    async def _emit_terminal_done(
        *,
        final_message: str | list[object],
        usage: dict[str, Any] | None,
        aborted: bool,
        message_id: str | None = None,
    ) -> None:
        nonlocal assistant_message_id, terminal_done_sent
        if terminal_done_sent:
            return
        # done 发出即代表 run 结束。生命周期必须先于帧翻转：否则前端收到 done
        # 立即 sync 时仍被判为 active，刚完成的正文被标 in_progress，要等
        # 30s 看门狗才收尾（表现为"回答完成但一直转圈"）。
        rc.active = False
        _set_loading_active(
            session_queue,
            conversation_id=msg.conversation_id,
            active=False,
        )
        assistant_message_id = message_id or assistant_message_id
        # 在场证明与 run 同生命周期：先停心跳再删键，然后才发 done。
        # 否则 done 后的对账会读到残留 active，把已结束的一轮判成运行中。
        if presence_task is not None:
            presence_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await presence_task
        if run_presence is not None:
            await run_presence.clear(msg.conversation_id)
        # 指数退避重试：覆盖 WS 短暂断开（网络抖动/VPN 切换/缓冲区满）。
        # 总窗口 ~31.5s，远超大多数 WS 重连时间（通常 <10s）。
        # 退避序列：0.5, 1, 2, 4, 6, 8, 10, 15 秒（8 次）
        _backoff_schedule = [0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0]
        seq = 0
        for attempt, delay in enumerate(_backoff_schedule):
            seq, sent = await send_event(
                state,
                SERVER_CHAT_DONE,
                msg.conversation_id,
                {
                    "final_message": final_message,
                    "message_id": assistant_message_id,
                    "usage": usage,
                    "aborted": aborted,
                },
            )
            if sent:
                terminal_done_sent = True
                break
            if attempt < len(_backoff_schedule) - 1:
                await asyncio.sleep(delay)
        if not terminal_done_sent:
            logger.warning(
                "chat.done event permanently lost after %d attempts (conversation_id=%s, seq=%d)",
                len(_backoff_schedule),
                msg.conversation_id,
                seq,
            )

    async def _finalize_terminated_run(
        *,
        error_code: str,
        message: str,
        aborted: bool,
    ) -> None:
        """异常终止的唯一收尾路径：正文只持久化一次、终态只发送一次。

        软着陆（超时 / 预算耗尽）：已流出的正文本身就是连贯答案
        （模型在正常生成中被截断，不会输出系统状态词），直接落库 + 发
        done，不追加任何系统说明。error 帧也省略（无额外信息量）。

        其余错误（阻塞 / 内部 / 上游 LLM）：保留 error 帧交代真实原因；
        已流出的正文照常落库并随 done 下发，避免刷新后正文消失。

        用户 abort：不落 partial（看到哪算哪），只发 done(aborted=true)。
        """
        nonlocal assistant_message_id
        # 单次收尾护栏：终态已送达、或本轮已有落库挂靠点时不再重复落库，
        # 避免「ErrorEvent 收尾后又抛异常」产生第二条内容相同的 partial。
        if terminal_done_sent:
            return
        # 异常/中止收尾：先把 redactor 滞留的尾巴推出并计入正文，否则已流出的
        # 结尾会缺字，落库内容与用户已看到的正文不一致。
        try:
            await _emit_text(_drain_text())
        except Exception:
            logger.warning("web.chat.path_redactor.flush_failed", exc_info=True)
        if _current_part[0]:
            full_transcript_parts.append(_current_part[0])
            _current_part[0] = ""
        body = "\n\n".join(p for p in full_transcript_parts if p).strip()

        if error_code == "aborted":
            # 用户主动中止：不发 error 帧、不追加系统说明，但**已流出的正文必须
            # 落库**。done 只是"去服务端对账"的信号，正文一律来自展示投影——
            # 不落库的话，中止后客户端 sync 到 idle 会把这段正文清掉（旧前端是
            # 在 done 里本地合成 partial 消息保住的，新契约下该兜底已删除）。
            if body or projector.has_content():
                if assistant_message_id is None:
                    assistant_message_id = (
                        await _persist_partial_assistant_message(
                            deps,
                            session_id=session_id,
                            projector=projector,
                            transcript_text=body,
                        )
                        or assistant_message_id
                    )
                if assistant_message_id and projector.has_content():
                    await _persist_execution_timeline(
                        deps,
                        session_id=session_id,
                        message_id=assistant_message_id,
                        projector=projector,
                    )
            await _emit_terminal_done(
                final_message=body,
                usage={},
                aborted=True,
                message_id=assistant_message_id,
            )
            return

        soft_landing = error_code in _SOFT_LANDING_CODES
        if not soft_landing and message:
            await send_event(
                state,
                SERVER_ERROR,
                msg.conversation_id,
                {"message": message, "code": error_code},
            )

        if not body and not projector.has_content():
            # 软着陆的前提是「已流出的正文自成答案」。预算在 prepare 阶段耗尽
            # （最后一轮还没调 LLM）时没有任何正文，静默 done 会让前端表现为
            # 「任务直接断了」。此时必须把终止原因送到前端。
            if soft_landing:
                await send_event(
                    state,
                    SERVER_ERROR,
                    msg.conversation_id,
                    {"message": message or "任务已终止", "code": error_code},
                )
            await _emit_terminal_done(final_message="", usage={}, aborted=aborted, message_id=None)
            return

        content = body
        if assistant_message_id is None:
            assistant_message_id = (
                await _persist_partial_assistant_message(
                    deps,
                    session_id=session_id,
                    projector=projector,
                    transcript_text=content,
                )
                or assistant_message_id
            )
        # 与正常收尾同契约：done 之前把执行过程挂到 partial 上，否则中止/超时
        # 场景的 timeline 会错过客户端水位，只能靠手动刷新才看得到。
        if assistant_message_id and projector.has_content():
            await _persist_execution_timeline(
                deps,
                session_id=session_id,
                message_id=assistant_message_id,
                projector=projector,
            )
        await _emit_terminal_done(
            final_message=content,
            usage={},
            aborted=aborted,
            message_id=assistant_message_id,
        )

    try:
        base_questions_task = asyncio.create_task(
            _generate_followup_questions_for_request(
                deps,
                session_id=session_id,
                user_message=user_text,
                raw_attachments=msg.attachments,
                assistant_text=None,
                max_turns=5,
            )
        )
        with contextlib.suppress(Exception):
            session_queue._track_detached(base_questions_task)
    except Exception:
        base_questions_task = None

    lifecycle_task: asyncio.Task[Any] | None = None
    run_projection_task: asyncio.Task[Any] | None = None
    # 正文累积状态在 try 之前初始化：except 崩溃路径引用它们构建 partial
    # 消息持久化（不变量 I3：final_message == 全量流式文本），必须保证
    # rc.active=True 之后的任何异常入口触达时均已绑定。
    full_transcript_parts: list[str] = []
    _current_part: list[str] = [""]
    # 跨分片路径脱敏：按 token 切流的绝对路径可能被切断，逐片替换会把
    # `/tmp/g` 替成 [内部路径] 后让后缀 `z_pages/x.html` 漏出。这里滞留
    # 可能是路径中段的尾部，等下一片拼接后统一处理。
    path_redactor = StreamingPathRedactor()
    # 模型把原生工具调用写成纯文本（端点缺 tool-call parser）时，标记会被
    # 分片切成 `<|DSML|tool` + `_calls>` 之类，逐片清理无法识别。先由
    # MarkupStreamFilter 吞掉标记（含未闭合块），再交给路径脱敏处理正文。
    markup_filter = MarkupStreamFilter()

    def _feed_text(raw: str) -> str:
        """分片入口：先剥工具标记，再做跨分片路径脱敏。"""
        return path_redactor.feed(markup_filter.feed(raw))

    def _drain_text() -> str:
        """取回两级过滤器滞留的尾巴（标记未闭合块在此丢弃）。"""
        return path_redactor.feed(markup_filter.flush()) + path_redactor.flush()

    async def _emit_text(text: str) -> None:
        """推送并累积一段**已脱敏**正文。

        正文有五个消费方必须完全同源，否则「用户看到的」与「落库/时间线里的」
        会分叉：chat.delta 推送、_current_part 转录累积（最终落库）、projector
        迭代文本、runtime projection 的 streaming_text、追问生成的 preview。
        新增消费方一律加在这里，不要在各分支就地追加。
        """
        nonlocal streamed_assistant_preview, refined_questions_task
        if not text:
            return
        await send_event(
            state,
            SERVER_CHAT_DELTA,
            msg.conversation_id,
            {"text": text, "segmentId": projector.current_segment},
        )
        _current_part[0] = f"{_current_part[0]}{text}"
        projector.on_iteration_text(text)
        session_queue.get_or_create_web_runtime_projection(
            msg.conversation_id
        ).streaming_text += text
        if refined_questions_task is None:
            streamed_assistant_preview = f"{streamed_assistant_preview}{text}"
            if len(streamed_assistant_preview) >= 400:
                try:
                    refined_questions_task = asyncio.create_task(
                        _generate_followup_questions_for_request(
                            deps,
                            session_id=session_id,
                            user_message=user_text,
                            raw_attachments=msg.attachments,
                            assistant_text=streamed_assistant_preview,
                            max_turns=5,
                        )
                    )
                    with contextlib.suppress(Exception):
                        session_queue._track_detached(refined_questions_task)
                except Exception:
                    refined_questions_task = None

    # try 必须覆盖 rc.active=True 之后的全部路径：rc.active 是 run 生命周期的
    # 权威信号，一旦置 True，其后任何 await 点的异常或取消都必须经 finally
    # 把它翻回 False。旧结构中下方首个 await 位于 try 之外——取消恰好落在
    # 该处时，CancelledError（BaseException，不经 except Exception）直接
    # 上抛而 finally 根本不在其保护范围内，rc.active 残留 True，
    # 形成「幽灵运行中」。
    try:
        rc.active = True
        if run_presence is not None:
            await run_presence.touch(msg.conversation_id)
            presence_task = asyncio.create_task(run_presence.heartbeat(msg.conversation_id))
        _set_loading_active(
            session_queue,
            conversation_id=msg.conversation_id,
            active=True,
        )
        await _push_task_runtime_event(
            state,
            deps=deps,
            conversation_id=msg.conversation_id,
            session_id=session_id,
        )
        # 所有自然语言 TextChunk（中间草稿迭代与最终答案）都即时 send_event(
        # SERVER_CHAT_DELTA) 推到正文 chat.delta，不做缓冲、不做打字机回放、不做延迟。
        # 全量正文由 full_transcript_parts 累积（每助手轮一段），在 Done 以
        # "\n\n".join 提交为 final_message（不变量 I3：final_message == 全量流式文本）。
        # 跨迭代分段由 join 的分隔符保证，不再单独发分隔符 delta。
        # ReasoningChunk 仍走 projector 思考区；ToolCall* 仍走 projector 时间线。

        chat_service = getattr(web_deps, "chat_service", None)
        if chat_service is None:
            await send_event(
                state,
                SERVER_ERROR,
                msg.conversation_id,
                {
                    "message": "Chat service not available",
                    "code": ProtocolErrorCode.INTERNAL_ERROR.value,
                },
            )
            return
        lifecycle_task = asyncio.create_task(
            _lifecycle_broadcaster(
                state,
                deps,
                session_queue,
                msg.conversation_id,
                session_id,
            )
        )
        run_projection_task = asyncio.create_task(
            _run_projection_flusher(
                deps,
                session_id=session_id,
                conversation_id=msg.conversation_id,
                session_queue=session_queue,
            )
        )
        async for event in chat_service.start_conversation(
            request,
            tool_workspace_path=tool_workspace_path,
            control=rc,
        ):
            if isinstance(event, IterationBoundary):
                if event.kind == "llm_start" and event.iteration > 1:
                    # 滞留的疑似路径尾部属于上一轮文本，必须在 on_iteration_boundary
                    # 推进 segment 之前吐出，否则会带上下一个 segmentId 落到新一轮。
                    await _emit_text(_drain_text())
                projector.on_iteration_boundary(event)
                if event.kind == "llm_start":
                    # 新迭代开始：把上一轮累积的转录段落 flush 进 full_transcript_parts，
                    if event.iteration > 1:
                        full_transcript_parts.append(_current_part[0])
                        _current_part[0] = ""
                        await _push_timeline_event(
                            state,
                            conversation_id=msg.conversation_id,
                            session_id=session_id,
                            snapshot=projector.build_snapshot_dict(assistant_message_id),
                        )
                elif event.kind == "llm_end":
                    # 迭代结束：文本已在 TextChunk 即时推送，此处无需任何回放/flush。
                    pass
                continue

            if isinstance(event, TextChunk):
                _set_loading_active(
                    session_queue,
                    conversation_id=msg.conversation_id,
                    active=False,
                )
                # 单遍实时流式：即时推送自然语言正文到 chat.delta，并累积进转录段落。
                # segmentId 随 payload 一起推 — 前端收到时如果 segmentId 变了（进入新
                # 迭代轮次），才先清 streamingText 再追加，避免 IterationBoundary 提前
                # 清空正文导致的"执行一段时间后正文消失"空白窗口。
                #
                # 路径过滤：LLM 在工具执行过程中可能看到宿主真实路径（bash 输出、
                # 工具结果里的物理落点），然后在回答里暴露出来。这些实现细节对用户
                # 没有意义，必须在实时推送链路就过滤掉——不能等历史回放时再处理
                # （用户在实时打字机效果中已经看到了）。按 token 切分的分片可能把
                # 一个绝对路径切成两半，故交给有状态 redactor 滞留疑似路径尾部，
                # 与下一片拼接后再统一替换。
                await _emit_text(_feed_text(event.text))

            elif isinstance(event, ReasoningChunk):
                _set_loading_active(
                    session_queue,
                    conversation_id=msg.conversation_id,
                    active=False,
                )
                projector.on_reasoning_delta(event.reasoning)
                session_queue.get_or_create_web_runtime_projection(
                    msg.conversation_id
                ).timeline_snapshot = projector.build_snapshot_dict(assistant_message_id)
                now = time.monotonic()
                if now - last_reasoning_push >= _REASONING_PUSH_INTERVAL_S:
                    reasoning_patch = projector.reasoning_patch()
                    if reasoning_patch is not None:
                        await _push_timeline_event(
                            state,
                            conversation_id=msg.conversation_id,
                            session_id=session_id,
                            patch=reasoning_patch,
                        )
                    last_reasoning_push = now
            elif isinstance(event, ToolCallStart):
                # 该轮会产生 tool_calls。此前已实时推送的 TextChunk 文本仍在正文展示；
                # 工具调用继续走 projector 时间线，无需清缓冲（已无正文缓冲）。
                _set_loading_active(
                    session_queue,
                    conversation_id=msg.conversation_id,
                    active=False,
                )
                tool_call_meta[event.tool_call_id] = (event.name, event.arguments)
                active_tool_call_ids.add(event.tool_call_id)
                web_runtime_proj = session_queue.get_or_create_web_runtime_projection(
                    msg.conversation_id
                )
                web_runtime_proj.tool_calls.append(
                    {
                        "id": event.tool_call_id,
                        "name": event.name,
                        "status": "running",
                        "arguments": event.arguments if isinstance(event.arguments, dict) else {},
                        "startedAt": int(time.time() * 1000),
                    }
                )
                patch = projector.on_tool_call_start(event, str(tool_workspace_path))
                web_runtime_proj.timeline_snapshot = projector.build_snapshot_dict(
                    assistant_message_id
                )
                if patch is not None:
                    await _push_timeline_event(
                        state,
                        conversation_id=msg.conversation_id,
                        session_id=session_id,
                        patch=patch,
                    )
                await _push_task_runtime_event(
                    state,
                    deps=deps,
                    conversation_id=msg.conversation_id,
                    session_id=session_id,
                )
            elif isinstance(event, ToolCallEnd):
                tool_name, tool_args = tool_call_meta.get(event.tool_call_id, ("", {}))
                if tool_name in ("present_files",) and not getattr(event.result, "is_error", False):
                    output_files.extend(
                        _extract_presented_paths(
                            tool_args,
                            tool_workspace_path,
                            session_root=session_root,
                        )
                    )

                ui_artifact = getattr(event.result, "ui_artifact", None)
                if ui_artifact is not None and ui_artifact.artifact_type == "file_changes":
                    for change in getattr(ui_artifact, "file_changes", []) or []:
                        try:
                            file_changes.append(change.model_dump(mode="python"))
                        except Exception:
                            logger.warning("web.file_changes.invalid_artifact", exc_info=True)

                result_text = _extract_tool_result_text(event)
                web_runtime_proj = session_queue.get_or_create_web_runtime_projection(
                    msg.conversation_id
                )
                is_error = getattr(event.result, "is_error", False)
                for tc in web_runtime_proj.tool_calls:
                    if tc.get("id") == event.tool_call_id:
                        tc["status"] = "error" if is_error else "done"
                        tc["endedAt"] = int(time.time() * 1000)
                        if not is_error:
                            tc["result"] = result_text[:2000] if result_text else None
                        break
                end_patch = projector.on_tool_call_end(event, str(tool_workspace_path))
                web_runtime_proj.timeline_snapshot = projector.build_snapshot_dict(
                    assistant_message_id
                )
                if end_patch is not None:
                    await _push_timeline_event(
                        state,
                        conversation_id=msg.conversation_id,
                        session_id=session_id,
                        patch=end_patch,
                    )
                active_tool_call_ids.discard(event.tool_call_id)
                if not active_tool_call_ids:
                    _set_loading_active(
                        session_queue,
                        conversation_id=msg.conversation_id,
                        active=True,
                    )
                await _push_task_runtime_event(
                    state,
                    deps=deps,
                    conversation_id=msg.conversation_id,
                    session_id=session_id,
                )
            elif isinstance(event, ToolClarifyRequest):
                # 澄清请求：推送点号顶层 type（tool.clarify_request），前端据此
                # 弹出提问卡片。生命周期相位由 agent_loop.set_phase 更新、由独立
                # lifecycle broadcaster 周期广播，此处不再重复推 SetPhasePatch。
                _set_loading_active(
                    session_queue,
                    conversation_id=msg.conversation_id,
                    active=True,
                )
                payload = event.model_dump()
                payload.pop("type", None)
                await send_event(
                    state,
                    SERVER_TOOL_CLARIFY_REQUEST,
                    msg.conversation_id,
                    payload,
                )
            elif isinstance(event, HeartbeatEvent):
                phase_patch = SetPhasePatch(
                    phase=HeartbeatPayload(
                        state=event.phase,
                        partial_tool_name=event.detail.partial_tool_name,
                        chars_so_far=event.detail.chars_so_far,
                        elapsed_s=event.elapsed_s,
                    )
                )
                await _push_timeline_event(
                    state,
                    conversation_id=msg.conversation_id,
                    session_id=session_id,
                    patch=phase_patch,
                )
            elif isinstance(event, Done):
                _set_loading_active(
                    session_queue,
                    conversation_id=msg.conversation_id,
                    active=False,
                )
                # flush 最后一段转录（最终答案轮无后续迭代触发 flush）。
                # 先吐 redactor 滞留的尾巴：它属于正文结尾，不推等于正文缺字，
                # 且落库与推送会不一致。
                await _emit_text(_drain_text())
                if _current_part[0]:
                    full_transcript_parts.append(_current_part[0])
                    _current_part[0] = ""

                if event.usage:
                    set_usage = getattr(session_queue, "set_last_usage", None)
                    if callable(set_usage):
                        set_usage(msg.conversation_id, event.usage)

                # final_message 只保留**最后一轮迭代**的文本，仅用于审计与非 Web
                # 通道；Web 端的正文一律来自展示消息投影（_build_display_messages
                # 的全轮拼接），二者不再共用同一个字段，避免"刷新前后两套正文"。
                final_msg = full_transcript_parts[-1] if full_transcript_parts else ""

                assistant_message_id = getattr(event, "assistant_message_id", None)
                # 先把执行过程与交付物落库，再发 done。done 的契约是"本轮已完全
                # 落库"：客户端收到后立即 sync 取定稿正文与元数据；若先发 done，
                # 这些晚落库的 CustomEntry seq 高于客户端水位，而前端进入 idle 后
                # 不再触发 sync，执行过程会缺失到手动刷新为止。
                if projector.has_content():
                    session_queue.get_or_create_web_runtime_projection(
                        msg.conversation_id
                    ).timeline_snapshot = projector.build_snapshot_dict(assistant_message_id)
                    if assistant_message_id:
                        await _persist_execution_timeline(
                            deps,
                            session_id=session_id,
                            message_id=assistant_message_id,
                            projector=projector,
                        )
                # 交付物确定策略：
                # - 优先：LLM 通过 present_files 主动声明的文件（LLM 知道哪些是最终交付物）
                # - 验证：自动扫描 outbox/ 确认声明文件确实存在且有变更
                # - 兜底：LLM 未调用 present_files 时，自动扫描 outbox/ 中有变更的文件
                #   （覆盖 agent 遗忘/被 doom-loop 打断的场景，避免已生成的交付物不展示）
                _current = ArtifactScanner(tool_workspace_path).scan(_outbox_scope)
                auto_scanned = [
                    path
                    for path, signature in _current.items()
                    if outbox_baseline.get(path) != signature
                ]
                auto_set = set(auto_scanned)
                if output_files:
                    # 取交集：LLM 声明 + 文件确实存在且有变更
                    validated = [f for f in output_files if f in auto_set]
                    # 交集为空时退回信任 LLM 声明（可能是路径格式差异或 baseline 误判）
                    all_output_files = validated if validated else output_files
                else:
                    # 兜底：agent 未声明时，展示 outbox/ 中本次执行新生成/变更的文件
                    all_output_files = auto_scanned
                final_output_files = _filter_existing_output_files(
                    _dedupe_simple(all_output_files),
                    tool_workspace_path,
                    session_root=session_root,
                )
                if final_output_files:
                    await send_event(
                        state,
                        SERVER_OUTBOX,
                        msg.conversation_id,
                        {
                            "message_id": assistant_message_id,
                            "files": final_output_files,
                        },
                    )
                    if assistant_message_id:
                        await persist_output_files_metadata(
                            deps.session_store,
                            session_id=session_id,
                            message_id=assistant_message_id,
                            files=final_output_files,
                        )

                if file_changes:
                    await send_event(
                        state,
                        SERVER_FILE_CHANGES,
                        msg.conversation_id,
                        {
                            "message_id": assistant_message_id,
                            "changes": file_changes,
                        },
                    )
                    if assistant_message_id:
                        await _persist_file_changes_metadata(
                            deps.session_store,
                            session_id=session_id,
                            message_id=assistant_message_id,
                            changes=file_changes,
                        )
                await _emit_terminal_done(
                    final_message=final_msg,
                    usage=event.usage,
                    aborted=False,
                    message_id=assistant_message_id,
                )
                _spawn_background(
                    _push_reference_event(
                        state,
                        deps=deps,
                        conversation_id=msg.conversation_id,
                        session_id=session_id,
                        assistant_message_id=assistant_message_id,
                    ),
                    label="reference",
                    session_queue=session_queue,
                )
                if base_questions_task is not None or refined_questions_task is not None:
                    _spawn_background(
                        _await_questions_and_push(
                            state,
                            conversation_id=msg.conversation_id,
                            assistant_message_id=assistant_message_id,
                            base_questions_task=base_questions_task,
                            refined_questions_task=refined_questions_task,
                        ),
                        label="questions",
                        session_queue=session_queue,
                    )
                await _push_task_runtime_event(
                    state,
                    deps=deps,
                    conversation_id=msg.conversation_id,
                    session_id=session_id,
                )
            elif isinstance(event, ErrorEvent):
                _set_loading_active(
                    session_queue,
                    conversation_id=msg.conversation_id,
                    active=False,
                )
                # 异常退出：正文已实时推送到正文区，收尾统一走 _finalize_terminated_run。
                # 中止不在这里短路：done 已不再携带正文，中止的 partial 必须由本
                # 路径落库后再发 done，客户端才能在 idle 对账后仍看到已流出的内容。
                aborted = event.error_code == "aborted"
                await _finalize_terminated_run(
                    error_code=event.error_code,
                    message=event.message,
                    aborted=aborted,
                )
                if projector.has_content():
                    # 只更新运行态快照供实时消费；落库已在 done 之前完成
                    # （见 _finalize_terminated_run），此处不再重复写 CustomEntry。
                    err_snapshot_mid = assistant_message_id
                    session_queue.get_or_create_web_runtime_projection(
                        msg.conversation_id
                    ).timeline_snapshot = projector.build_snapshot_dict(err_snapshot_mid)
                await _push_task_runtime_event(
                    state,
                    deps=deps,
                    conversation_id=msg.conversation_id,
                    session_id=session_id,
                )
            else:
                # 事件分发契约：任何未知事件类型不得静默吞掉。记录告警，
                # 从根上暴露契约缺口（新增事件类型忘了补 handler 时立即可见）。
                logger.warning(
                    "web.chat.unhandled_event_type=%s conversation_id=%s",
                    type(event).__name__,
                    msg.conversation_id,
                )
        if not terminal_done_sent:
            logger.warning(
                "stream ended without Done event; forcing chat.done (conversation_id=%s)",
                msg.conversation_id,
            )
            # 流式在无 Done 情况下结束：已实时推送的正文同样必须落库再 done，
            # 否则客户端对账到 idle 后这段正文会消失。复用唯一收尾路径。
            await _finalize_terminated_run(
                error_code="stream_truncated",
                message="",
                aborted=False,
            )
    except Exception:
        logger.exception("error running chat for conversation %s", msg.conversation_id)
        # runner 崩溃与 ErrorEvent 同族：统一收尾（error 帧交代崩溃，已流出的
        # 正文照常落库保留执行现场）。
        await _finalize_terminated_run(
            error_code=ProtocolErrorCode.INTERNAL_ERROR.value,
            message="Internal error",
            aborted=False,
        )
        try:
            if projector.has_content():
                # 挂靠到收尾落库的 partial 消息上，让执行过程可持久化回放。
                exc_snapshot_mid = assistant_message_id
                session_queue.get_or_create_web_runtime_projection(
                    msg.conversation_id
                ).timeline_snapshot = projector.build_snapshot_dict(exc_snapshot_mid)
                if exc_snapshot_mid:
                    await _persist_execution_timeline(
                        deps,
                        session_id=session_id,
                        message_id=exc_snapshot_mid,
                        projector=projector,
                    )
                await _push_task_runtime_event(
                    state,
                    deps=deps,
                    conversation_id=msg.conversation_id,
                    session_id=session_id,
                )
        except Exception:
            logger.warning("web.chat.finalize_timeline.failed", exc_info=True)
    finally:
        # 终态清理顺序（关键不变量）：全部为同步语句（无 await），rc.active
        # 最先翻转——任何取消/异常都不会使 run 残留在 running 列表。否则
        # 刷新后 GET /conversations/running 仍列出它，前端 hydrateRun 重建
        # 流式气泡，使已落库的执行过程（timeline）被渲染成「第二段」
        # （执行过程被分割成两段）。
        if lifecycle_task is not None:
            lifecycle_task.cancel()
        if run_projection_task is not None:
            run_projection_task.cancel()
        # 同步内存清理（无 await，绝不被取消打断）：先把 run 移出 running 列表。
        # rc.active 是「run 进行中」的权威生命周期信号（is_conversation_running
        # 判活依据），最先翻转——后续任一步骤异常也不会残留 True。
        rc.active = False
        try:
            session_queue.settle_steering_after_run(msg.conversation_id)
        except Exception:
            logger.warning(
                "web.chat.steering_settle.failed conversation_id=%s",
                msg.conversation_id,
                exc_info=True,
            )
        try:
            _set_loading_active(
                session_queue,
                conversation_id=msg.conversation_id,
                active=False,
            )
        except Exception:
            logger.warning(
                "web.chat.set_loading_active.failed conversation_id=%s",
                msg.conversation_id,
                exc_info=True,
            )
        session_queue.finish_web_runtime(msg.conversation_id)
        # 在场证明最后清理：先同步取消心跳，再删键。此处若被取消，键也会在
        # RUN_TTL_SECONDS 内自然过期，非 owner worker 最多短暂误判 active。
        if presence_task is not None:
            presence_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await presence_task
        if run_presence is not None:
            await run_presence.clear(msg.conversation_id)
