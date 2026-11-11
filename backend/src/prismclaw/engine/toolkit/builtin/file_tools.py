from __future__ import annotations

import asyncio
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.engine.toolkit.builtin.file_helpers import (
    _READ_DEFAULT_PAGE_LINES,
    _blocked_binary_extension,
    _binary_write_guidance,
    build_file_change,
    _ws_root,
    _ws_kind,
    _require_runtime,
    _sha256hex,
    session_root,
)
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.engine.toolkit.file.file_mutation_queue import with_file_mutation_queue
from prismclaw.engine.review.registry import get_review_registry
from prismclaw.schema.content.content_types import FileChangeEntry, UiArtifact
from prismclaw.schema.content.workspace import SandboxTier


# ─── ListDirectoryTool ────────────────────────────────────────────────

class ListDirectoryTool:
    name = "ls"
    description = (
        "列出工作区指定路径下的文件与目录。每条结果包含类型 "
        "（[DIR]/[FILE]）、名称、大小与修改时间。recursive 默认为 "
        "false（设为 true 可包含子目录）；ignore_hidden 默认为 "
        "true。如果需要按模式匹配而不是完整列举，请改用 glob。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作区的路径"},
            "recursive": {"type": "boolean", "description": "是否递归列出（默认：false）"},
            "ignore_hidden": {"type": "boolean", "description": "是否忽略隐藏文件/目录（默认：true）"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    side_effect = False
    tool_class = "read"
    effect_class = "pure_read"
    approval_eligible = False
    is_concurrency_safe = True

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self.resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:

        call_id = args.get("_call_id", "")
        path_str = args.get("path", ".")
        recursive = args.get("recursive", False)
        ignore_hidden = args.get("ignore_hidden", True)

        runtime = _require_runtime(context)
        _tier = getattr(context, "current_sandbox_tier", SandboxTier.STANDARD)

        try:
            resp = await runtime.fs_list(
                path=path_str,
                recursive=recursive,
                ignore_hidden=ignore_hidden,
                template_id=_ws_kind(context),
                workspace_id=context.workspace_id,
                workspace_root=_ws_root(context),
            )
        except Exception as exc:
            return error_result(call_id, f"ls：运行时执行失败：{exc}")

        entries: list[dict[str, Any]] = resp.get("entries", []) if isinstance(resp, dict) else []

        if isinstance(resp, dict) and resp.get("error"):
            return error_result(call_id, f"ls: {resp['error']}")

        if not entries:
            return text_result(call_id, "目录为空。")

        lines: list[str] = []
        import datetime as _dt
        for entry in entries:
            kind = "[DIR] " if entry.get("is_dir") else "[FILE]"
            name = entry.get("name", "")
            size = entry.get("size", 0)
            mtime = entry.get("modified_epoch", 0)
            try:
                time_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            except (OSError, ValueError):
                time_str = "-"
            lines.append(f"{kind}  {name}  (size={size}, modified={time_str})")

        return text_result(call_id, "\n".join(lines))


# ─── ReadTool ─────────────────────────────────────────────────────────

class ReadTool:
    name = "read"
    DEFAULT_PAGE_LINES = _READ_DEFAULT_PAGE_LINES
    MAX_LINE_LENGTH = 2000

    description = (
        "读取文件内容：文本、代码、文档 "
        "（.docx/.xlsx/.pptx/.pdf/.md）以及图片（.png/.jpg/.jpeg/.webp/.gif/"
        ".bmp/.svg/.ico/.tiff）。图片会返回由视觉模型生成的文本描述"
        "（OCR + 图像理解）——每次读取图片都会消耗视觉 token，因此请仅针对真正重要的图片使用，"
        "不要用于批量扫描；可传入 `question` 聚焦分析方向。"
        "文件按页读取：未显式指定 limit 时只返回前 "
        f"{_READ_DEFAULT_PAGE_LINES} 行，并附带续读标记以给出下一个 offset——"
        "对大文件请分页读取，而不是整体载入。对 .xlsx/.pptx，会保留结构锚点"
        "以供编辑操作使用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "相对于工作区的路径"},
            "offset": {"type": "integer", "description": "起始行号（从 1 开始）"},
            "limit": {"type": "integer", "description": "最多返回的行数"},
            "question": {
                "type": "string",
                "description": (
                    "仅对图片有效：针对该图片的自定义问题。默认进行综合分析"
                    "（OCR + 类型识别 + 描述 + 关键要点）。"
                ),
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }
    side_effect = False
    tool_class = "read"
    effect_class = "pure_read"
    approval_eligible = False
    is_concurrency_safe = True
    timeout_seconds = 120.0  # 图片分支走视觉模型 HTTP 调用，需要更长超时

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:

        call_id = args.get("_call_id", "")
        raw_path = args.get("file_path")
        if not isinstance(raw_path, str) or not raw_path:
            return error_result(call_id, "read：缺少 'file_path' 参数")

        if context.abort.is_set():
            return error_result(call_id, "read：已中止")

        try:
            full_path = self._resolver.resolve_within(
                context.workspace_path, raw_path, session_root=session_root(context),
            )
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, str(exc))

        # Image files — dispatched to the vision branch (former read_image
        # tool, merged into read). Function-level import keeps the builtin ↔
        # file packages decoupled at load time.
        ext = full_path.suffix.lower()
        from prismclaw.engine.toolkit.file.read_image import _IMAGE_EXTENSIONS, read_image_branch

        if ext in _IMAGE_EXTENSIONS:
            return await read_image_branch(
                call_id=call_id,
                raw_path=raw_path,
                question=args.get("question"),
                context=context,
                resolver=self._resolver,
            )

        # Legacy binary office formats are not supported by any reader —
        # fail with actionable guidance instead of a UnicodeDecodeError.
        if ext in (".doc", ".xls", ".ppt"):
            return error_result(
                call_id,
                f"read：'{ext}' 属于旧式二进制 Office 格式，无法直接读取。"
                "请先将该文件转换为开放格式（.docx/.xlsx/.pptx）。",
            )

        # Document formats (.docx/.xlsx/.pptx/.pdf/.md/...) — handled by the
        # document branch (former read_office tool, M1 merge). Function-level
        # import keeps the builtin ↔ file packages decoupled at load time.
        from prismclaw.engine.toolkit.file.read_office import (
            _fmt_for_path,
            read_document_branch,
        )

        if _fmt_for_path(raw_path) is not None:
            return await read_document_branch(
                call_id=call_id,
                raw_path=raw_path,
                offset=args.get("offset"),
                limit=args.get("limit"),
                context=context,
                resolver=self._resolver,
            )

        # Read as text — always through context.runtime
        try:
            r_result = await _require_runtime(context).read_text(
                str(full_path),
                template_id=_ws_kind(context),
                workspace_id=context.workspace_id,
                workspace_root=_ws_root(context),
            )
            if not r_result.success:
                return error_result(call_id, f"read：{r_result.error}")
            content = r_result.content
        except UnicodeDecodeError:
            size = full_path.stat().st_size
            return text_result(call_id, f"[二进制文件或编码不受支持。大小：{size} 字节]")
        except OSError as exc:
            return error_result(call_id, f"read：{exc}")

        # Compute content fingerprint for optimistic concurrency
        read_epoch = _sha256hex(content)

        # Line-level truncation: cap each line at MAX_LINE_LENGTH
        lines = content.splitlines(keepends=True)
        truncated_line_count = 0
        truncated_lines: list[str] = []
        for line in lines:
            if len(line) > self.MAX_LINE_LENGTH:
                truncated_lines.append(line[: self.MAX_LINE_LENGTH] + "… [truncated]\n")
                truncated_line_count += 1
            else:
                truncated_lines.append(line)

        total_lines = len(truncated_lines)

        # Pagination
        offset = args.get("offset")
        limit = args.get("limit")

        if isinstance(offset, int) or isinstance(limit, int):
            start = max(0, (offset - 1) if isinstance(offset, int) and offset > 0 else 0)
        else:
            start = 0

        if isinstance(limit, int) and limit > 0:
            end = start + limit
        else:
            end = start + ReadTool.DEFAULT_PAGE_LINES

        if start >= total_lines and total_lines > 0:
            return text_result(
                call_id,
                f"[read：offset {start + 1} 已超出文件末尾"
                f"（共 {total_lines} 行）。无可显示内容。]",
                metadata={"read_epoch": read_epoch},
            )

        window = "".join(truncated_lines[start:end])
        returned_lines = min(end, total_lines) - start

        # Smart footer: continuation marker when not all lines shown
        if end < total_lines:
            shown_from = start + 1
            shown_to = min(end, total_lines)
            window += (
                f"\n[当前显示第 {shown_from}-{shown_to} 行，共 {total_lines} 行。"
                f"继续读取请调用：read(file_path={raw_path!r}, offset={shown_to + 1})]\n"
            )

        # Record observation for file_observer (if read_file_state is a dict)
        if hasattr(context, "read_file_state") and context.read_file_state is not None:
            if isinstance(context.read_file_state, dict):
                context.read_file_state[raw_path] = {
                    "read_epoch": read_epoch,
                    "line_count": total_lines,
                }

        return text_result(
            call_id,
            window,
            metadata={
                "file_path": raw_path,
                "total_lines": total_lines,
                "returned_lines": returned_lines,
                "read_epoch": read_epoch,
                "truncated_lines": truncated_line_count,
            },
        )


# ─── WriteTool ────────────────────────────────────────────────────────

class WriteTool:
    name = "write"
    description = "在工作区内写入（创建或覆盖）文本文件，并自动创建缺失的目录。硬性规则：绝不要用于 Office/二进制文件（.docx、.pptx、.xlsx、.pdf）——写入文本会产生损坏文件。这些文件应先用 `read` 读取，再用对应技能（通过 `activate_skill('docx')` 等激活）或 `bash` 调用相应 CLI/库来修改。"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "相对于工作区的路径"},
            "content": {"type": "string", "description": "要写入的文件内容"},
            "create_dirs": {"type": "boolean", "description": "若父目录不存在则自动创建", "default": False},
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }
    side_effect = True
    tool_class = "write"
    effect_class = "local_mutation"
    is_concurrency_safe = True
    timeout_seconds = 30.0

    MAX_EDIT_FILE_SIZE = 100 * 1024 * 1024

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:

        call_id = args.get("_call_id", "")
        raw_path = args.get("file_path")
        content = args.get("content")
        if not isinstance(raw_path, str) or not raw_path:
            return error_result(call_id, "write：缺少 'file_path' 参数")
        if not isinstance(content, str):
            return error_result(call_id, "write：'content' 必须是字符串")

        blocked_ext = _blocked_binary_extension(raw_path)
        if blocked_ext is not None:
            return error_result(call_id, _binary_write_guidance("write", blocked_ext))

        if context.abort.is_set():
            return error_result(call_id, "write：已中止")

        try:
            full_path = self._resolver.resolve_within(
                context.workspace_path, raw_path, session_root=session_root(context),
                for_write=True,
            )
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, str(exc))

        runtime = _require_runtime(context)
        _wk = _ws_kind(context)
        _root = _ws_root(context)

        # Serialize concurrent write/edit on the same file (write tool is
        # concurrency-safe only across *different* files; same-file mutation
        # must be ordered to keep baseline/diff/epoch consistent).
        async def _do_write() -> Any:
            baseline_ok = True
            old_text = ""
            before_content: bytes | None = b""
            r_base = await runtime.read_text(
                str(full_path),
                template_id=_wk,
                workspace_id=context.workspace_id,
                workspace_root=_root,
            )
            if r_base.success:
                old_text = r_base.content or ""
                before_content = old_text.encode("utf-8")
            elif "file not found" in (r_base.error or ""):
                before_content = None  # new file -> delete on reject
            else:
                baseline_ok = False

            try:
                w_result = await runtime.write_text(
                    str(full_path),
                    content,
                    template_id=_wk,
                    workspace_id=context.workspace_id,
                    workspace_root=_root,
                )
                if not w_result.success:
                    return error_result(call_id, f"write：{w_result.error}")
            except OSError as exc:
                return error_result(call_id, f"write：{exc}")

            ui_artifact = None
            if baseline_ok:
                before_sha = _sha256hex(old_text) if old_text else ""
                change = build_file_change(
                    raw_path, old_text, content,
                    before_ref="", before_sha=before_sha,
                )
                if change["insertions"] or change["deletions"]:
                    ui_artifact = UiArtifact(
                        artifact_type="file_changes",
                        file_changes=[FileChangeEntry.model_validate(change)],
                    )
                    conversation_id = context.parent_session_id or context.session_id
                    after_content = content.encode("utf-8") if content else b""
                    registry = get_review_registry(context.workspace_path)
                    await asyncio.to_thread(
                        registry.register_edit,
                        conversation_id, raw_path, before_content, after_content,
                        context.session_id, call_id,
                    )

            # Compute read_epoch of written content for subsequent edit
            read_epoch = _sha256hex(content)

            # Update read_file_state observation
            if hasattr(context, "read_file_state") and context.read_file_state is not None:
                if isinstance(context.read_file_state, dict):
                    context.read_file_state[raw_path] = {
                        "read_epoch": read_epoch,
                        "line_count": content.count("\n") + 1,
                    }

            return text_result(
                call_id,
                f"write：已成功向 {raw_path} 写入 {len(content)} 个字符",
                ui_artifact=ui_artifact,
                metadata={
                    "file_path": raw_path,
                    "bytes_written": len(content.encode("utf-8")),
                    "lines": content.count("\n") + 1,
                    "read_epoch": read_epoch,
                },
            )

        return await with_file_mutation_queue(str(full_path), _do_write)


# ─── EditTool ─────────────────────────────────────────────────────────

class EditTool:
    name = "edit"
    description = "在文本文件中将唯一的 old_string 替换为 new_string。多处匹配时使用 replace_all。对于 Office 文档（.docx/.xlsx/.pptx）：先用 `read` 读取，再通过 Office 文件技能（activate_skill('docx') 等）修改——绝不要把它们当纯文本编辑。"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "相对于工作区的路径"},
            "old_string": {"type": "string", "description": "要替换的精确文本"},
            "new_string": {"type": "string", "description": "替换后的文本"},
            "replace_all": {"type": "boolean", "description": "替换所有出现位置", "default": False},
            "read_epoch": {"type": "string", "description": "可选的 SHA-256 指纹，来自 `read` 工具。若提供，当文件在读取后发生变化时编辑会失败。"},
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    }
    side_effect = True
    tool_class = "write"
    effect_class = "local_mutation"
    is_concurrency_safe = True
    timeout_seconds = 30.0

    MAX_EDIT_FILE_SIZE = 100 * 1024 * 1024

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:

        call_id = args.get("_call_id", "")
        raw_path = args.get("file_path")
        old = args.get("old_string")
        new = args.get("new_string")
        replace_all = bool(args.get("replace_all", False))

        if not isinstance(raw_path, str) or not raw_path:
            return error_result(call_id, "edit：缺少 'file_path' 参数")
        if not isinstance(old, str) or not isinstance(new, str):
            return error_result(call_id, "edit：'old_string' 与 'new_string' 必须是字符串")

        blocked_ext = _blocked_binary_extension(raw_path)
        if blocked_ext is not None:
            return error_result(call_id, _binary_write_guidance("edit", blocked_ext))

        if context.abort.is_set():
            return error_result(call_id, "edit：已中止")

        # edit 走读链（SDD §3.1 L127）：裸路径读兜底定位 → 应用补丁 → 写回，
        # 全程不传 for_write=True，避免把合法的裸路径原件编辑拦截成边界错误。
        try:
            full_path = self._resolver.resolve_within(
                context.workspace_path, raw_path, session_root=session_root(context),
            )
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, str(exc))

        runtime = _require_runtime(context)
        _wk = _ws_kind(context)
        _root = _ws_root(context)

        # Serialize concurrent write/edit on the same file: the read →
        # epoch check → write sequence must be atomic against other
        # write/edit calls targeting the same path.
        async def _do_edit() -> Any:
            r_result = await runtime.read_text(
                str(full_path),
                template_id=_wk,
                workspace_id=context.workspace_id,
                workspace_root=_root,
            )
            if not r_result.success:
                return error_result(call_id, f"edit：文件不存在：{raw_path}")
            content = r_result.content or ""

            if len(content) > self.MAX_EDIT_FILE_SIZE:
                size_mb = len(content) / (1024 * 1024)
                return error_result(
                    call_id,
                    f"edit：文件过大，无法编辑（{size_mb:.2f}MB）。"
                    f"可编辑文件大小上限为 "
                    f"{self.MAX_EDIT_FILE_SIZE / (1024 * 1024):.2f}MB。",
                )

            # Optimistic concurrency: verify read_epoch if provided
            claimed_epoch = args.get("read_epoch")
            current_epoch = _sha256hex(content)
            if isinstance(claimed_epoch, str) and claimed_epoch:
                if current_epoch != claimed_epoch:
                    return error_result(
                        call_id,
                        "edit：文件在读取之后已被修改（read_epoch 不匹配）。"
                        "请先重新读取该文件，然后再编辑。",
                    )

            before_sha = current_epoch if content else ""

            # Normalize CRLF to LF to prevent cross-platform mismatch.
            # (``old`` is reassigned here, so it must not shadow the outer
            # parameter inside this closure — use a distinct local name.)
            old_n = old.replace('\r\n', '\n')
            content = content.replace('\r\n', '\n')

            count = content.count(old_n)
            if count == 0:
                import re
                def normalize(s: str) -> str:
                    return re.sub(r'[ \t]+$', '', s.strip(), flags=re.MULTILINE)

                if normalize(old_n) in normalize(content):
                    return error_result(
                        call_id,
                        "edit：精确匹配失败，但找到了近似匹配。"
                        "你的 old_string 可能缩进有误、尾部空白不一致或缺少换行。"
                        "请用 `read` 查看文件中的精确内容。",
                    )
                return error_result(call_id, f"edit：在 {raw_path} 中未找到 old_string")

            if count > 1 and not replace_all:
                return error_result(
                    call_id,
                    f"edit：old_string 在 {raw_path} 中出现了 {count} 次；"
                    "请设置 replace_all=true，或提供一段唯一的片段",
                )

            updated = content.replace(old_n, new) if replace_all else content.replace(old_n, new, 1)

            # Verify Python syntax if it's a Python file
            if raw_path.endswith(".py"):
                try:
                    import ast
                    ast.parse(updated)
                except SyntaxError as e:
                    error_msg = (
                        f"edit：已中止，因为修改后的代码存在无效的 Python 语法。\n"
                        f"SyntaxError: {e}\n\n"
                        f"第 {e.lineno} 行：{e.text}"
                    )
                    if e.offset is not None and e.text is not None:
                        error_msg += f"{' ' * (e.offset - 1)}^\n"
                    error_msg += "\n请检查缩进与语法后重试。"
                    return error_result(call_id, error_msg)

            w_result = await runtime.write_text(
                str(full_path),
                updated,
                template_id=_wk,
                workspace_id=context.workspace_id,
                workspace_root=_root,
            )
            if not w_result.success:
                return error_result(call_id, f"edit：写入文件失败：{w_result.error}")

            change = build_file_change(
                raw_path, content, updated,
                before_ref="", before_sha=before_sha,
            )
            ui_artifact = (
                UiArtifact(
                    artifact_type="file_changes",
                    file_changes=[FileChangeEntry.model_validate(change)],
                )
                if (change["insertions"] or change["deletions"])
                else None
            )
            if ui_artifact is not None:
                conversation_id = context.parent_session_id or context.session_id
                before_content = content.encode("utf-8") if content else b""
                after_content = updated.encode("utf-8") if updated else b""
                registry = get_review_registry(context.workspace_path)
                await asyncio.to_thread(
                    registry.register_edit,
                    conversation_id, raw_path, before_content, after_content,
                    context.session_id, call_id,
                )

            # Compute new read_epoch and update observation
            new_read_epoch = _sha256hex(updated)
            if hasattr(context, "read_file_state") and context.read_file_state is not None:
                if isinstance(context.read_file_state, dict):
                    context.read_file_state[raw_path] = {
                        "read_epoch": new_read_epoch,
                        "line_count": updated.count("\n") + 1,
                    }

            result_text = f"已编辑 {raw_path}（{count if replace_all else 1} 处替换）"
            # Remind to use read_epoch when it was not provided
            if not isinstance(claimed_epoch, str) or not claimed_epoch:
                result_text += "\n[提示：可传入 read 工具结果中的 read_epoch，以避免并发编辑冲突]"

            return text_result(
                call_id,
                result_text,
                ui_artifact=ui_artifact,
                metadata={
                    "file_path": raw_path,
                    "replacements": count if replace_all else 1,
                    "read_epoch": new_read_epoch,
                },
            )

        return await with_file_mutation_queue(str(full_path), _do_edit)
