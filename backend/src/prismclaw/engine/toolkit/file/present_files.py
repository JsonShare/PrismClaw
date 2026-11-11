from __future__ import annotations

from pathlib import Path
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
    error_result,
    text_result,
)
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver, WorkspaceBoundaryError
from prismclaw.infra.workspace_paths import session_dir_in
from prismclaw.schema.content.content_types import ToolResult


class PresentFilesTool:
    name = "present_files"
    description = (
        "向用户展示最终交付文件——已完成的作品（报告、代码、图表、文档）。"
        "只有通过本工具调用才会出现在交付物面板中。请在全部文件完成后调用。"
        "只接受会话交付目录（sessions/<sid>/outputs/，见 workspace 段的「会话交付目录」）"
        "下的文件；目录之外的文件会被拒绝，需先复制/写入该目录再调用。"
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
                    "示例：[\"outputs/report.pdf\"]——确切目录见 workspace 段。"
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
    # 交付物唯一通道：文件必须在会话交付目录（sessions/<sid>/outputs/）内。
    # Web 展示链（_extract_presented_paths + ArtifactScanner 兜底扫描 +
    # SERVER_OUTPUTS）只认该目录。校验失败返回可纠正的错误（模型可复制
    # 文件后重试），而不是假成功——假成功会让模型声称"已展示"但前端无卡片。

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

        # 统一走 resolve_within——与 read/write/edit/file_tools 完全一致的路径解析
        # 裸路径 "outputs/x" → sessions/<sid>/outputs/x
        # 前缀 "outputs/x" → sessions/<sid>/outputs/x
        # 前缀 "sessions/<sid>/outputs/x" → 同名归一分支
        # session_root 用 session_dir_in 算——它会 sanitize subagent sid（`#`/`:`→`-`），
        # 与磁盘上的真实会话目录一致；同时用 context.workspace_path（当前会话真实
        # workspace 根），避免 resolver.resolve(workspace_id) 与 context 不一致。
        session_root = session_dir_in(Path(context.workspace_path), context.session_id)
        outputs_dir = session_root / "outputs"

        accepted: list[str] = []
        outside: list[str] = []
        missing: list[str] = []
        for raw in files:
            if not isinstance(raw, str) or not raw.strip():
                continue
            normalized = raw.strip().replace("\\", "/")
            try:
                full = self._resolver.resolve_within(
                    context.workspace_path,
                    normalized,
                    session_root=session_root,
                    for_write=False,
                )
            except WorkspaceBoundaryError:
                missing.append(normalized)
                continue
            try:
                full = full.resolve()
            except OSError:
                missing.append(normalized)
                continue
            if not full.is_file():
                missing.append(normalized)
                continue
            try:
                full.relative_to(outputs_dir.resolve())
            except ValueError:
                outside.append(normalized)
                continue
            accepted.append(normalized)

        if not accepted:
            details = []
            if outside:
                details.append(f"不在会话交付目录：{', '.join(f'`{p}`' for p in outside[:5])}")
            if missing:
                details.append(f"文件不存在：{', '.join(f'`{p}`' for p in missing[:5])}")
            return error_result(
                call_id,
                f"present_files：未展示任何文件。"
                f"本工具只接受会话交付目录 `outputs/` 下的文件。"
                f"（{'；'.join(details)}）"
                "请将最终交付物复制/写入该目录后重新调用。",
            )

        listed = ", ".join(f"`{f}`" for f in accepted[:10])
        suffix = f" 等，另有 {len(accepted) - 10} 个" if len(accepted) > 10 else ""
        result = f"已向用户展示 {len(accepted)} 个交付文件：{listed}{suffix}。"
        rejected = outside + missing
        if rejected:
            result += (
                f"另有 {len(rejected)} 个路径被忽略"
                f"（须在会话交付目录 `outputs/` 下且文件存在）："
                + ", ".join(f"`{p}`" for p in rejected[:5])
                + "。"
            )
        return text_result(call_id, result)
