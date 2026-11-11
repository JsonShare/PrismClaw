"""DOCX native review flow, wrapped as a ReviewableChange.

The workspace ``.docx`` is never mutated during review: the agent's suggestions
become a separate tracked-changes artifact. ``build_artifact`` generates it;
``save_reviewed`` persists the user-confirmed version back to the workspace.
The unified ``reject``/``accept`` contract maps to discard / explicit-save.
"""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from prismclaw.engine.review.changes.intent import ReviewIntent
from prismclaw.schema.agent.review_errors import (
    DocxArtifactMismatch,
    DocxSourceDrift,
    OfficeBlobNotFound,
    OfficeEditError,
)
from prismclaw.engine.toolkit.office.docx_review_native import build_docx_review_artifact
from prismclaw.engine.toolkit.file.tracking import (
    DocxReviewArtifactRecord,
    FileEditRecord,
    get_tracking_store,
)



def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DocxChange:
    kind = "docx"

    # -- unified contract (docx never mutates the file during review) --------
    def reject(self, intent: ReviewIntent) -> dict[str, Any]:
        # docx reject is not a file mutation — the native review artifact is
        # discarded simply by not saving it. Fail loud so callers use the
        # correct docx endpoints instead of expecting a file revert.
        raise OfficeEditError(
            "docx 的 reject 不是文件改动操作；原生审查产物只需不保存即被丢弃。"
            "如需接受改动，请使用 /file-docx-save-reviewed。"
        )

    def accept(self, intent: ReviewIntent) -> dict[str, Any]:
        # docx accept is an explicit save of the reviewed payload (save_reviewed).
        raise OfficeEditError(
            "docx 的 accept 需要已审查的载荷；请通过 /file-docx-save-reviewed "
            "调用 save_reviewed()"
        )

    # -- native flow ----------------------------------------------------------
    def build_artifact(
        self,
        *,
        workspace_path: Path,
        session_id: str,
        path: str,
        before_ref: str,
        operations: list[dict[str, Any]],
        author: str | None = None,
    ) -> dict[str, Any]:
        store = get_tracking_store(workspace_path)
        before_bytes = store.read_blob_bytes(before_ref)
        if before_bytes is None:
            raise OfficeBlobNotFound(f"Backup blob not found: {before_ref}")
        # docx-specific unsupported-change / geometry / engine-gap errors
        # propagate untouched to the caller for HTTP mapping.
        result = build_docx_review_artifact(
            before_bytes, operations, author=author or "PrismClaw Agent"
        )
        review_docx_ref = store.save_blob_bytes(result.review_docx_bytes)
        artifact_record = DocxReviewArtifactRecord(
            artifact_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            path=path,
            before_ref=before_ref,
            review_docx_ref=review_docx_ref,
            review_docx_sha=result.review_docx_sha,
            change_count=result.pending_changes,
            ts=time.time(),
        )
        store.record_docx_review_artifact(artifact_record)
        file_change = {
            "path": path,
            "name": Path(path).name,
            "kind": "docx",
            "insertions": result.insertions,
            "deletions": result.deletions,
            "hunks": [],
            "truncated": False,
            "before_ref": before_ref,
            "before_sha": _sha(before_bytes),
            "note": result.note,
            "review_mode": "docx-native",
            "review_docx_ref": review_docx_ref,
            "review_docx_sha": result.review_docx_sha,
            "pending_changes": result.pending_changes,
        }
        return {
            "ok": True,
            "path": path,
            "review_mode": "docx-native",
            "review_docx_ref": review_docx_ref,
            "review_docx_sha": result.review_docx_sha,
            "pending_changes": result.pending_changes,
            "file_change": file_change,
        }

    def save_reviewed(
        self,
        *,
        workspace_path: Path,
        session_id: str,
        path: str,
        review_docx_ref: str,
        before_ref: str,
        before_sha: str,
        content_base64: str,
    ) -> dict[str, Any]:
        store = get_tracking_store(workspace_path)
        artifact = store.get_docx_review_artifact(
            session_id=session_id, review_docx_ref=review_docx_ref
        )
        if artifact is None:
            raise OfficeBlobNotFound(f"DOCX review artifact not found: {review_docx_ref}")
        if artifact.path != path or artifact.before_ref != before_ref:
            raise DocxArtifactMismatch(
                "DOCX 审查产物已与当前审查会话不一致"
            )
        full = (workspace_path / path).resolve()
        try:
            full.relative_to(workspace_path.resolve())
        except ValueError:
            raise OfficeEditError("无效路径：超出工作区根目录")
        if not full.exists():
            raise OfficeEditError("工作区中未找到 docx 文件")
        current_bytes = full.read_bytes()
        current_sha = _sha(current_bytes)
        if current_sha != before_sha:
            raise DocxSourceDrift(
                "DOCX 源文件在审查产物生成之后已发生变更；"
                "请重新打开审查后再试"
            )
        try:
            reviewed_bytes = base64.b64decode(content_base64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise OfficeEditError(f"已审查的 docx 载荷无效：{exc}") from exc
        if not reviewed_bytes:
            raise OfficeEditError("已审查的 docx 载荷为空")
        full.write_bytes(reviewed_bytes)
        before_ref_new = store.save_blob_bytes(current_bytes)
        after_ref = store.save_blob_bytes(reviewed_bytes)
        after_sha = _sha(reviewed_bytes)
        record_id = uuid.uuid4().hex[:12]
        rec = FileEditRecord(
            record_id=record_id,
            session_id=session_id,
            tool_call_id="docx:native-save",
            path=path,
            before_sha=current_sha,
            after_sha=after_sha,
            before_ref=before_ref_new,
            after_ref=after_ref,
            ts=time.time(),
            binary=True,
        )
        store.record(rec)
        file_change = {
            "path": path,
            "name": Path(path).name,
            "kind": "docx",
            "insertions": 0,
            "deletions": 0,
            "hunks": [],
            "truncated": False,
            "before_ref": before_ref_new,
            "before_sha": current_sha,
            "note": "confirmed native docx review",
        }
        return {
            "status": "ok",
            "path": path,
            "record_id": record_id,
            "before_sha": current_sha,
            "after_sha": after_sha,
            "file_change": file_change,
        }
