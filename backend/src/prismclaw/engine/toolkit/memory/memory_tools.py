"""Memory tool — persist durable memories and archive outdated ones."""
from __future__ import annotations

import time
import uuid
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.session.ids import derive_memory_session_key
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore
from prismclaw.schema.memory.memory_types import MemoryEntry
from prismclaw.schema.memory.classify import CANONICAL_TYPES, normalize_type

_MEMORY_ACTIONS = ("save", "archive")


class MemoryTool:
    """Single entry point for durable memory writes and archives.

    Merged from the former MemorizeTool (save) and ForgetTool (archive):
    same resource, forward and inverse verb on ``MemoryEntry``. The shared
    session guard (loaded tree + at least one non-error tool use) lives here
    once instead of being duplicated per verb.
    """

    name = "memory"
    description = (
        "为后续会话持久化长期记忆，或归档已过时的记忆。\n"
        "动作：\n"
        "- save：存储事实、偏好或工作流。layer='L2' 用于事实性知识"
        "（用户偏好、环境事实），'L3' 用于工作流 SOP。\n"
        "- archive：把某条已检索到的记忆标记为过时，使其不再浮现。"
        "需要提供 entry_id（即 memory_context 中以 [type|id] 形式展示的短 ID）以及归档原因。\n"
        "只有在当前会话中至少成功执行过一次工具之后，才能使用本工具。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_MEMORY_ACTIONS),
                "description": "save：持久化一条新记忆；archive：归档一条过时记忆。",
            },
            "content": {
                "type": "string",
                "description": "要记住的事实、偏好或工作流（save）。",
            },
            "layer": {
                "type": "string",
                "enum": ["L2", "L3"],
                "description": "L2 = 事实性知识，L3 = 工作流 SOP（save）。",
            },
            "type": {
                "type": "string",
                "description": "类别：user_preference、env_fact、workflow 等（save，默认为 general）。",
            },
            "entry_id": {
                "type": "string",
                "description": "要归档的记忆 ID：8 字符短 ID 或 memory_context 中可见的完整 UUID（archive）。",
            },
            "reason": {
                "type": "string",
                "description": "该记忆过时的原因：步骤陈旧、执行失败、环境变更（archive）。",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    side_effect = True
    tool_class = "memory-write-safe"
    effect_class = "local_mutation"
    visibility_scope = "main_only"

    def __init__(
        self,
        memory_store: MemoryStore,
        session_store: SessionStore,
        *,
        gateway: Any = None,
    ) -> None:
        self._memory_store = memory_store
        self._session_store = session_store
        # SDD B2: arbitration gateway — when present and mode != off, writes
        # route through submit() instead of direct store.
        self._gateway = gateway

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        action = args.get("action")
        if action not in _MEMORY_ACTIONS:
            allowed = ", ".join(_MEMORY_ACTIONS)
            return error_result(call_id, f"memory：无效的 action '{action}'（允许值：{allowed}）")

        # Shared session guard: both verbs require an active session with at
        # least one successful tool use (prevents memory writes from bare chat).
        tree, guard_error = await self._load_active_session(context)
        if guard_error is not None:
            return error_result(call_id, f"memory: {guard_error}")

        if action == "save":
            return await self._save(call_id, args, context)
        return await self._archive(call_id, args, context)

    # ── shared guard ───────────────────────────────────────────────────

    async def _load_active_session(self, context: ToolContext) -> tuple[Any, str | None]:
        """Load the session tree and enforce the non-error-tool-use guard.

        Returns ``(tree, None)`` on success or ``(None, reason)`` on failure.
        """
        try:
            tree = await self._session_store.load(context.session_id)
        except Exception as exc:
            return None, f"加载会话失败：{exc}"
        if tree is None:
            return None, "需要本会话中已有工具执行记录（会话不存在）"
        if not self._has_non_error_tool_use(tree):
            return None, "需要本会话中至少有一次成功的工具执行"
        return tree, None

    @staticmethod
    def _has_non_error_tool_use(tree) -> bool:
        """是否存在至少一次「成功」的工具执行。

        SDD D8：任何 role=="tool" 条目（含 error 结果）都算成功的旧语义
        让报错工具调用也能触发记忆写入；现在依据 MessageEntry.is_error
        （工具执行循环在 ToolResult.is_error 时置位）排除失败执行。
        """
        for entry in tree.entries.values():
            if (
                isinstance(entry, MessageEntry)
                and entry.role == "tool"
                and not entry.is_error
            ):
                return True
        return False

    # ── save ───────────────────────────────────────────────────────────

    async def _save(self, call_id: str, args: dict[str, Any], context: ToolContext) -> ToolResult:
        content = args.get("content")
        layer = args.get("layer")
        # SDD D7: type 走受控词表（归一 + 未知词拒绝），默认 fact。
        entry_type = normalize_type(args.get("type", "fact"))
        if entry_type not in CANONICAL_TYPES:
            allowed = ", ".join(sorted(CANONICAL_TYPES))
            return error_result(
                call_id, f"memory：'type' 必须是受控词表之一（{allowed}），实际为 {args.get('type')!r}"
            )

        if not isinstance(content, str) or not content.strip():
            return error_result(call_id, "memory：'content' 必须是非空字符串")
        if layer not in ("L2", "L3"):
            return error_result(call_id, f"memory：'layer' 必须是 'L2' 或 'L3'，实际为 {layer!r}")

        session_key = derive_memory_session_key(context.session_id)
        now = time.time()
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            layer=layer,
            type=entry_type,
            content=content,
            source_session_id=context.session_id,
            created_at=now,
            updated_at=now,
            # SDD D7：写侧质量控制——confidence 与来源标记确定性补齐。
            # save 是模型在会话内沉淀的知识，无工具结果/用户明示可区分信号，
            # 统一中等置信度；更细的来源推断留待归因体系（D14 决策）处理。
            confidence=0.7,
            origin_stage="memorize_tool",
        )

        try:
            if self._gateway is not None:
                from prismclaw.engine.memory.gateway import GatewayLockError

                try:
                    result = await self._gateway.submit(
                        session_key, entry, source="memorize"
                    )
                except GatewayLockError as exc:
                    return error_result(
                        call_id, f"memory：仲裁锁繁忙，请重试：{exc}"
                    )
                summary = (
                    f"已记忆（{layer}/{entry_type}，id={entry.id[:8]}）：{content[:80]}"
                    + ("..." if len(content) > 80 else "")
                )
                if result.notes:
                    summary += f" [{','.join(result.notes)}]"
                # NOOP reporting only reflects reality in active mode: in
                # shadow the legacy store ALWAYS ran, so "skipped" would lie
                # to the model about what actually happened.
                if result.decision == "NOOP" and result.mode == "active":
                    summary = (
                        f"已跳过记忆（与既有记忆重复）：{content[:80]}"
                    )
                return text_result(call_id, summary)
            await self._memory_store.store(session_key, entry)
        except Exception as exc:
            return error_result(call_id, f"memory：写入失败：{exc}")

        return text_result(
            call_id,
            f"已记忆（{layer}/{entry_type}，id={entry.id[:8]}）：{content[:80]}"
            + ("..." if len(content) > 80 else ""),
        )

    # ── archive ────────────────────────────────────────────────────────

    async def _archive(self, call_id: str, args: dict[str, Any], context: ToolContext) -> ToolResult:
        entry_id = args.get("entry_id", "").strip()
        reason = args.get("reason", "").strip()

        if not entry_id:
            return error_result(call_id, "memory：archive 操作需要 'entry_id'")
        if not reason:
            return error_result(call_id, "memory：archive 操作需要 'reason'（说明该记忆为何过时）")

        session_key = derive_memory_session_key(context.session_id)
        prefix_match = len(entry_id) < 32
        full_id, content_preview = await self._memory_store.get_entry_by_id(
            session_key, entry_id, prefix_match=prefix_match
        )
        if full_id is None:
            return error_result(call_id, content_preview or "memory：条目查询失败")

        archived = await self._memory_store.archive_entry(session_key, full_id, reason=reason)
        if not archived:
            return error_result(call_id, f"memory：条目 '{entry_id}' 已归档或不存在")

        preview = content_preview[:50] + "..." if len(content_preview) > 50 else content_preview
        return text_result(call_id, f"已归档：{preview}\n原因：{reason}")