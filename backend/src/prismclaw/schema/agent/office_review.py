"""Office review operations protocol — consumed by delivery layer.

Pi 先例：Pi 按 consumer 定义接口（FileSystem/Shell）。
OfficeReviewProtocol 是 PrismClaw 独有的——Pi 没有 web review 子系统。
但"按 consumer 定义接口"这个原则是抄 Pi 的。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OfficeReviewProtocol(Protocol):
    """delivery 层通过此 Protocol 执行 office review 操作。

    实现方在 engine 层，通过 web_deps.office_review 注入。
    delivery 不知道实现细节，只知道方法签名。
    """

    def fmt_from_path(self, path: str) -> str | None:
        """Detect office file format from path.

        Returns "xlsx", "pptx", "docx", or None.
        Corresponds to: structured_office_fmt_from_path(...)
        """
        ...

    def apply_office_edit(
        self,
        fmt: str,
        workspace_path: str,
        path: str,
        conversation_id: str,
        current_bytes: bytes,
        rejected: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply an office edit/reject operation.

        Corresponds to: dispatch(fmt) → ReviewIntent(...) → change.reject(intent)
        Returns the result dict from change.reject().
        """
        ...

    def build_docx_artifact(
        self,
        workspace_path: str,
        session_id: str,
        path: str,
        before_ref: str,
        operations: list[dict[str, Any]],
        author: str | None = None,
    ) -> dict[str, Any]:
        """Build a docx review artifact from tracked-changes operations.

        Corresponds to: DocxChange().build_artifact(...)
        """
        ...

    def save_docx_reviewed(
        self,
        workspace_path: str,
        session_id: str,
        path: str,
        review_docx_ref: str,
        before_ref: str,
        before_sha: str,
        content_base64: str,
    ) -> dict[str, Any]:
        """Save a reviewed docx file back to workspace.

        Corresponds to: DocxChange().save_reviewed(...)
        """
        ...

    def revert_file(
        self,
        workspace_root: str,
        session_id: str,
        path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Revert a file to its pre-review state.

        Corresponds to: get_review_registry(workspace_root).reject_path(...)
        """
        ...

    def register_edit(
        self,
        workspace_root: str,
        session_id: str,
        path: str,
        before_content: bytes | None,
        after_content: bytes,
        tool_call_id: str,
        is_binary: bool = False,
    ) -> None:
        """Register a manual edit in the review registry.

        Corresponds to: get_review_registry(workspace_root).register_edit(...)
        """
        ...
