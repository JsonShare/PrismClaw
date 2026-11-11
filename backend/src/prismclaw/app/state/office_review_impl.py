"""OfficeReviewProtocol implementation — delegates to engine layer.

This class only exists in the wiring layer (composition root).
delivery and engine both remain unaware of it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class _OfficeReviewImpl:
    """OfficeReviewProtocol implementation — delegates to engine layer."""

    def fmt_from_path(self, path: str) -> str | None:
        from prismclaw.runtime.facade.review import structured_office_fmt_from_path
        return structured_office_fmt_from_path(path)

    def apply_office_edit(
        self,
        fmt: str,
        workspace_path: str,
        path: str,
        conversation_id: str,
        current_bytes: bytes,
        rejected: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from prismclaw.runtime.facade.review import ReviewIntent
        from prismclaw.runtime.facade.review import dispatch
        change = dispatch(fmt)
        intent = ReviewIntent(
            workspace_path=Path(workspace_path),
            path=path,
            conversation_id=conversation_id,
            current_bytes=current_bytes,
            rejected=rejected,
        )
        return change.reject(intent)

    def build_docx_artifact(
        self,
        workspace_path: str,
        session_id: str,
        path: str,
        before_ref: str,
        operations: list[dict[str, Any]],
        author: str | None = None,
    ) -> dict[str, Any]:
        from prismclaw.runtime.facade.review import DocxChange
        change = DocxChange()
        return change.build_artifact(
            workspace_path=Path(workspace_path),
            session_id=session_id,
            path=path,
            before_ref=before_ref,
            operations=operations,
            author=author,
        )

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
        from prismclaw.runtime.facade.review import DocxChange
        change = DocxChange()
        return change.save_reviewed(
            workspace_path=Path(workspace_path),
            session_id=session_id,
            path=path,
            review_docx_ref=review_docx_ref,
            before_ref=before_ref,
            before_sha=before_sha,
            content_base64=content_base64,
        )

    def revert_file(
        self,
        workspace_root: str,
        session_id: str,
        path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        from prismclaw.runtime.facade.review import get_review_registry
        registry = get_review_registry(Path(workspace_root))
        return registry.reject_path(session_id, path, force=force)

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
        from prismclaw.runtime.facade.review import get_review_registry
        registry = get_review_registry(Path(workspace_root))
        registry.register_edit(
            session_id, path, before_content, after_content,
            session_id, tool_call_id, is_binary=is_binary,
        )
