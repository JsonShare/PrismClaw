from __future__ import annotations

from pathlib import Path
from typing import Any

from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
    error_result,
    text_result,
)
from prismclaw.infra.workspace_paths import session_dir_in
from prismclaw.schema.content.content_types import ToolResult


class PresentFilesTool:
    name = "present_files"
    description = (
        "声明最终交付文件——已完成的作品（报告、图表、文档）。"
        "只接受会话交付目录（sessions/<sid>/outbox/，见 workspace 段的「会话交付目录」）"
        "下的路径；目录之外的路径会被拒绝。本工具只做声明，不创建文件："
        "文件可以稍后写入，写入后会自动出现在交付物面板；尚未生成时返回「已登记」。"
        "只列出最终交付物，不要列出中间产物或工具输出。"
        "若本次任务没有产出任何文件，请不要调用本工具。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "会话交付目录下的交付物文件路径（相对工作区根或绝对路径）。"
                    '示例：["outbox/report.pdf"]——确切目录见 workspace 段。'
                ),
            },
        },
        "required": ["files"],
        "additionalProperties": False,
    }
    side_effect = False
    tool_class = "read"
    effect_class = "pure_read"
    approval_eligible = False
    is_concurrency_safe = True

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        files = args.get("files")
        if not isinstance(files, list) or len(files) == 0:
            return error_result(
                call_id,
                "present_files：没有可展示的文件。请提供一个非空的 'files' 列表，"
                "其中填写会话交付目录下的文件路径。",
            )
        if not context.session_id or not context.workspace_path:
            return error_result(
                call_id,
                "present_files：缺少会话上下文，无法确定会话交付目录，未展示任何文件。",
            )

        session_root = session_dir_in(Path(context.workspace_path), context.session_id)
        outbox_dir = session_root / "outbox"

        accepted: list[str] = []
        outside: list[str] = []
        pending: list[str] = []
        for raw in files:
            if not isinstance(raw, str) or not raw.strip():
                continue
            normalized = raw.strip().replace("\\", "/")
            # 只接受显式交付目录路径。裸相对路径会被 resolve_within 静默改写到
            # session outbox 下（如 `other/outbox/x` → `outbox/other/outbox/x`），
            # 与声明意图不符，直接按“不在交付目录”拒绝。
            is_abs = normalized.startswith("/") or normalized.startswith("~/")
            in_outbox_prefix = normalized.startswith("outbox/") or normalized == "outbox"
            in_session_prefix = normalized.startswith(f"sessions/{session_root.name}/outbox")
            if not is_abs and not (in_outbox_prefix or in_session_prefix):
                outside.append(normalized)
                continue
            try:
                full = self._resolver.resolve_within(
                    context.workspace_path,
                    normalized,
                    session_root=session_root,
                    for_write=False,
                )
            except WorkspaceBoundaryError:
                outside.append(normalized)
                continue
            try:
                full = full.resolve()
            except OSError:
                outside.append(normalized)
                continue
            try:
                full.relative_to(outbox_dir.resolve())
            except ValueError:
                outside.append(normalized)
                continue
            # 只登记落点：文件可能稍后才由 write/bash 创建，不在此处判存在。
            accepted.append(normalized)
            if not full.is_file():
                pending.append(normalized)

        if not accepted:
            details = []
            if outside:
                details.append(f"不在会话交付目录：{', '.join(f'`{p}`' for p in outside[:5])}")
            return error_result(
                call_id,
                "present_files：未登记任何交付文件。"
                "本工具只接受会话交付目录 `outbox/` 下的路径。"
                f"（{'；'.join(details)}）"
                "请改用 `outbox/<rel>` 形式的路径后重新声明。",
            )

        existing = [p for p in accepted if p not in pending]
        parts: list[str] = []
        if existing:
            listed = ", ".join(f"`{f}`" for f in existing[:10])
            suffix = f" 等，另有 {len(existing) - 10} 个" if len(existing) > 10 else ""
            parts.append(f"已向用户展示 {len(existing)} 个交付文件：{listed}{suffix}。")
        if pending:
            listed = ", ".join(f"`{f}`" for f in pending[:10])
            suffix = f" 等，另有 {len(pending) - 10} 个" if len(pending) > 10 else ""
            parts.append(
                f"已登记 {len(pending)} 个待生成交付文件：{listed}{suffix}；"
                "写入 `outbox/` 后会自动出现在交付物面板。"
            )
        result = "".join(parts)
        if outside:
            result += (
                f"另有 {len(outside)} 个路径被忽略"
                f"（须在会话交付目录 `outbox/` 下）："
                + ", ".join(f"`{p}`" for p in outside[:5])
                + "。"
            )
        return text_result(call_id, result)
