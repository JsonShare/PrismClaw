from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismclaw.schema.agent.session import MessageEntry

if TYPE_CHECKING:
    from prismclaw.engine.execution.dispatch.call_handling import ParsedToolCall


_FILE_PATH_TOOLS = frozenset({"read", "write", "edit", "multi_edit", "glob", "grep"})
_FILE_PATH_ARG_NAMES = frozenset({"path", "file_path", "file", "filepath"})


def _is_ask_user_question(call: "ParsedToolCall") -> bool:
    """判断一个工具调用是否为 AskUserQuestion（人机交互提问）。

    parsed_calls 的元素是 ParsedToolCall 对象（见 call_handling.parse_tool_calls），
    不是 dict，因此访问 .name 属性而非 .get(...)。
    """
    name = getattr(call, "name", "") or ""
    name_lower = name.lower()
    return "askuserquestion" in name_lower or "ask_user_question" in name_lower or "ask_user" in name_lower


def _extract_file_paths_from_tool_calls(tool_calls: list) -> list[str]:
    """Extract file paths from tool call arguments for conditional skill matching.

    Tracks file paths from tools like read, write, edit, glob, grep so that
    skills with ``paths`` frontmatter patterns can be auto-activated.
    """
    paths: list[str] = []
    for call in tool_calls:
        try:
            name = call.name if hasattr(call, "name") else getattr(call, "function", {}).get("name", "")
            if name not in _FILE_PATH_TOOLS:
                continue
            args = call.arguments if hasattr(call, "arguments") else getattr(call, "function", {}).get("arguments", {})
            if not isinstance(args, dict):
                continue
            for arg_name in _FILE_PATH_ARG_NAMES:
                if arg_name in args and isinstance(args[arg_name], str) and args[arg_name]:
                    paths.append(args[arg_name])
                    break
            if name == "glob":
                pattern = args.get("pattern", "")
                if isinstance(pattern, str) and pattern:
                    paths.append(pattern)
        except Exception:
            continue
    return paths


def _entry_to_llm_dict(entry: MessageEntry) -> dict[str, Any]:
    llm_content = entry.enriched_content if entry.enriched_content is not None else entry.content
    out: dict[str, Any] = {"role": entry.role, "content": llm_content}
    if entry.tool_calls:
        out["tool_calls"] = entry.tool_calls
    if entry.tool_call_id:
        out["tool_call_id"] = entry.tool_call_id
    return out