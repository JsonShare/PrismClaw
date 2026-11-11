"""Post-compact file re-injection（查询时重建，ephemeral）.

对齐 Claude-Code：attachments 不持久化到 messages 数组，每次查询重新
读取最新文件内容注入 ``messages_for_query``，仅当会话已发生过压缩
（存在 CompactionEntry）时启用。

预算按 128K 缩放：5 文件 × 3,200 tokens = 16,000 tokens 全局。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session.types import CompactionEntry

logger = logging.getLogger(__name__)

_READ_TOOL_NAMES = {"read", "builtin.read", "Read", "read_office", "read_image"}


def _resolve_reinject_budgets() -> tuple[int, int]:
    """读配置：(reinject_max_files, reinject_file_tokens)。

    全局预算 = 单文件 × 文件数（128K 基线 3200×5=16000），不另设键。
    配置加载失败时回退同值字面量。
    """
    try:
        from prismclaw.infra.settings import load_settings
        settings = load_settings()
        return (
            int(settings.agent.context.compaction.reinject_max_files),
            int(settings.agent.context.budget.subcaps.reinject_file_tokens),
        )
    except Exception:
        return 5, 3_200


def build_file_reinjection_messages(
    *,
    tree: SessionTree,
    estimate_tokens: Callable[[str], int],
) -> list[dict[str, str]]:
    """构建文件 re-injection 消息列表（ephemeral，不持久化）。

    逐级披露模式：
      1. 完全注入 —— 文件在预算内，完整注入。
      2. 头部+尾部预览 —— 超预算文件注入头部 + 尾部预览 + 行计数 + 恢复指引。
      3. 引用指引 —— 超大文件仅注入元信息 + Read 指引。

    仅当 tree 中存在 CompactionEntry（已发生过压缩）时启用。
    """
    branch = tree.get_branch()
    if not any(isinstance(e, CompactionEntry) for e in branch):
        return []  # 未压缩过，无需 re-injection

    file_limit, file_tokens = _resolve_reinject_budgets()
    global_tokens = file_tokens * file_limit

    recent_paths = _extract_recent_read_paths(branch, limit=file_limit)
    messages: list[dict[str, str]] = []
    used = 0
    for file_path in recent_paths:
        if used >= global_tokens:
            break
        content = _read_file(file_path)
        if content is None:
            continue
        file_lines = content.count("\n") + 1
        tokens = estimate_tokens(content)
        if tokens > file_tokens:
            # Path 2: 头部 + 尾部预览
            head_chars = int(file_tokens * 2.5)  # ~8000 chars head
            tail_chars = int(file_tokens * 1.5)  # ~4800 chars tail
            head = content[:head_chars]
            tail = content[-tail_chars:] if len(content) > tail_chars else ""

            parts = [
                f"[压缩后重新注入文件：{file_path}]",
                f"[完整文件：{len(content)} 字符，磁盘上共 {file_lines} 行]",
                f"[预览：头部 {len(head)} 字符，尾部 {len(tail)} 字符]",
                "",
                "--- 头部 ---",
                head,
            ]
            if tail and len(content) > head_chars + tail_chars:
                parts.extend([
                    "",
                    f"……（中间省略 {len(content) - head_chars - tail_chars} 字符）",
                    "",
                    "--- 尾部 ---",
                    tail,
                ])
            parts.extend([
                "",
                f"[如需读取特定片段：请用 read 工具并传入 offset/limit 参数，"
                f"或用 grep 检索模式。"
                f"示例：read(file_path=\"{file_path}\", offset=100, limit=50)]",
            ])
            body = "\n".join(parts)
            tokens = file_tokens
        else:
            body = f"[压缩后重新注入文件：{file_path}]\n\n{content}"
        if used + tokens > global_tokens:
            break
        messages.append({"role": "user", "content": body})
        used += tokens
    return messages


def _extract_recent_read_paths(branch: list[Any], limit: int) -> list[str]:
    """从 MessageEntry.tool_calls（OpenAI 格式，arguments 为 JSON 字符串）
    由新到旧提取去重后的 read 工具路径。"""
    paths: list[str] = []
    seen: set[str] = set()
    for entry in reversed(branch):
        if len(paths) >= limit:
            break
        if not isinstance(entry, MessageEntry):
            continue
        for tc in entry.tool_calls or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            if fn.get("name") not in _READ_TOOL_NAMES:
                continue
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (json.JSONDecodeError, TypeError):
                continue
            path = args.get("file_path") or args.get("path")
            if isinstance(path, str) and path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _read_file(file_path: str) -> str | None:
    try:
        path = Path(file_path)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("file_reinjection.read_failed path=%s err=%s", file_path, exc)
        return None
