from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.staging.tool_result_persistence import (
    PERSISTED_OUTPUT_TAG,
    is_persisted_content,
)
from prismclaw.engine.toolkit.registry.tool_classification import is_network_read_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolResultLifecycle:
    """单个工具结果的保留窗口与清除后的恢复指引。

    只登记当前存在的工具；恢复提示必须指向现存工具，否则会把模型导向
    已删除的工具。
    """

    keep_recent: int
    restore_hint: str


_TOOL_LIFECYCLE: dict[str, ToolResultLifecycle] = {
    # Tier 1: ephemeral — consumed in the current reasoning step
    "fetch_mcp_resource": ToolResultLifecycle(
        1, "使用 `fetch_mcp_resource` 工具重新获取该 MCP 资源。"
    ),
    "tool_search": ToolResultLifecycle(1, "使用 `tool_search` 工具重新检索。"),
    "skill_search": ToolResultLifecycle(1, "使用 `skill_search` 工具重新检索。"),
    "activate_skill": ToolResultLifecycle(1, "使用 `activate_skill` 工具重新激活该技能。"),
    # Tier 2: short-term — may be referenced 1-3 turns later
    "bash": ToolResultLifecycle(3, "使用 `bash` 工具重新执行该命令。"),
    "ls": ToolResultLifecycle(4, "使用 `ls` 工具重新列出该目录。"),
    "grep": ToolResultLifecycle(4, "使用 `grep` 工具重新执行搜索。"),
    "glob": ToolResultLifecycle(4, "使用 `glob` 工具重新执行搜索。"),
    # Tier 3: persistent — multi-turn reference (code, docs, reports)
    "read": ToolResultLifecycle(8, "使用 `read` 工具重新读取该文件。"),
}
_DEFAULT_KEEP_RECENT = 3  # fallback for unrecognised compactable tools
_DEFAULT_KEEP_RECENT_TIERS = {name: spec.keep_recent for name, spec in _TOOL_LIFECYCLE.items()}


def is_compactable_tool(tool_name: str) -> bool:
    """Compactability: known lifecycle entry + any configured web-retrieval MCP tool."""
    return tool_name in _TOOL_LIFECYCLE or is_network_read_tool(tool_name)


_CLEARED_TAG_PREFIX = "[已清除的旧工具结果："

# ── Tiered retention ──────────────────────────────────────────────────
# Different tool results have different useful lifetimes.  Ephemeral
# results (web searches) are useful for one turn; persistent results
# (file reads) may be referenced across many turns.


def _load_keep_recent_tiers() -> dict[str, int]:
    """惰性读取配置分档表；加载失败回退内置分档。"""
    try:
        from prismclaw.infra.settings import load_settings

        tiers = load_settings().agent.context.compaction.microcompact_keep_recent
        if isinstance(tiers, dict) and tiers:
            return tiers
    except Exception:
        pass
    return _DEFAULT_KEEP_RECENT_TIERS


def keep_recent_for(tool_name: str) -> int:
    """Retention window; web-retrieval MCP tools are tier-1 ephemeral."""
    if is_network_read_tool(tool_name):
        return 1
    tiers = _load_keep_recent_tiers()
    return tiers.get(tool_name, _DEFAULT_KEEP_RECENT)


def is_tier1_ephemeral(tool_name: str) -> bool:
    if is_network_read_tool(tool_name):
        return True
    spec = _TOOL_LIFECYCLE.get(tool_name)
    return spec is not None and spec.keep_recent == 1


@dataclass(frozen=True, slots=True)
class QueryMicrocompactResult:
    changed: bool
    messages: list[Any]
    cleared_count: int


def _build_cleared_reference(
    tool_name: str,
    call_id: str,
    original_content: object,
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

    spec = _TOOL_LIFECYCLE.get(tool_name)
    tip = spec.restore_hint if spec else f"如需使用，请重新调用 `{tool_name}` 工具。"

    parts = [
        f"{_CLEARED_TAG_PREFIX} tool={tool_name} call_id={call_id} 原大小={original_size} 字符]。",
    ]
    if preview:
        parts.append(f"预览：{preview}")
    parts.append(tip)
    return "\n".join(parts)


def _content_size(content: object) -> int:
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


def _extract_text(content: object) -> str:
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
    content: object,
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
    elif tool_name == "grep":
        match_count = sum(1 for ln in lines if ln and not ln.startswith(" ") and ":" in ln)
        return f"内容：grep 搜索结果（{match_count} 处匹配）"
    elif tool_name == "glob":
        return f"内容：glob 搜索列表（{line_count} 项）"
    elif tool_name == "ls":
        return f"内容：目录列表（{line_count} 项）"
    elif tool_name == "bash":
        cmd = _extract_arg(tool_args, "command") or tool_args[:100]
        return f"内容：`{cmd}` 的 bash 输出（{line_count} 行）"
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
        rest = args_str[idx + len(needle) :]
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
    """Layer 3 读时微压缩：清理旧工具结果的内容，保留 tool_use/tool_result 结构。

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

    Retention is tiered per tool type (see ``_TOOL_LIFECYCLE``).
    Ephemeral results (fetch_mcp_resource / discovery) keep only 1; persistent results
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
            (
                "microcompact_query_messages: cleared %d old "
                "tool results (tiered retention, archived=%s)"
            ),
            cleared_count,
            results_dir is not None,
        )
    return QueryMicrocompactResult(
        changed=cleared_count > 0,
        messages=projected,
        cleared_count=cleared_count,
    )


def _is_cleared(content: object) -> bool:
    """True if content already carries a "cleared" marker."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if _CLEARED_TAG_PREFIX in block.get("text", ""):
                    return True
            elif hasattr(block, "text") and _CLEARED_TAG_PREFIX in (
                getattr(block, "text", "") or ""
            ):
                return True
    elif isinstance(content, str) and _CLEARED_TAG_PREFIX in content:
        return True
    return False


def _build_cleared_content(content: object, tool_name: str, call_id: str) -> object:
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
    budget: object,
    tool_results: object,
    window: int | None = None,
    model_max_output: int | None = None,
    tools_wire: int = 0,
) -> int:
    """batch 触发阈值：absolute 取 aggregate_char_budget，回退 dynamic_zone_tokens×4。

    计数器按「字符」累计（tool_result_chars 用 len 计），故返回字符口径：
    dynamic_zone_tokens 是 token 值，按 4 字符/token 换算。
    """
    agg = getattr(tool_results, "aggregate_char_budget", None)
    if agg is None:
        agg = max(0, int(getattr(budget, "dynamic_zone_tokens", 0) or 0)) * 4
    return int(agg)


def batch_microcompact_tick(
    *,
    messages: list[Any],
    counter: int,
    prev_total: int,
    threshold: int,
    working_dir: str | Path | None = None,
) -> tuple[int, int, QueryMicrocompactResult | None]:
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
