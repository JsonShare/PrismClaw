"""Agent-facing tool for managing scheduled (automation) tasks via dialogue.

This is the dialogue-creation entry point for the scheduler system. It is the
native-tool replacement for the deprecated ``.work/skills/cron`` skill
(which targeted the unrelated AionUI helper binary).

Design:
- Single tool, multiple actions (create/list/update/delete/trigger/enable/disable).
- Services are injected per-request via typed fields on ``ToolContext`` (see web chat
  handler). The tool holds no constructor state, so registration is decoupled
  from injection.
- Schedule is expressed as a structured spec (converted via ``spec_to_cron``);
  a raw ``cron_expr`` is accepted only as an escape hatch for compound schedules.
- Store operations are wrapped so failures surface as friendly ``error_result``
  instead of propagating tracebacks to the runner.
- Scheduler services are injected by the web chat path. Scheduled runs now
  share that path, so this tool is available there as well.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from croniter import croniter

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.scheduler import (
    INVALIDATE_CHANNEL,
    ScheduledTask,
    ScheduledTaskRun,
    SchedulerDispatchJob,
    make_run_id,
)
from prismclaw.schema.shared.cron_convert import cron_to_spec, spec_to_cron

logger = logging.getLogger(__name__)

_WEEKDAY_CN = {
    "mon": "一",
    "tue": "二",
    "wed": "三",
    "thu": "四",
    "fri": "五",
    "sat": "六",
    "sun": "日",
}

_INSTRUCTION_PREVIEW_CHARS = 80


class ScheduledTaskTool:
    name = "scheduled_task"
    description = (
        "管理自动化定时任务：创建、查询、修改、删除、手动触发、启用、禁用。\n"
        "\n"
        "关键规则：\n"
        "- instruction 是任务触发时 AI 收到的唯一输入，没有对话历史，必须完全自包含。\n"
        "- 用户指令模糊时（如\"整理新闻\"），先帮助细化为具体可执行步骤，再调用本工具。\n"
        "- 用户未指定执行时间时，先询问偏好再创建。\n"
        "- 修改/删除/触发前先用 list 确认目标 task_id。\n"
        "\n"
        "instruction 写法：\n"
        "  反例：\"按之前说的格式发报告\"（依赖对话上下文，执行时无法理解）\n"
        "  正例：\"搜索过去24小时AI新闻，按领域分类，标注重要性，以表格输出\"\n"
        "\n"
        "schedule 字段按 type 取值：\n"
        "  minute → everyMinutes(1-59)；hour → minute；day → hour,minute；\n"
        "  week → weekday(mon-sun),hour,minute；month → day(1-31),hour,minute。\n"
        "  仅当结构化字段无法表达（如\"工作日9点和15点\"）时才用 cron_expr。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "update", "delete", "trigger", "enable", "disable"],
                "description": "操作类型",
            },
            "task_id": {
                "type": "string",
                "description": "目标任务 ID（非 create 时必填）",
            },
            "name": {
                "type": "string",
                "description": "任务名称，简短描述性，最长 50 字",
            },
            "instruction": {
                "type": "string",
                "description": "任务触发时发送给 AI 的完整自包含指令",
            },
            "schedule": {
                "type": "object",
                "description": "结构化调度定义（优先使用）",
                "properties": {
                    "type": {"type": "string", "enum": ["minute", "hour", "day", "week", "month"]},
                    "everyMinutes": {"type": "integer", "description": "type=minute 时的间隔分钟数(1-59)"},
                    "minute": {"type": "integer", "description": "分钟 0-59"},
                    "hour": {"type": "integer", "description": "小时 0-23"},
                    "weekday": {"type": "string", "description": "type=week 时取值 mon-sun"},
                    "day": {"type": "integer", "description": "type=month 时的日期 1-31"},
                },
                "required": ["type"],
            },
            "cron_expr": {
                "type": "string",
                "description": "5 段 cron 表达式，仅当 schedule 无法表达时使用",
            },
            "enabled": {
                "type": "boolean",
                "description": "仅 create 时有效：创建后是否立即启用，默认 true。已有任务的启停请用 enable/disable action",
            },
        },
        "required": ["action"],
    }
    side_effect = True
    tool_class = "write"
    effect_class = "local_mutation"
    approval_eligible = False
    is_concurrency_safe = True

    # ── Entry point ────────────────────────────────────────────────────

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        action = args.get("action")
        handler = self._ACTION_HANDLERS.get(action)
        if handler is None:
            return error_result(
                call_id,
                f"scheduled_task：未知 action '{action}'。"
                "有效值：create、list、update、delete、trigger、enable、disable。",
            )
        # Scheduler services come from the web chat context; their absence
        # means the scheduler is disabled or the tool runs outside that
        # context — refuse cleanly.
        if self._store(context) is None:
            return error_result(call_id, "scheduled_task：调度服务不可用")
        try:
            return await handler(self, args, context, call_id)
        except Exception as exc:  # noqa: BLE001 — never leak tracebacks to the runner
            logger.exception("scheduled_task.unhandled", extra={"action": action})
            return error_result(call_id, f"scheduled_task：操作失败 — {type(exc).__name__}: {exc}")

    # ── Service resolution ─────────────────────────────────────────────

    def _store(self, context: ToolContext):
        return context.scheduler_store

    def _engine(self, context: ToolContext):
        return context.scheduler_engine

    def _dispatch(self, context: ToolContext):
        return context.scheduler_dispatch

    def _redis(self, context: ToolContext):
        return context.scheduler_redis_client

    def _max_tasks(self, context: ToolContext) -> int:
        value = context.scheduler_max_tasks_per_user
        return int(value) if isinstance(value, int) and value > 0 else 20

    async def _broadcast(self, context: ToolContext, event_type: str, data: dict) -> None:
        broadcast = context.scheduler_broadcast
        if broadcast is None or context.user_id is None:
            return
        try:
            await broadcast(context.user_id, event_type, data)
        except Exception:  # noqa: BLE001 — notification is best-effort
            logger.warning("scheduled_task.broadcast_failed", extra={"event": event_type}, exc_info=True)

    async def _invalidate(self, context: ToolContext, task_id: str) -> None:
        """Local cache invalidation + cross-worker pub/sub (cloud)."""
        engine = self._engine(context)
        if engine is not None:
            try:
                engine.invalidate_cache(task_id)
            except Exception:  # noqa: BLE001
                logger.debug("scheduled_task.invalidate_failed", exc_info=True)
        redis = self._redis(context)
        if redis is not None:
            try:
                await redis.publish(INVALIDATE_CHANNEL, task_id)
            except Exception:  # noqa: BLE001
                logger.debug("scheduled_task.pubsub_failed", exc_info=True)

    async def _load_owned_task(self, context: ToolContext, task_id: str) -> tuple[ScheduledTask | None, str | None]:
        """Return (task, error). error is set when the task is missing or not owned."""
        if not task_id:
            return None, "scheduled_task：该操作需要 'task_id'"
        store = self._store(context)
        task = await store.get_task(task_id)
        if task is None or task.user_id != context.user_id:
            return None, f"scheduled_task：未找到任务：{task_id}"
        return task, None

    # ── Actions ────────────────────────────────────────────────────────

    async def _action_create(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        if context.user_id is None:
            return error_result(call_id, "scheduled_task：无法确定当前用户")
        store = self._store(context)

        name = _clean_str(args.get("name"))
        if not name:
            return error_result(call_id, "scheduled_task：缺少 'name' 参数")
        instruction = _clean_str(args.get("instruction"))
        if not instruction:
            return error_result(call_id, "scheduled_task：缺少 'instruction' 参数")

        limit = self._max_tasks(context)
        if await store.count_user_tasks(context.user_id) >= limit:
            return error_result(call_id, f"scheduled_task：已达到每用户任务上限（{limit} 个），请先删除不用的任务")

        cron_expr, schedule_spec, error = _resolve_schedule(args)
        if error:
            return error_result(call_id, f"scheduled_task：{error}")

        enabled = bool(args.get("enabled", True))
        task = ScheduledTask(
            user_id=context.user_id,
            workspace_id=context.workspace_id or "default",
            name=name,
            instruction=instruction,
            cron_expr=cron_expr,
            schedule_spec=schedule_spec,
            enabled=enabled,
            conversation_id=context.session_id,
            created_at=time.time(),
        )
        try:
            await store.put_task(task)
        except Exception as exc:  # noqa: BLE001
            return error_result(call_id, f"scheduled_task：任务保存失败 — {type(exc).__name__}")

        await self._invalidate(context, task.id)
        await self._broadcast(context, "scheduled_task.created", {
            "task_id": task.id,
            "name": task.name,
            "enabled": task.enabled,
            "schedule": task.schedule_spec,
        })

        return text_result(call_id, (
            f"已创建自动化任务「{task.name}」（id: {task.id}）。\n"
            f"调度：{_describe_schedule(task.cron_expr, task.schedule_spec)}\n"
            f"下次执行：{_next_fire_text(task.cron_expr)}\n"
            f"状态：{'已启用' if task.enabled else '已停用'}"
        ))

    async def _action_list(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        if context.user_id is None:
            return error_result(call_id, "scheduled_task：无法确定当前用户")
        store = self._store(context)
        tasks = await store.list_tasks(
            user_id=context.user_id,
            workspace_id=context.workspace_id or None,
        )
        if not tasks:
            return text_result(call_id, "当前没有自动化任务。")

        items = [
            {
                "id": t.id,
                "name": t.name,
                "schedule": _describe_schedule(t.cron_expr, t.schedule_spec),
                "enabled": t.enabled,
                "last_status": t.last_status,
                "instruction_preview": _preview(t.instruction),
            }
            for t in tasks
        ]
        return text_result(call_id, json.dumps(items, ensure_ascii=False))

    async def _action_update(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        store = self._store(context)
        task, error = await self._load_owned_task(context, _clean_str(args.get("task_id")))
        if error:
            return error_result(call_id, error)

        changed = False
        name = _clean_str(args.get("name"))
        if name:
            task.name = name
            changed = True
        instruction = _clean_str(args.get("instruction"))
        if instruction:
            task.instruction = instruction
            changed = True
        if args.get("schedule") or args.get("cron_expr"):
            cron_expr, schedule_spec, sched_error = _resolve_schedule(args)
            if sched_error:
                return error_result(call_id, f"scheduled_task：{sched_error}")
            task.cron_expr = cron_expr
            task.schedule_spec = schedule_spec
            changed = True
        if not changed:
            return error_result(call_id, "scheduled_task：没有可更新的字段（name/instruction/schedule）")

        try:
            await store.put_task(task)
        except Exception as exc:  # noqa: BLE001
            return error_result(call_id, f"scheduled_task：任务保存失败 — {type(exc).__name__}")

        await self._invalidate(context, task.id)
        await self._broadcast(context, "scheduled_task.updated", {
            "task_id": task.id,
            "name": task.name,
            "enabled": task.enabled,
            "schedule": task.schedule_spec,
        })
        return text_result(call_id, (
            f"已更新任务「{task.name}」。\n"
            f"调度：{_describe_schedule(task.cron_expr, task.schedule_spec)}\n"
            f"下次执行：{_next_fire_text(task.cron_expr)}"
        ))

    async def _action_delete(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        store = self._store(context)
        task, error = await self._load_owned_task(context, _clean_str(args.get("task_id")))
        if error:
            return error_result(call_id, error)
        try:
            await store.delete_task(task.id)
        except Exception as exc:  # noqa: BLE001
            return error_result(call_id, f"scheduled_task：删除失败 — {type(exc).__name__}")
        await self._invalidate(context, task.id)
        await self._broadcast(context, "scheduled_task.deleted", {"task_id": task.id})
        return text_result(call_id, f"已删除任务「{task.name}」。")

    async def _action_trigger(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        dispatch = self._dispatch(context)
        if dispatch is None:
            return error_result(call_id, "scheduled_task：派发队列不可用")
        store = self._store(context)
        task, error = await self._load_owned_task(context, _clean_str(args.get("task_id")))
        if error:
            return error_result(call_id, error)

        fire_slot = now = time.time()
        run = ScheduledTaskRun(
            id=make_run_id(task.id, fire_slot),
            task_id=task.id,
            fired_at=now,
            status="pending",
        )
        try:
            won = await store.add_run(run, fired_task_id=task.id, fired_at=now)
        except Exception as exc:  # noqa: BLE001
            return error_result(call_id, f"scheduled_task：触发失败 — {type(exc).__name__}")

        if not won:
            return text_result(call_id, "该触发已存在，请勿重复触发。")

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
            await dispatch.enqueue(job)
        except Exception as exc:
            await store.update_run(
                run.id, status="failed", error="dispatch_enqueue_failed"
            )
            return error_result(
                call_id, f"scheduled_task：入队失败 — {type(exc).__name__}"
            )
        return text_result(call_id, f"已触发任务「{task.name}」，正在后台执行（run_id: {run.id}）。")

    async def _action_set_enabled(self, args: dict, context: ToolContext, call_id: str, enabled: bool) -> ToolResult:
        store = self._store(context)
        task, error = await self._load_owned_task(context, _clean_str(args.get("task_id")))
        if error:
            return error_result(call_id, error)
        if task.enabled == enabled:
            return text_result(call_id, f"任务「{task.name}」当前已是{'启用' if enabled else '停用'}状态。")
        task.enabled = enabled
        if enabled:
            task.consecutive_failures = 0
            task.last_status = "idle"
        try:
            await store.put_task(task)
        except Exception as exc:  # noqa: BLE001
            return error_result(call_id, f"scheduled_task：保存失败 — {type(exc).__name__}")
        await self._invalidate(context, task.id)
        await self._broadcast(context, "scheduled_task.updated", {
            "task_id": task.id,
            "name": task.name,
            "enabled": task.enabled,
            "schedule": task.schedule_spec,
        })
        return text_result(call_id, f"已{'启用' if enabled else '停用'}任务「{task.name}」。")

    async def _action_enable(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        return await self._action_set_enabled(args, context, call_id, True)

    async def _action_disable(self, args: dict, context: ToolContext, call_id: str) -> ToolResult:
        return await self._action_set_enabled(args, context, call_id, False)

    _ACTION_HANDLERS = {
        "create": _action_create,
        "list": _action_list,
        "update": _action_update,
        "delete": _action_delete,
        "trigger": _action_trigger,
        "enable": _action_enable,
        "disable": _action_disable,
    }


# ── Module helpers ─────────────────────────────────────────────────────


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _preview(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _INSTRUCTION_PREVIEW_CHARS:
        return text
    return text[:_INSTRUCTION_PREVIEW_CHARS] + "..."


def _resolve_schedule(args: dict) -> tuple[str, dict, str | None]:
    """Resolve (cron_expr, schedule_spec, error). Structured spec takes priority."""
    spec = args.get("schedule")
    if isinstance(spec, dict) and spec.get("type"):
        try:
            return spec_to_cron(spec), spec, None
        except (ValueError, KeyError, TypeError) as exc:
            return "", {}, f"schedule 无效：{exc}"

    cron_expr = _clean_str(args.get("cron_expr"))
    if cron_expr:
        if not croniter.is_valid(cron_expr):
            return "", {}, f"invalid cron expression: {cron_expr}"
        return cron_expr, cron_to_spec(cron_expr), None

    return "", {}, "either 'schedule' or 'cron_expr' is required"


def _describe_schedule(cron_expr: str, spec: dict | None) -> str:
    spec = spec or cron_to_spec(cron_expr)
    t = spec.get("type")
    try:
        if t == "minute":
            return f"每 {spec.get('everyMinutes', '?')} 分钟"
        if t == "hour":
            return f"每小时第 {int(str(spec['minute'])):02d} 分"
        if t == "day":
            return f"每天 {int(str(spec['hour'])):02d}:{int(str(spec['minute'])):02d}"
        if t == "week":
            day = _WEEKDAY_CN.get(str(spec.get("weekday", "")).lower(), "?")
            return f"每周{day} {int(str(spec['hour'])):02d}:{int(str(spec['minute'])):02d}"
        if t == "month":
            return f"每月 {spec.get('day', '?')} 日 {int(str(spec['hour'])):02d}:{int(str(spec['minute'])):02d}"
    except (KeyError, ValueError, TypeError):
        pass
    return cron_expr or "自定义调度"


def _next_fire_text(cron_expr: str) -> str:
    try:
        nxt = croniter(cron_expr, datetime.now()).get_next(datetime)
    except (ValueError, KeyError):
        return "（无法计算）"
    return nxt.strftime("%Y-%m-%d %H:%M")
