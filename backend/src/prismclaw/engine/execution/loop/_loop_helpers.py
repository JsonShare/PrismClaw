"""agent_loop 辅助函数 — 不依赖类实例(self)的纯函数和辅助函数。"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# run_loop 使用的内部辅助函数 (无 deps 子系统知识)
# ---------------------------------------------------------------------------

def compute_tool_signature(tool_name: str, args: dict | None) -> str:
    """Compute a doom-loop signature from tool name + argument values.

    The signature includes a short hash of the argument values (not just keys)
    so that calling the same tool with different arguments is NOT flagged as
    a doom-loop.  Only truly identical calls (same tool + same args) repeat.
    """
    import hashlib
    import json as _json

    if not tool_name:
        return ""
    args = args or {}
    try:
        payload = _json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        payload = repr(sorted(args.items()))
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:8]
    return f"{tool_name}:{digest}"


def compute_result_fingerprint(result: Any) -> str:
    """结果指纹：内容 digest + 错误位。同签名不同结果 → 不同指纹。

    None（无结果，如被拦截/未执行）返回空串，record 侧退化为纯签名。
    """
    import hashlib

    if result is None:
        return ""
    from prismclaw.schema.agent.data_types import TextBlock

    content = getattr(result, "content", None) or []
    text = "".join(
        block.text for block in content if isinstance(block, TextBlock)
    )
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"{digest}{'e' if getattr(result, 'is_error', False) else 'n'}"


def _parse_tool_calls(tool_calls: list[Any]) -> list[Any]:
    """Parse raw tool calls into ParsedToolCall list."""
    from prismclaw.engine.execution.dispatch.call_handling import parse_tool_calls
    return parse_tool_calls(tool_calls)


def _tool_call_start_events(parsed_calls: list[Any]) -> list[Any]:
    """Emit ToolCallStart events for each parsed tool call."""
    from prismclaw.engine.execution.dispatch.call_handling import tool_call_start_events
    return tool_call_start_events(parsed_calls)


def _create_blocked_tool_result(call: Any, reason: str) -> Any:
    """Create a tool result for a blocked tool call.

    返回 ToolCallEnd 而非 ErrorEvent，前端可区分"工具被阻止"和"运行终止"。
    """
    from prismclaw.schema.agent.data_types import TextBlock
    from prismclaw.schema.content.content_types import ToolResult
    from prismclaw.schema.content.streaming import ToolCallEnd

    call_id = getattr(call, "call_id", None) or (
        call.call.get("id", "") if isinstance(getattr(call, "call", None), dict) else ""
    )
    return ToolCallEnd(
        tool_call_id=call_id,
        result=ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=f"[Blocked] {reason}")],
            is_error=True,
        ),
    )


def _build_all_blocked_guidance(blocked_results: Any) -> str:
    """把「全拒」结果翻译成一次可执行的重规划指令。

    模型在 all_blocked 回合只看到自己被拒（落树的 tool 结果），但不知道
    下一步该换什么。RESTART 若不携带原因与动作约束，下一次生成就是完全相同
    的调用（空转直到熔断）。这里聚合拒绝原因并给出两条明确出路。
    """
    reasons: list[str] = []
    for event in blocked_results or ():
        result = getattr(event, "result", None)
        content = getattr(result, "content", None) or ()
        text = "".join(getattr(block, "text", "") for block in content).strip()
        if text:
            # 去掉统一的 "[Blocked] " 前缀，避免提示词里重复噪音。
            reasons.append(text.removeprefix("[Blocked] ").strip())
    # 去重且保序，截断防止提示词被超长拒绝理由淹没。
    unique = list(dict.fromkeys(reason for reason in reasons if reason))
    detail = "；".join(unique)[:500]
    suffix = f"（原因：{detail}）" if detail else ""
    return (
        "[系统提示：上一回合的全部工具调用都被拒绝"
        f"{suffix}。重复同一批调用不会改变结果：请改用未被策略禁用的工具，"
        "或直接给出已有结论并说明受阻原因。]"
    )


def _create_aborted_tool_result(call_id: str) -> Any:
    """Create a synthetic ToolCallEnd for an abort scenario.

    用于维持 ToolCallStart ↔ ToolCallEnd 配对：abort 时已 yield ToolCallStart，
    未启动的 call 必须有对应的 ToolCallEnd 才能让 replay 闭合。
    """
    from prismclaw.schema.agent.data_types import TextBlock
    from prismclaw.schema.content.content_types import ToolResult
    from prismclaw.schema.content.streaming import ToolCallEnd

    return ToolCallEnd(
        tool_call_id=call_id,
        result=ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text="运行已中止——该工具未被派发执行")],
            is_error=True,
        ),
    )


def _create_clarify_tool_end(call_id: str, answer: str) -> Any:
    """Create a synthetic ToolCallEnd closing the AskUserQuestion HITL call.

    HITL 不走 execute_tools：ToolCallStart 已在 3a 统一发出，但 ask_user_question
    被拦截等待用户回答——若不补发 End，前端时间线的「向用户提问」行永远停在
    running。合成 End（is_error=False，结果文本=用户答案）让该行落定，答案
    随 timeline 持久化，历史回放同样可见。
    """
    from prismclaw.schema.agent.data_types import TextBlock
    from prismclaw.schema.content.content_types import ToolResult
    from prismclaw.schema.content.streaming import ToolCallEnd

    return ToolCallEnd(
        tool_call_id=call_id,
        result=ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=answer)],
            is_error=False,
        ),
    )


async def _persist_clarified_turn(
    *,
    deps: Any,
    tree: Any,
    response: Any,
    allowed_calls: list[Any],
    ask_call_id: str,
    answer: str | None,
) -> None:
    """HITL 回合（AskUserQuestion）的树持久化。

    与 execute_tools Step 0 / persist_denied_turn 同形态：assistant（含
    全部 tool_calls）+ 每个 tool_call 的合成 tool 结果。HITL 分支不经
    execute_tools（Step 0 从未执行），此前直接 continue 导致：
      1. assistant 不落树 → answer 成孤儿 user 消息（模型下轮看不到
         自己问过什么，上下文断裂）；
      2. 同批次其余调用凭空消失（无执行、无结果）；
      3. 树中若有孤立 tool_calls，prepare_next_turn 重建上下文时被
         provider 拒绝（400）。

    结果合成规则（对齐 deepseek exclusive-barrier 语义——同批次仅 ask
    生效，其余以可见的跳过说明收尾，模型可按需重发）：
      - ask_call：answer 非空 = 用户答案（is_error=False）；
        None = 中止说明（is_error=True）
      - 同批次其余调用：跳过说明（is_error=True，模型可见、可重发）

    落树顺序（唯一正确序，wait_for_clarification 不再落树的原因）：
    assistant(tool_calls) → tool 结果（ask 答案 + 其余跳过）→ user answer。
    user 消息若先落会插在 assistant 提问之前、且隔断 tool_calls ↔ tool
    结果配对（provider 400）。
    """
    from prismclaw.engine.execution.dispatch.response_handling import (
        append_assistant_response,
    )
    from prismclaw.engine.execution.dispatch.turn_utils import strip_thinking
    from prismclaw.engine.execution.output.tool_result_rendering import (
        tool_result_to_llm_content,
    )
    from prismclaw.engine.execution.loop.tree_writer import _append
    from prismclaw.schema.agent.session import MessageEntry
    from prismclaw.schema.agent.data_types import TextBlock
    from prismclaw.schema.content.content_types import ToolResult
    from prismclaw.schema.session.types import generate_entry_id, now_iso

    await append_assistant_response(
        deps=deps,
        tree=tree,
        append_entry=_append,
        text=strip_thinking(response.text or ""),
        tool_calls=response.tool_calls,
    )
    _ask_text = (
        answer
        if answer is not None
        else "（等待用户回答期间执行已中止，未收到作答）"
    )
    _skip_text = (
        "（该调用在等待用户回答提问期间未执行，已跳过；"
        "如仍需要请根据用户回答重新发起）"
    )
    entry_ids = set(tree.entries.keys())
    for call in allowed_calls:
        cid = getattr(call, "call_id", None) or (
            call.call.get("id", "")
            if isinstance(getattr(call, "call", None), dict)
            else ""
        )
        if not cid:
            continue
        _is_ask = cid == ask_call_id
        _is_error = not (_is_ask and answer is not None)
        result = ToolResult(
            tool_call_id=cid,
            content=[TextBlock(text=_ask_text if _is_ask else _skip_text)],
            is_error=_is_error,
        )
        entry = MessageEntry(
            id=generate_entry_id(entry_ids),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            role="tool",
            content=tool_result_to_llm_content(result),
            tool_call_id=cid,
            is_error=_is_error,
            tool_name=getattr(call, "name", None),
        )
        entry_ids.add(entry.id)
        await _append(deps, tree, entry)
    # user answer 最后落树：模型下轮从 tree 重建时看到完整对话
    # （提问 → 工具结果 → 用户回答），answer 跨轮持久。
    if answer is not None:
        await _append(
            deps,
            tree,
            MessageEntry(
                id=generate_entry_id(entry_ids),
                parent_id=tree.leaf_id,
                timestamp=now_iso(),
                role="user",
                content=answer,
                # 标记为人机交互的澄清追问答案：落树保留为独立 user entry（模型
                # 上下文需要，下一轮 prepare_next_turn 才能看到用户作答），但
                # 显示投影 (_build_display_messages) 据此抑制为独立气泡——答案
                # 已作为「人机交互」步骤内嵌在提问气泡的执行时间线里，无需再拆
                # 成第二条 user 记录造成割裂。
                metadata={"kind": "clarification_answer", "ask_tool_call_id": ask_call_id},
            ),
        )


async def _persist_blocked_tool_results(
    *,
    deps: Any,
    tree: Any,
    blocked_events: list[Any],
) -> None:
    """Persist blocked tool-call results as ``role="tool"`` entries.

    execute_tools 持久化 assistant 消息（含 tool_calls）后，被拒调用的
    tool 结果必须同步落树——tool_calls ↔ tool 消息配对是 provider 的
    硬约束（否则下一轮请求 400）。entry 形态与
    project_tool_result_entries 的 tool 条目一致（tool_call_id / is_error /
    tool_name），content 走 tool_result_to_llm_content 保证模型可见
    "[Blocked] …" 提示。
    """
    from prismclaw.engine.execution.output.tool_result_rendering import (
        tool_result_to_llm_content,
    )
    from prismclaw.schema.agent.session import MessageEntry
    from prismclaw.schema.session.types import generate_entry_id, now_iso
    from prismclaw.engine.execution.loop.tree_writer import _append

    entry_ids = set(tree.entries.keys())
    for event in blocked_events:
        result = getattr(event, "result", None)
        if result is None:
            continue
        tool_call_id = getattr(result, "tool_call_id", "") or ""
        if not tool_call_id:
            continue
        entry = MessageEntry(
            id=generate_entry_id(entry_ids),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            role="tool",
            content=tool_result_to_llm_content(result),
            tool_call_id=tool_call_id,
            is_error=getattr(result, "is_error", True),
            tool_name=None,
        )
        entry_ids.add(entry.id)
        await _append(deps, tree, entry)


async def _close_orphaned_tool_calls(
    *,
    deps: Any,
    tree: Any,
    tool_calls: list[Any],
) -> None:
    """execute_tools 异常路径的配对闭合。

    assistant 消息（含 tool_calls）已落树，但执行中断——尚未执行的
    调用合成 ``role="tool"`` error 条目落树，避免下轮从 tree 重建
    上下文时孤立 tool_calls 触发 API 400。
    与 runner.py abort 收尾的 _create_aborted_tool_result 语义一致
    （那边只补流事件，这边还要补树条目）。

    已覆盖判定以树内条目为准（而非 last_tool_results）：异常可能
    发生在 persist_projected_tool_result_entries 中途，部分条目已
    落树而内存列表尚未回写——按内存判定会写出重复 tool_call_id。
    """
    _covered_ids: set[str] = set()
    for entry in tree.get_branch():
        _tcid = getattr(entry, "tool_call_id", None)
        if _tcid:
            _covered_ids.add(_tcid)
    from prismclaw.schema.agent.session import MessageEntry
    from prismclaw.schema.session.types import generate_entry_id, now_iso
    from prismclaw.engine.execution.loop.tree_writer import _append

    _synth_content = "[错误] 工具在执行前即失败"
    entry_ids = set(tree.entries.keys())
    for call in tool_calls:
        cid = call.get("id", "") if isinstance(call, dict) else ""
        if not cid or cid in _covered_ids:
            continue
        entry = MessageEntry(
            id=generate_entry_id(entry_ids),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            role="tool",
            content=_synth_content,
            tool_call_id=cid,
            is_error=True,
            tool_name=(call.get("function") or {}).get("name"),
        )
        entry_ids.add(entry.id)
        await _append(deps, tree, entry)


# ---------------------------------------------------------------------------
# stage 模块（terminal / tools）使用的辅助函数
# ---------------------------------------------------------------------------

def _build_clarify_request(ask_call: Any, tool_ctx: Any, deps: Any, request: Any) -> Any:
    """构建 AskUserQuestion 的 ToolClarifyRequest 事件。"""
    from prismclaw.schema.content.streaming import ToolClarifyRequest
    from prismclaw.engine.execution.query.clarify_normalize import normalize_clarify_questions as _normalize
    from prismclaw.engine.execution.dispatch.call_handling import _resolve_approval_runtime_ids

    _ac_args = ask_call.arguments or {}
    _ac_tool_call_id = (
        ask_call.call.get("id", "")
        if isinstance(getattr(ask_call, "call", None), dict)
        else ""
    )
    _ac_run_id, _ac_turn_id = _resolve_approval_runtime_ids(tool_ctx)
    _ac_questions = _normalize(
        _ac_args.get("questions") if isinstance(_ac_args, dict) else None,
    )
    _ac_pending = deps.session_queue.create_pending(
        conversation_id=request.session_id,
        tool_call_id=_ac_tool_call_id,
        tool_name="ask_user_question",
        run_id=_ac_run_id,
        turn_id=_ac_turn_id,
        questions=_ac_questions,
    )
    return ToolClarifyRequest(
        approval_id=_ac_pending.approval_id,
        run_id=_ac_run_id,
        turn_id=_ac_turn_id,
        tool_call_id=_ac_tool_call_id,
        tool_name="ask_user_question",
        questions=_ac_questions,
    )
