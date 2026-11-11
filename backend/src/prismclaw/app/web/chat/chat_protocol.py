from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel

from prismclaw.app.web.protocol.messages import (
    ChatSendMessage,
)
from prismclaw.app.web.connection import ConnectionState
from prismclaw.schema.agent.run_control_types import RunControl
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.content.streaming import SubagentProgressBatch

logger = logging.getLogger(__name__)

MessageHandler = Callable[[ChatSendMessage], Coroutine[Any, Any, None]]

# 同一会话同时处于 steering 通道（已标记待转移 + 已入 steering 队列）的消息上限。
# 超限时 mark_steered 拒绝（前端收到 cap_reached 回执），消息留在排队队列。
_STEERING_CAP = 10

# mark_steered / mark_cancelled 的结果码。
STEER_OK = "steered"
STEER_ALREADY = "already_steered"
STEER_NOT_FOUND = "not_found"
STEER_CAP_REACHED = "cap_reached"
CANCEL_OK = "cancelled"

_SUBAGENT_TERMINAL_STATUSES = frozenset({"completed", "failed", "aborted"})


# ── Inlined approval runtime (previously channels.web.approval_runtime) ──────
# Retained for AskUserQuestion/clarification flows and SessionQueue lifecycle.
# The old ToolApprovalHook pipeline is removed; these models support the
# remaining interactive-user wait mechanism (clarify, confirm).

ApprovalLifecycleState = Literal[
    "pending",
    "approved",
    "denied",
    "stale",
]
ApprovalDecisionSource = Literal["user", "auto:stale", "auto:shutdown"]


class PendingApprovalRecord(BaseModel):
    approval_id: str
    conversation_id: str
    run_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    state: ApprovalLifecycleState = "pending"
    created_at_ms: int


class ApprovalDecisionRecord(BaseModel):
    approval_id: str
    approved: bool
    decided_by: ApprovalDecisionSource
    decided_at_ms: int
    answer: str | None = None


@dataclass
class _ApprovalRuntimeEntry:
    record: PendingApprovalRecord
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: ApprovalDecisionRecord | None = None


class _ApprovalRuntime:
    """Minimal in-process approval/clarification wait mechanism.

    Extracted from the deleted approval_runtime.py; used by SessionQueue
    for create_pending / wait_for_decision / submit_user_decision.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _ApprovalRuntimeEntry] = {}

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _key(*, conversation_id: str, run_id: str, turn_id: str, tool_call_id: str) -> str:
        return ":".join((conversation_id, run_id, turn_id, tool_call_id))

    def create_pending(
        self,
        *,
        conversation_id: str,
        tool_call_id: str,
        tool_name: str,
        run_id: str,
        turn_id: str,
    ) -> PendingApprovalRecord:
        approval_id = self._key(
            conversation_id=conversation_id,
            run_id=run_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
        )
        now_ms = self._now_ms()
        entry = _ApprovalRuntimeEntry(
            record=PendingApprovalRecord(
                approval_id=approval_id,
                conversation_id=conversation_id,
                run_id=run_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                state="pending",
                created_at_ms=now_ms,
            ),
        )
        self._entries[approval_id] = entry
        return entry.record

    async def wait_for_decision(
        self,
        *,
        approval_id: str,
    ) -> ApprovalDecisionRecord:
        """无限等待用户决策——只有用户提交（``submit_user_decision``）或 run
        被 abort/stale 才会解除。等待期间绝不用「超时假答案」让执行继续。"""
        entry = self._entries.get(approval_id)
        if entry is None:
            raise KeyError(f"missing approval entry for {approval_id}")
        if entry.decision is not None:
            return entry.decision
        await entry.event.wait()
        if entry.decision is None:
            now_ms = self._now_ms()
            entry.record.state = "stale"
            entry.decision = ApprovalDecisionRecord(
                approval_id=entry.record.approval_id,
                approved=False,
                decided_by="auto:stale",
                decided_at_ms=now_ms,
            )
        return entry.decision

    def submit_user_decision(
        self,
        *,
        approval_id: str,
        approved: bool,
        answer: str | None = None,
    ) -> bool:
        entry = self._entries.get(approval_id)
        if entry is None or entry.record.state != "pending":
            return False
        entry.record.state = "approved" if approved else "denied"
        entry.decision = ApprovalDecisionRecord(
            approval_id=entry.record.approval_id,
            approved=approved,
            decided_by="user",
            decided_at_ms=self._now_ms(),
            answer=answer,
        )
        entry.event.set()
        return True

    def mark_stale(self, *, approval_id: str) -> None:
        entry = self._entries.get(approval_id)
        if entry is None:
            return
        if entry.record.state in {"stale"}:
            return
        entry.record.state = "stale"
        if entry.decision is None:
            entry.decision = ApprovalDecisionRecord(
                approval_id=entry.record.approval_id,
                approved=False,
                decided_by="auto:stale",
                decided_at_ms=self._now_ms(),
            )
        entry.event.set()

    def mark_stale_for_conversation(self, conversation_id: str) -> None:
        for approval_id, entry in list(self._entries.items()):
            if entry.record.conversation_id == conversation_id:
                self.mark_stale(approval_id=approval_id)

    def pending_tool_call_ids(self, conversation_id: str) -> set[str]:
        return {
            entry.record.tool_call_id
            for entry in self._entries.values()
            if entry.record.conversation_id == conversation_id and entry.record.state == "pending"
        }

    def clear(self) -> None:
        for entry in self._entries.values():
            entry.event.set()
        self._entries.clear()

    async def shutdown(self, grace_s: float = 0.05) -> None:
        if not self._entries:
            return
        now_ms = self._now_ms()
        for entry in list(self._entries.values()):
            if entry.record.state == "pending":
                entry.record.state = "stale"
            if entry.decision is None:
                entry.decision = ApprovalDecisionRecord(
                    approval_id=entry.record.approval_id,
                    approved=False,
                    decided_by="auto:shutdown",
                    decided_at_ms=now_ms,
                )
            entry.event.set()
        if grace_s and grace_s > 0:
            try:
                await asyncio.sleep(grace_s)
            except (asyncio.CancelledError, BaseException):
                pass
        self._entries.clear()


# ── End inlined approval runtime ─────────────────────────────────────────────


@dataclass
class ConversationWebRuntimeProjection:
    loading_active: bool = False
    subagent_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    approval_pending_tool_call_ids: set[str] = field(default_factory=set)
    event_seq: int = 0
    timeline_snapshot: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # 待用户回答的 HITL 澄清态（AskUserQuestion）。非空即表示 run 正因提问而
    # 暂停；由内存投影进 GET /run payload 与 run.snapshot 推送，刷新/重连后
    # 前端重建提问卡片并作答。
    # 形态：{approval_id, tool_call_id, tool_name, questions:[...], created_at_ms}
    pending_clarification: dict[str, Any] | None = None
    # 后端累积的助理正文；GET /run 内存直读与 run.snapshot 推送的实时来源。
    streaming_text: str = ""
    # 上次落盘 payload 的 sha1 签名，用于去重写优化（内存态，不持久化、不进 payload）。
    _run_projection_sig: str = ""

    def is_active(self) -> bool:
        # 判活口径 = 「有正在执行的痕迹」：加载中 / 非终态子代理 / 待审批 /
        # 运行中工具调用。最后一项覆盖无子代理的长工具场景（单次 MCP/子代理
        # 分发可执行数分钟，期间会话对 GET /conversations/running 必须可见，
        # 否则刷新后前端不水合、长任务从 UI 消失）。run 结束时
        # finish_web_runtime 会把残留 running 翻成终态，防陈旧误判。
        return bool(
            self.loading_active
            or any(
                str(item.get("partial_status", "")) not in _SUBAGENT_TERMINAL_STATUSES
                for item in self.subagent_items.values()
            )
            or self.approval_pending_tool_call_ids
            or any(str(tc.get("status", "")) == "running" for tc in self.tool_calls)
        )

    def next_event_seq(self) -> int:
        self.event_seq += 1
        return self.event_seq


def _set_loading_active(
    queue: "SessionQueue",
    *,
    conversation_id: str,
    active: bool,
) -> None:
    projection = queue.get_or_create_web_runtime_projection(conversation_id)
    projection.loading_active = active


class SessionQueue:
    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self._task_manager = task_manager
        self._queues: dict[str, asyncio.Queue[tuple[ChatSendMessage, MessageHandler]]] = {}
        self._consumers: dict[str, str] = {}
        self._busy: dict[str, bool] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._run_controls: dict[str, RunControl] = {}
        self._approval_runtime = _ApprovalRuntime()
        self._last_usage: dict[str, dict[str, int]] = {}
        self._last_tier: dict[str, str] = {}
        self._web_runtime: dict[str, ConversationWebRuntimeProjection] = {}
        self._detached_tasks: set[asyncio.Task[Any]] = set()
        self._closed: bool = False
        # 生命周期相位：由 agent_loop.wait_for_clarification 进入/退出时更新，
        # 由 chat_runner 的独立 lifecycle broadcaster 周期读取并广播。与事件流
        # 解耦——事件流在 HITL 长阻塞（600s）期间不 yield，此状态仍可被读取。
        self._phase: str | None = None
        self._phase_started_monotonic: float = 0.0
        self._phase_started_epoch_ms: int = 0
        # steering 通道：排队消息可被提升（chat.steer → 注入当前 run）或取消
        # （chat.cancel → 跳过执行）。(msg, handler) 元组在排队队列与 steering
        # 队列之间流转，handler 始终随行——drain 兜底转排队后仍可消费。
        # asyncio.Queue 不支持按项移除，故用平行索引 + 标记集：标记在 consumer
        # 取出时生效（单 consumer 串行取出，标记检查无竞态）。
        self._steering: dict[str, deque[tuple[ChatSendMessage, MessageHandler]]] = {}
        self._queue_index: dict[str, dict[str, ChatSendMessage]] = {}
        self._steered: dict[str, set[str]] = {}
        self._cancelled: dict[str, set[str]] = {}

    def set_task_manager(self, tm: TaskManager) -> None:
        self._task_manager = tm

    def set_phase(self, phase: str | None) -> None:
        """更新当前生命周期相位，供 broadcaster 周期性广播（含 elapsed_s）。"""
        self._phase = phase
        self._phase_started_monotonic = time.monotonic()
        self._phase_started_epoch_ms = int(time.time() * 1000)

    def get_phase(self) -> tuple[str | None, float]:
        """返回 (phase, phase_started_monotonic)。phase 为 None 表示非长静默状态。"""
        return self._phase, self._phase_started_monotonic

    def get_phase_epoch_ms(self) -> int:
        """phase 开始的 Unix epoch 毫秒；未进入过 phase 时为 0。"""
        return self._phase_started_epoch_ms

    def _track_detached(self, task: asyncio.Task[Any]) -> None:
        self._detached_tasks.add(task)

        def _on_done(t: asyncio.Task[Any]) -> None:
            self._detached_tasks.discard(t)
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("detached task finished with exception", exc_info=True)

        task.add_done_callback(_on_done)

    def _consumer_running(self, conversation_id: str) -> bool:
        tid = self._consumers.get(conversation_id)
        if tid is None or self._task_manager is None:
            return False
        state = self._task_manager.get_state(tid)
        return state == "running"

    def get_web_runtime_projection(
        self,
        conversation_id: str,
    ) -> ConversationWebRuntimeProjection | None:
        return self._web_runtime.get(conversation_id)

    def get_or_create_web_runtime_projection(
        self,
        conversation_id: str,
    ) -> ConversationWebRuntimeProjection:
        projection = self._web_runtime.get(conversation_id)
        if projection is None:
            projection = ConversationWebRuntimeProjection()
            self._web_runtime[conversation_id] = projection
        return projection

    def _cleanup_web_runtime_projection_if_inactive(self, conversation_id: str) -> None:
        projection = self._web_runtime.get(conversation_id)
        if projection is not None and not projection.is_active():
            self._web_runtime.pop(conversation_id, None)

    def is_conversation_running(self, conversation_id: str) -> bool:
        """该会话的 run 是否进行中——查询层统一判活入口。

        projection.is_active() 只覆盖流式细分态（loading/子代理/待审批/
        running 工具）；LLM 正文流式生成阶段（chat_runner 在 TextChunk/
        ReasoningChunk 处置 loading_active=False、工具已全部终态）四字段
        全空，会把进行中的 run 误判为不活跃 → /conversations/running 漏列
        + GET /run 判 null → 刷新后前端水合断链。RunControl.active 由
        channel adapter 在 run 开始（chat_runner rc.active=True）/结束
        （finally 同步段最前翻转 False）维护，是「run 是否进行中」的权威
        生命周期信号。

        运行中投影必然存在：chat_runner 先创建投影（get_or_create）再置
        rc.active=True，且 run 进行途中投影无删除路径（_web_runtime.pop
        仅出现在 finish_web_runtime 与 consumer finally，两者都发生在
        rc.active 翻 False 之后）——故按 _web_runtime 键遍历的消费方
        不会因此漏列。
        """
        projection = self._web_runtime.get(conversation_id)
        if projection is not None and projection.is_active():
            return True
        rc = self._run_controls.get(conversation_id)
        return rc is not None and rc.is_active()

    def list_running_conversations(self) -> list[str]:
        return [
            conversation_id
            for conversation_id in self._web_runtime
            if self.is_conversation_running(conversation_id)
        ]

    def finish_web_runtime(self, conversation_id: str) -> None:
        projection = self.get_web_runtime_projection(conversation_id)
        if projection is None:
            return
        self._approval_runtime.mark_stale_for_conversation(conversation_id)
        projection.loading_active = False
        projection.subagent_items.clear()
        # run 已终结，任何残留的 pending_clarification 都是陈旧态：
        # 要么用户已作答（set_approval_decision 理论上已清），要么 run 结束
        # 时用户未作答（标记 stale 后直接清）。不清的话内存投影会带着它进
        # GET /run payload 与 run.snapshot，前端 hydrateRun 重建
        # clarifyRequest → 刷新后又弹一张陈旧的提问卡片（用户已作答 /
        # run 已结束但卡片还在）。
        projection.pending_clarification = None
        # 残留 running 的工具调用必然是被中止/未收尾的陈旧态。翻成 error：
        # 既避免 is_active 因 running 永远判活（会话被永久列出），也不谎报成功。
        for tc in projection.tool_calls:
            if tc.get("status") == "running":
                tc["status"] = "error"
                tc["endedAt"] = int(time.time() * 1000)
        projection.approval_pending_tool_call_ids = self._approval_runtime.pending_tool_call_ids(
            conversation_id
        )
        self._cleanup_web_runtime_projection_if_inactive(conversation_id)

    def record_subagent_progress(
        self,
        conversation_id: str,
        batch: SubagentProgressBatch,
    ) -> None:
        projection = self.get_or_create_web_runtime_projection(conversation_id)
        for item in batch.items:
            projection.subagent_items[item.sub_session_id] = item.model_dump(mode="json")

    async def enqueue(
        self,
        conversation_id: str,
        msg: ChatSendMessage,
        handler: MessageHandler,
    ) -> int:
        if conversation_id not in self._queues:
            self._queues[conversation_id] = asyncio.Queue()

        pending = self._queues[conversation_id].qsize()
        busy = self._busy.get(conversation_id, False)
        position = pending + (1 if busy else 0)

        await self._queues[conversation_id].put((msg, handler))
        self._register_queue_index(conversation_id, msg)
        self._ensure_consumer(conversation_id)

        return position

    def _register_queue_index(self, conversation_id: str, msg: ChatSendMessage) -> None:
        """登记排队消息的 client_message_id 索引（供提升/取消按 id 定位）。"""
        mid = msg.client_message_id
        if not mid:
            return
        self._queue_index.setdefault(conversation_id, {})[mid] = msg

    def _ensure_consumer(self, conversation_id: str) -> None:
        """保证该会话的排队消费者存活（enqueue 与兜底转排队共用）。"""
        if self._closed or self._consumer_running(conversation_id) or self._task_manager is None:
            return
        task_id = self._task_manager.spawn(
            f"web-consumer:{conversation_id}",
            self._consume(conversation_id),
            category="consumer",
            owner=conversation_id,
        )
        self._consumers[conversation_id] = task_id

    # ── steering 通道：排队消息提升 / 取消 ──────────────────────────────

    def mark_steered(self, conversation_id: str, client_message_id: str) -> tuple[str, int]:
        """把一条排队消息标记为 steering——consumer 取出时转入 steering 队列。

        返回 (结果码, 操作后 steering 通道占用数 = 已转移 + 待转移标记)。
        实际转移发生在 consumer 取出时（消息本体温留排队队列，保持 FIFO
        时序与单消费者模型不变）。
        """
        if not client_message_id or client_message_id not in self._queue_index.get(conversation_id, {}):
            return (STEER_NOT_FOUND, self._steering_channel_len(conversation_id))
        steered = self._steered.setdefault(conversation_id, set())
        if client_message_id in steered:
            return (STEER_ALREADY, self._steering_channel_len(conversation_id))
        if self._steering_channel_len(conversation_id) >= _STEERING_CAP:
            return (STEER_CAP_REACHED, self._steering_channel_len(conversation_id))
        steered.add(client_message_id)
        return (STEER_OK, self._steering_channel_len(conversation_id))

    def mark_cancelled(self, conversation_id: str, client_message_id: str) -> str:
        """把一条排队消息标记为取消——consumer 取出时跳过执行。

        若该消息已标记提升但尚未转移，一并撤销提升标记：取消是更明确的
        用户意图，优先于提升（宁可不注入，也不注入刚被取消的内容）。
        """
        if not client_message_id or client_message_id not in self._queue_index.get(conversation_id, {}):
            return STEER_NOT_FOUND
        steered = self._steered.get(conversation_id)
        if steered is not None:
            steered.discard(client_message_id)
        self._cancelled.setdefault(conversation_id, set()).add(client_message_id)
        return CANCEL_OK

    def push_steering(self, conversation_id: str, msg: ChatSendMessage, handler: MessageHandler) -> None:
        """消息进入 steering 队列（consumer 转移时调用；cap 已在 mark 阶段把关）。"""
        self._steering.setdefault(conversation_id, deque()).append((msg, handler))

    def drain_steering(self, conversation_id: str) -> list[tuple[ChatSendMessage, MessageHandler]]:
        """引擎侧一次取空 steering 队列（FIFO 保序）。"""
        q = self._steering.get(conversation_id)
        if not q:
            return []
        items = list(q)
        q.clear()
        return items

    def drain_steering_to_queue(self, conversation_id: str) -> int:
        """兜底：把未消费的 steering 消息转回排队队列（降级 follow-up，不丢）。

        run 结束 / abort 时由 chat_runner finally 调用；consumer 已退出时
        重新拉起（转回的消息仍需被消费）。
        """
        q = self._steering.get(conversation_id)
        if not q:
            return 0
        items = list(q)
        q.clear()
        queue = self._queues.get(conversation_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[conversation_id] = queue
        for msg, handler in items:
            queue.put_nowait((msg, handler))
            self._register_queue_index(conversation_id, msg)
        self._ensure_consumer(conversation_id)
        return len(items)

    def settle_steering_after_run(self, conversation_id: str) -> int:
        """run 结束收口：未转移的提升标记失效（消息回到排队原位按 FIFO 执行），
        已转移的 steering 消息转回排队（降级 follow-up，不丢）。返回转回条数。

        _cancelled 标记不在此清除——取消是持久用户意图，不因 run 结束复活。
        由 chat_runner 的 _run_chat 收口区调用。
        """
        self._steered.pop(conversation_id, None)
        return self.drain_steering_to_queue(conversation_id)

    def _steering_len(self, conversation_id: str) -> int:
        return len(self._steering.get(conversation_id, ()))

    def _steering_channel_len(self, conversation_id: str) -> int:
        """steering 通道占用数 = 已转移进 deque 的 + 已标记待转移的。"""
        return self._steering_len(conversation_id) + len(self._steered.get(conversation_id, ()))

    def _clear_steering_state(self, conversation_id: str) -> None:
        """consumer 退出时清理该会话的 steering 状态。

        先兜底把未消费的 steering 消息转回排队（重新登记索引，用户仍可再次
        提升/取消），再清空标记集。_queue_index 不整体清除——转回排队的消息
        索引仍然有效；其条目在消息被取出时逐一移除。
        """
        self.drain_steering_to_queue(conversation_id)
        self._steering.pop(conversation_id, None)
        self._steered.pop(conversation_id, None)
        self._cancelled.pop(conversation_id, None)

    def queue_position(self, conversation_id: str) -> int:
        q = self._queues.get(conversation_id)
        pending = q.qsize() if q else 0
        busy = self._busy.get(conversation_id, False)
        return pending + (1 if busy else 0)

    def get_abort_event(self, conversation_id: str) -> asyncio.Event:
        return self.get_run_control(conversation_id).abort_event

    def get_run_control(self, conversation_id: str) -> RunControl:
        rc = self._run_controls.get(conversation_id)
        if rc is None:
            event = self._abort_events.get(conversation_id) or asyncio.Event()
            self._abort_events[conversation_id] = event
            rc = RunControl(abort_event=event)
            self._run_controls[conversation_id] = rc
        return rc

    def reset_abort_event(self, conversation_id: str) -> None:
        rc = self._run_controls.get(conversation_id)
        if rc is not None:
            rc.abort_event.clear()
            return
        if conversation_id in self._abort_events:
            self._abort_events[conversation_id].clear()

    def release_pending_approvals(self, conversation_id: str) -> None:
        self._approval_runtime.mark_stale_for_conversation(conversation_id)
        self.clear_pending_clarification(conversation_id)

    def create_pending(
        self,
        conversation_id: str,
        tool_call_id: str,
        tool_name: str,
        *,
        run_id: str,
        turn_id: str,
        questions: list[dict[str, Any]] | None = None,
    ) -> PendingApprovalRecord:
        pending = self._approval_runtime.create_pending(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=run_id,
            turn_id=turn_id,
        )
        projection = self.get_or_create_web_runtime_projection(conversation_id)
        projection.approval_pending_tool_call_ids.add(tool_call_id)
        # 澄清态（questions 非空）写入投影：run 因等用户作答而暂停的事实
        # 必须可持久化，否则刷新页面前端无从重建提问卡片。
        if questions:
            projection.pending_clarification = {
                "approval_id": pending.approval_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "questions": questions,
                "created_at_ms": pending.created_at_ms,
            }
        return pending

    def clear_pending_clarification(self, conversation_id: str) -> None:
        """清除会话的待回答澄清态（用户已作答 / 已作废 / run 结束）。"""
        projection = self.get_web_runtime_projection(conversation_id)
        if projection is not None and projection.pending_clarification is not None:
            projection.pending_clarification = None

    def set_approval_decision(
        self,
        conversation_id: str,
        approval_id: str,
        tool_call_id: str,
        approved: bool,
        answer: str | None = None,
    ) -> None:
        accepted = self._approval_runtime.submit_user_decision(
            approval_id=approval_id,
            approved=approved,
            answer=answer,
        )
        projection = self.get_web_runtime_projection(conversation_id)
        if projection is not None and accepted:
            projection.approval_pending_tool_call_ids.discard(tool_call_id)
            # 用户已作答：澄清态必须清除，否则落盘快照会让刷新后的前端
            # 重复弹出同一张提问卡片。
            projection.pending_clarification = None

    async def wait_for_clarification(
        self,
        approval_id: str,
    ) -> str | None:
        """无限等待用户对 AskUserQuestion 的作答——执行在此暂停，直到用户
        提交；仅用户提交 / abort / stale / 会话结束才会解除。

        返回值语义：
        - ``str``  — 用户作答文本（含「跳过」这类显式用户动作）
        - ``None`` — **非用户作答**（abort / stale / 会话结束）。调用方必须
          据此终止本轮，不得合成假答案让执行继续。
        """
        decision = await self._approval_runtime.wait_for_decision(
            approval_id=approval_id,
        )
        if decision.decided_by != "user" or not decision.approved:
            return None
        return decision.answer or ""

    def set_last_usage(self, conversation_id: str, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        self._last_usage[conversation_id] = {
            str(k): int(v) for k, v in usage.items() if isinstance(v, (int, float))
        }

    def get_last_usage(self, conversation_id: str) -> dict[str, int] | None:
        return self._last_usage.get(conversation_id)

    async def _consume(self, conversation_id: str) -> None:
        q = self._queues[conversation_id]
        try:
            while True:
                try:
                    msg, handler = await asyncio.wait_for(q.get(), timeout=300)
                except TimeoutError:
                    break
                # 取出即离队：先移除索引（此后不可再被提升/取消），再按标记分流。
                mid = msg.client_message_id
                if mid:
                    index = self._queue_index.get(conversation_id)
                    if index is not None:
                        index.pop(mid, None)
                if mid and mid in self._steered.get(conversation_id, set()):
                    self._steered[conversation_id].discard(mid)
                    self.push_steering(conversation_id, msg, handler)
                    q.task_done()
                    continue
                if mid and mid in self._cancelled.get(conversation_id, set()):
                    self._cancelled[conversation_id].discard(mid)
                    q.task_done()
                    continue
                self._busy[conversation_id] = True
                try:
                    self.reset_abort_event(conversation_id)
                    await handler(msg)
                except Exception:
                    logger.exception("error in chat consumer for conversation %s", conversation_id)
                finally:
                    self._busy[conversation_id] = False
                    q.task_done()
        finally:
            self._approval_runtime.mark_stale_for_conversation(conversation_id)
            self._queues.pop(conversation_id, None)
            self._consumers.pop(conversation_id, None)
            self._busy.pop(conversation_id, None)
            self._abort_events.pop(conversation_id, None)
            self._run_controls.pop(conversation_id, None)
            self._last_usage.pop(conversation_id, None)
            self._last_tier.pop(conversation_id, None)
            self._web_runtime.pop(conversation_id, None)
            # steering 兜底置于最后：先注销自身（_consumers 已 pop），drain
            # 转回排队时 _ensure_consumer 才能判定“无活 consumer”而重新拉起，
            # 让未消费消息降级为排队消息继续消费（G4）。
            self._clear_steering_state(conversation_id)

    def reset(self) -> None:
        if self._task_manager is not None:
            for tid in list(self._consumers.values()):
                handle = self._task_manager._tasks.get(tid)
                if handle is not None and not handle.asyncio_task.done():
                    handle.asyncio_task.cancel()
        for conversation_id in list(self._web_runtime.keys()):
            self._approval_runtime.mark_stale_for_conversation(conversation_id)
        self._queues.clear()
        self._consumers.clear()
        self._busy.clear()
        self._abort_events.clear()
        self._run_controls.clear()
        self._approval_runtime.clear()
        self._last_usage.clear()
        self._last_tier.clear()
        self._web_runtime.clear()
        self._steering.clear()
        self._queue_index.clear()
        self._steered.clear()
        self._cancelled.clear()

    async def shutdown(self, *, grace_s: float = 0.25) -> None:
        if self._closed:
            return
        self._closed = True
        for conversation_id in list(self._web_runtime.keys()):
            try:
                self._approval_runtime.mark_stale_for_conversation(conversation_id)
            except Exception:
                logger.debug("approval mark_stale_for_conversation failed", exc_info=True)
        for event in self._abort_events.values():
            try:
                event.set()
            except Exception:
                pass
        for rc in self._run_controls.values():
            try:
                rc.abort_event.set()
            except Exception:
                pass
        if self._task_manager is not None:
            for tid in list(self._consumers.values()):
                handle = self._task_manager._tasks.get(tid)
                if handle is not None and not handle.asyncio_task.done():
                    handle.asyncio_task.cancel()
        pending = [t for t in list(self._detached_tasks) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            try:
                await asyncio.wait(pending, timeout=max(0.0, grace_s), return_when=asyncio.ALL_COMPLETED)
            except Exception:
                pass
            for t in pending:
                if not t.done():
                    self._detached_tasks.discard(t)
                else:
                    try:
                        t.result()
                    except (asyncio.CancelledError, Exception):
                        pass
        try:
            await self._approval_runtime.shutdown(grace_s=max(0.0, grace_s))
        except Exception:
            logger.debug("approval_runtime shutdown failed", exc_info=True)
        self._queues.clear()
        self._consumers.clear()
        self._busy.clear()
        self._abort_events.clear()
        self._run_controls.clear()
        self._last_usage.clear()
        self._last_tier.clear()
        self._web_runtime.clear()
        self._steering.clear()
        self._queue_index.clear()
        self._steered.clear()
        self._cancelled.clear()

    def maybe_record_tier_change(
        self, conversation_id: str, new_tier: str
    ) -> str | None:
        previous = self._last_tier.get(conversation_id)
        self._last_tier[conversation_id] = new_tier
        if previous is not None and previous != new_tier:
            return previous
        return None


_session_queue = SessionQueue()


def _get_session_queue(state: ConnectionState) -> SessionQueue:
    from prismclaw.app.web.deps import WebDeps

    web_deps: WebDeps = state.ws.app.state.web_deps
    return cast(SessionQueue, web_deps.session_queue)