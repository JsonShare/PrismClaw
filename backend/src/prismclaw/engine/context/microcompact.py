from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.staging.tool_result_persistence import (
    is_persisted_content,
    PERSISTED_OUTPUT_TAG,
)
from prismclaw.engine.toolkit.registry.tool_classification import is_network_read_tool

logger = logging.getLogger(__name__)

# Retired builtin web tools — recognized so historical sessions compact the
# same way. Current web search lives behind an MCP service whose name is
# deployment config, matched dynamically via is_network_read_tool().
_LEGACY_WEB_TOOLS = ("web_search", "web_fetch", "web_extract")

COMPACTABLE_TOOLS = {
    # File tools
    "read", "read_image", "read_office", "ls", "grep", "glob",
    # Legacy web tools (historical sessions; current MCP web tools match dynamically)
    *_LEGACY_WEB_TOOLS, "fetch_mcp_resource",
    # Shell tools
    "bash", "bash_read", "python",
    # Sub-agent
    "reduce_subagent_results",
    # Discovery
    "tool_search", "skill_search", "activate_skill",
}


def is_compactable_tool(tool_name: str) -> bool:
    """Compactability: static builtin sets + any configured web-retrieval MCP tool."""
    return tool_name in COMPACTABLE_TOOLS or is_network_read_tool(tool_name)


_CLEARED_TAG_PREFIX = "[已清除的旧工具结果："

# ── Tiered retention ──────────────────────────────────────────────────
# Different tool results have different useful lifetimes.  Ephemeral
# results (web searches) are useful for one turn; persistent results
# (file reads) may be referenced across many turns.
TOOL_KEEP_RECENT: dict[str, int] = {
    # Tier 1: ephemeral — consumed in the current reasoning step
    **{name: 1 for name in _LEGACY_WEB_TOOLS},
    "fetch_mcp_resource": 1,
    "tool_search": 1, "skill_search": 1, "activate_skill": 1,
    # Tier 2: short-term — may be referenced 1-3 turns later
    "bash": 3, "bash_read": 3, "python": 3, "ls": 4, "grep": 4, "glob": 4,
    "reduce_subagent_results": 3,
    # Tier 3: persistent — multi-turn reference (code, docs, reports, images)
    "read": 8, "read_image": 6, "read_office": 6,
}
_DEFAULT_KEEP_RECENT = 3  # fallback for unrecognised compactable tools


def _load_keep_recent_tiers() -> dict[str, int]:
    """惰性读取 microcompact 分档表；加载失败回退内置 TOOL_KEEP_RECENT。"""
    try:
        from prismclaw.infra.settings import load_settings
        tiers = load_settings().agent.context.compaction.microcompact_keep_recent
        if isinstance(tiers, dict) and tiers:
            return tiers
    except Exception:
        pass
    return TOOL_KEEP_RECENT


def keep_recent_for(tool_name: str) -> int:
    """Retention window; web-retrieval MCP tools are tier-1 ephemeral."""
    if is_network_read_tool(tool_name):
        return 1
    tiers = _load_keep_recent_tiers()
    return tiers.get(tool_name, _DEFAULT_KEEP_RECENT)

# Tier 1 tools: ephemeral — archive to disk is wasteful, just clear with preview
_TIER1_EPHEMERAL = frozenset({
    *_LEGACY_WEB_TOOLS, "fetch_mcp_resource",
    "tool_search", "skill_search", "activate_skill",
})


def is_tier1_ephemeral(tool_name: str) -> bool:
    return tool_name in _TIER1_EPHEMERAL or is_network_read_tool(tool_name)


@dataclass(frozen=True, slots=True)
class QueryMicrocompactResult:
    changed: bool
    messages: list[Any]
    cleared_count: int


def _build_cleared_reference(
    tool_name: str,
    call_id: str,
    original_content: Any,
) -> str:
    """Build a recovery-enabled cleared marker with content preview.

    Instead of a dead "[content cleared]" marker, we include:
    - The original tool name
    - The call ID (for traceability)
    - The original content size
    - A content preview (first ~500 chars) so the LLM can decide
      whether re-fetching is worthwhile
    - Recovery guidance based on tool type
    """
    original_size = 0
    if isinstance(original_content, str):
        original_size = len(original_content)
    elif isinstance(original_content, list):
        for block in original_content:
            if isinstance(block, dict) and block.get("type") == "text":
                original_size += len(block.get("text", ""))
            elif hasattr(block, "text"):
                original_size += len(getattr(block, "text", "") or "")

    # Extract a short preview of the original content
    preview_text = _extract_text(original_content)
    preview = preview_text[:500].strip() if preview_text else ""

    recovery_tips = {
        "read": "使用 `read` 工具重新读取该文件。",
        "read_image": "使用 `read` 工具重新读取该图片。",
        # read_office / bash_read：历史 transcript 的工具名（键保留用于
        # 匹配旧记录），但提示文本必须指向现存工具，否则恢复提示会把
        # LLM 导向已删除的工具形成 doom-loop。
        "read_office": "使用 `read` 工具重新读取该文档。",
        "grep": "使用 `grep` 工具重新执行搜索。",
        "glob": "使用 `glob` 工具重新执行搜索。",
        "ls": "使用 `ls` 工具重新列出该目录。",
        "web_search": "使用 `web_search` 工具重新执行网页搜索。",
        "web_fetch": "使用 `web_fetch` 工具重新抓取该 URL。",
        "web_extract": "使用 `web_extract` 工具重新提取该 URL。",
        "fetch_mcp_resource": "使用 `fetch_mcp_resource` 工具重新获取该 MCP 资源。",
        "bash": "使用 `bash` 工具重新执行该命令。",
        "bash_read": "使用 `bash` 工具重新执行该只读命令。",
        "python": "使用 `python` 工具重新执行该脚本。",
        "reduce_subagent_results": "如需要，请重新运行该子代理。",
        "tool_search": "使用 `tool_search` 工具重新检索。",
        "skill_search": "使用 `skill_search` 工具重新检索。",
        "activate_skill": "使用 `activate_skill` 工具重新激活该技能。",
    }
    tip = recovery_tips.get(tool_name, f"如需使用，请重新调用 `{tool_name}` 工具。")

    parts = [
        f"{_CLEARED_TAG_PREFIX} tool={tool_name} call_id={call_id} "
        f"原大小={original_size} 字符]。",
    ]
    if preview:
        parts.append(f"预览：{preview}")
    parts.append(tip)
    return "\n".join(parts)


def _content_size(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                total += len(block.get("text", ""))
            elif hasattr(block, "text"):
                total += len(getattr(block, "text", "") or "")
    return 0


def _resolve_results_dir(working_dir: str | Path | None) -> Path | None:
    """Resolve and create the tool_results subdirectory if possible."""
    if working_dir is None:
        return None
    try:
        d = Path(working_dir) / "tool_results"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _extract_text(content: Any) -> str:
    """Extract plain text from tool result content (str or list[ContentBlock])."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(getattr(block, "text", "") or "")
        return "\n".join(parts)
    return ""


def _archive_and_reference(
    *,
    content: Any,
    tool_name: str,
    call_id: str,
    tool_args: str,
    results_dir: Path | None,
) -> str | None:
    """Archive tool result to disk and return a persisted-output reference.

    Returns ``None`` when archiving is not possible (no working_dir,
    write failure, or empty content), signalling the caller to fall back
    to the stub marker.
    """
    if results_dir is None:
        return None

    text = _extract_text(content)
    if not text:
        return None

    try:
        import hashlib
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        safe_id = call_id.replace("/", "_").replace("\\", "_").replace("..", "_").replace(":", "_")
        filename = f"mc_{safe_id}_{content_hash}.txt"
        persist_path = results_dir / filename
        persist_path.write_text(text, encoding="utf-8")

        preview = text[:500]
        desc = _describe_content(tool_name, text, tool_args)

        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"tool: {tool_name}  call_id: {call_id}\n"
            f"args: {tool_args[:200]}\n"
            f"size: {len(text)} chars\n"
            f"{desc}\n"
            f"preview: {preview}\n"
            f"[完整输出已归档至：tool_results/{filename}] "
            f"使用 `read` 工具查看完整内容。\n"
        )
    except OSError as exc:
        logger.warning("microcompact archive failed: call_id=%s err=%s", call_id, exc)
        return None


def _describe_content(tool_name: str, text: str, tool_args: str) -> str:
    """Build a human-readable description of the archived content."""
    lines = text.split("\n")
    line_count = len(lines)

    if tool_name == "read":
        path = _extract_arg(tool_args, "file_path") or _extract_arg(tool_args, "path") or "file"
        return f"内容：读取文件 {path}（{line_count} 行）"
    elif tool_name in ("read_office", "read_image"):
        path = _extract_arg(tool_args, "file_path") or _extract_arg(tool_args, "path") or "document"
        return f"内容：{tool_name} 处理 {path}"
    elif tool_name == "grep":
        match_count = sum(1 for l in lines if l and not l.startswith(" ") and ":" in l)
        return f"内容：grep 搜索结果（{match_count} 处匹配）"
    elif tool_name == "glob":
        return f"内容：glob 搜索列表（{line_count} 项）"
    elif tool_name == "ls":
        return f"内容：目录列表（{line_count} 项）"
    elif tool_name in ("bash", "bash_read"):
        cmd = _extract_arg(tool_args, "command") or tool_args[:100]
        return f"内容：`{cmd}` 的 bash 输出（{line_count} 行）"
    elif tool_name == "python":
        return f"内容：Python 脚本输出（{line_count} 行）"
    elif tool_name == "reduce_subagent_results":
        return f"内容：子代理结果汇总（{line_count} 行）"
    else:
        return f"内容：{tool_name} 结果（{line_count} 行）"


def _extract_arg(args_str: str, key: str) -> str:
    """Try to extract a JSON argument value from tool args string."""
    try:
        import json
        parsed = json.loads(args_str)
        val = parsed.get(key, "")
        return str(val) if val else ""
    except (json.JSONDecodeError, AttributeError):
        needle = f'"{key}"'
        idx = args_str.find(needle)
        if idx < 0:
            return ""
        rest = args_str[idx + len(needle):]
        for i, ch in enumerate(rest):
            if ch == '"':
                start = i + 1
                end = rest.find('"', start)
                if end > start:
                    return rest[start:end]
                break
        return ""


def microcompact_query_messages(
    messages: list[Any],
    *,
    working_dir: str | Path | None = None,
) -> QueryMicrocompactResult:
    """Layer 3 read-time microcompact (Claude-Code ``query.ts`` alignment).

    Clears the content of old compactable tool results while preserving
    the tool_use/tool_result pair structure. Pure projection: the input
    list is never mutated and nothing is persisted — the transformation
    applies only to ``messages_for_query`` for the upcoming request.

    Each cleared result is either:
    - **Archived** to disk (when ``working_dir`` is provided): the full
      content is written under ``{working_dir}/tool_results/`` and the
      inline content is replaced with a ``<persisted-output>`` reference
      that the model can read back via the Read tool.
    - **Stubbed** (when no ``working_dir``): replaced with a lightweight
      ``[已清除的旧工具结果]`` 标记 + 恢复指引。

    Retention is tiered per tool type (see ``TOOL_KEEP_RECENT``).
    Ephemeral results (web_search) keep only 1; persistent results
    (read) keep up to 8.
    """
    # Pass 1: build call_id → tool_name/args mapping and collect compactable IDs
    call_id_to_tool: dict[str, str] = {}
    call_id_to_args: dict[str, str] = {}
    compactable_ids: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            func = (tool_call or {}).get("function") or {}
            name = func.get("name")
            if name and is_compactable_tool(name):
                call_id = tool_call.get("id")
                if isinstance(call_id, str) and call_id:
                    call_id_to_tool[call_id] = name
                    call_id_to_args[call_id] = func.get("arguments") or ""
                    compactable_ids.append(call_id)

    if not compactable_ids:
        return QueryMicrocompactResult(changed=False, messages=messages, cleared_count=0)

    # Pass 2: determine which IDs to clear, per-tool tiered retention
    # Group IDs by tool name, apply per-tool keep_recent, then union
    tool_to_ids: dict[str, list[str]] = {}
    for cid in compactable_ids:
        tool_name = call_id_to_tool.get(cid, "")
        tool_to_ids.setdefault(tool_name, []).append(cid)

    clear_ids: set[str] = set()
    for tool_name, ids in tool_to_ids.items():
        keep = keep_recent_for(tool_name)
        keep = max(1, keep)
        if len(ids) > keep:
            clear_ids.update(ids[:-keep])

    if not clear_ids:
        return QueryMicrocompactResult(changed=False, messages=messages, cleared_count=0)

    # Pass 3: clear old results
    # - Tier 1 (ephemeral): no archive, just preview+stub
    # - Tier 2/3: archive to disk if possible, else preview+stub
    projected: list[Any] = list(messages)
    cleared_count = 0
    results_dir = _resolve_results_dir(working_dir)

    for index, message in enumerate(projected):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        if call_id not in clear_ids:
            continue
        content = message.get("content")
        if _is_cleared(content) or is_persisted_content(content):
            continue
        tool_name = call_id_to_tool.get(call_id, "unknown")
        tool_args = call_id_to_args.get(call_id, "")
        new_message = dict(message)

        if is_tier1_ephemeral(tool_name):
            # Ephemeral: just preview+stub, no disk archive
            new_message["content"] = _build_cleared_content(content, tool_name, call_id)
        else:
            # Tier 2/3: try to archive to disk with description
            archived = _archive_and_reference(
                content=content,
                tool_name=tool_name,
                call_id=call_id,
                tool_args=tool_args,
                results_dir=results_dir,
            )
            if archived is not None:
                new_message["content"] = archived
            else:
                new_message["content"] = _build_cleared_content(content, tool_name, call_id)

        projected[index] = new_message
        cleared_count += 1

    if cleared_count:
        logger.info(
            "microcompact_query_messages: cleared %d old tool results (tiered retention, archived=%s)",
            cleared_count,
            results_dir is not None,
        )
    return QueryMicrocompactResult(
        changed=cleared_count > 0,
        messages=projected,
        cleared_count=cleared_count,
    )


def _is_cleared(content: Any) -> bool:
    """True if content already carries a "cleared" marker."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if _CLEARED_TAG_PREFIX in block.get("text", ""):
                    return True
            elif hasattr(block, "text") and _CLEARED_TAG_PREFIX in (getattr(block, "text", "") or ""):
                return True
    elif isinstance(content, str):
        if _CLEARED_TAG_PREFIX in content:
            return True
    return False


def _build_cleared_content(content: Any, tool_name: str, call_id: str) -> Any:
    """Build cleared content with full recovery guidance."""
    cleared_text = _build_cleared_reference(tool_name, call_id, content)
    if isinstance(content, list):
        return [{"type": "text", "text": cleared_text}]
    return cleared_text


def resolve_microcompact_mode() -> str:
    """读取 microcompact 触发模式；缺省 batch（v4 §5.4）。"""
    try:
        from prismclaw.infra.settings import load_settings

        mode = load_settings().agent.context.compaction.microcompact_mode
        if mode in ("incremental", "batch"):
            return mode
    except Exception:
        pass
    return "batch"


def tool_result_chars(messages: list[Any]) -> int:
    """统计消息列表中工具结果（role == "tool"）占用的字符数。"""
    total = 0
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "tool":
            total += _content_size(message.get("content"))
    return total


def batch_cleanup_threshold(
    *,
    budget: Any,
    tool_results: Any,
    window: int | None = None,
    model_max_output: int | None = None,
    tools_wire: int = 0,
) -> int:
    """batch 触发阈值：ratio 取 zones.result 预算；absolute 取 aggregate_char_budget。

    计数器按「字符」累计（tool_result_chars 用 len 计），故两个分支都
    必须返回字符口径：ratio 的 zones.result 是 token，按 4 字符/token
    换算（与 absolute 回退 dynamic_zone_tokens×4 同一估算口径）。
    """
    mode = getattr(budget, "mode", "absolute")
    if mode == "ratio":
        from prismclaw.engine.compression.context_zones import zone_of

        return int(
            zone_of(
                budget,
                "result",
                window=window,
                model_max_output=model_max_output,
                tools_wire=tools_wire,
            )
        ) * 4
    agg = getattr(tool_results, "aggregate_char_budget", None)
    if agg is None:
        # dynamic_zone_tokens 是 token 值，按 4 字符/token 换算成字符阈值，
        # 与 _result_budget 的派生口径一致。
        agg = max(0, int(getattr(budget, "dynamic_zone_tokens", 0) or 0)) * 4
    return int(agg)


def batch_microcompact_tick(
    *,
    messages: list[Any],
    counter: int,
    prev_total: int,
    threshold: int,
    working_dir: str | Path | None = None,
) -> tuple[int, int, "QueryMicrocompactResult | None"]:
    """batch 模式单轮决策：只累加「本轮新增」的 R 区结果字符，超阈值才批量清理。

    增量 = 当前全量结果字符 − 上一轮快照（prev_total）。上一轮清理导致总量
    下降时取 0（压缩本身已处理过清理，本轮无新增）。Returns
    ``(new_counter, new_total, result)``；``result`` 仅在触发清理时非 None，
    且此时 ``new_counter`` 归零；``new_total`` 恒为当前全量字符快照，调用方
    写回状态供下一轮作 prev_total。
    """
    total = max(0, tool_result_chars(messages))
    delta = max(0, total - max(0, int(prev_total)))
    new_counter = max(0, int(counter)) + delta
    if threshold > 0 and new_counter >= threshold:
        return 0, total, microcompact_query_messages(messages, working_dir=working_dir)
    return new_counter, total, None
