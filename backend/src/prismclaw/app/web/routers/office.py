"""Office/review-related API routes (extracted from routes.py)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web._routes_helpers import (
    _candidate_file_targets,
    _clean_workspace_id,
    _content_disposition_inline,
    _get_store,
    _resolve_workspace_root,
)
from prismclaw.app.web.routers.office_review import (
    count_marks,
    synthesize_review_doc,
)
from prismclaw.schema.agent.review_errors import (
    DocxArtifactMismatch,
    DocxReviewEngineGap,
    DocxSourceDrift,
    IntegrityError,
    OfficeBlobNotFound,
    OfficeEditError,
    OfficeRevertConflict,
    ReconcileConflict,
    ReviewSetNotFound,
    UnsupportedDocxChange,
    UnsupportedDocxGeometry,
)
from prismclaw.schema.agent.office_review import OfficeReviewProtocol
from prismclaw.app.web.deps import get_web_deps
from prismclaw.schema.session.types import SessionTree

logger = logging.getLogger(__name__)


def _get_office_review(request: Request) -> OfficeReviewProtocol:
    from prismclaw.app.web.deps import get_web_deps
    return get_web_deps(request).office_review


office_router = APIRouter(tags=["office"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_and_verify(session_id: str, user_id: str) -> SessionTree:
    store = _get_store()
    tree = await store.load(session_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not tree.header.session_key.startswith(f"web:{user_id}"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tree


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FileRevertRequest(BaseModel):
    path: str
    force: bool = False    # override a drift conflict (out-of-band edit detected)


class OfficeApplyRequest(BaseModel):
    """Reverse *every* tracked change of an ``.xlsx``/``.pptx`` file.

    Triggered by the frontend 'Reject All' button. Rejected changes are reversed
    locally (``revert_office_ops``) on the live workspace file; because we never
    replay from a frozen backup blob, edits made in later rounds are never destroyed.
    The per-change Accept/Reject UI was removed, so this endpoint only ever rejects all.
    """
    path: str
    details: list[dict[str, Any]] = []


class OfficeReviewHtmlRequest(BaseModel):
    path: str
    workspace_id: str | None = None
    before_ref: str | None = None
    details: list[dict[str, Any]] = []


class BuildDocxReviewArtifactRequest(BaseModel):
    path: str
    before_ref: str
    operations: list[dict[str, Any]] = []
    author: str | None = None


class SaveDocxReviewedRequest(BaseModel):
    path: str
    review_docx_ref: str
    before_ref: str
    before_sha: str
    content_base64: str


class FileWriteContentRequest(BaseModel):
    """Frontend manual-edit save: write arbitrary text content to a workspace file.

    The endpoint mirrors the backend WriteTool flow:
    1. Read the current file content (if file exists) and save a backup blob.
    2. Write the new content to the workspace file.
    3. Build a ``file_change`` diff payload (same shape as WriteTool metadata).
    4. Record a ``FileEditRecord`` in the tracking log (enables future rollback).
    """
    path: str
    content: str   # new file content (UTF-8 text only; binary files are rejected)


# ---------------------------------------------------------------------------
# NOTE: office review/apply no longer shell out to the deleted `.prismclaw/plugins/office`
# MCP server. Pure-Python implementations live in `prismclaw.channels.web.routers.office_review`.
# ---------------------------------------------------------------------------


@office_router.post("/sessions/{session_id}/file-office-apply")
async def apply_office_edit(
    request: Request,
    session_id: str,
    body: OfficeApplyRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Reverse all tracked changes of the current ``.xlsx``/``.pptx`` file (Reject All).

    Delegates to the unified ``ReviewableChange`` contract via ``dispatch`` — no
    ``if fmt ==`` branching lives here. Rejected changes are reversed locally on
    the live workspace file; edits made in later rounds are preserved. Drift
    (a later edit touched a rejected location) fails loud with 409.
    """
    tree = await _load_and_verify(session_id, user_id)
    workspace_root = _resolve_workspace_root(
        request=request, user_id=user_id, workspace_id=tree.header.workspace_id,
    )

    # Guard against path traversal (path must stay inside the workspace)
    full_path = (workspace_root / body.path).resolve()
    try:
        full_path.relative_to(workspace_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path: outside workspace root")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="office file not found in workspace")

    office = _get_office_review(request)
    fmt = office.fmt_from_path(body.path)
    if fmt is None:
        raise HTTPException(status_code=400, detail="only .xlsx/.pptx files are supported")

    # The review UI only offers "Reject All" now, so reverse every tracked change.
    try:
        result = office.apply_office_edit(
            fmt=fmt,
            workspace_path=str(workspace_root),
            path=body.path,
            conversation_id=session_id,
            current_bytes=full_path.read_bytes(),
            rejected=body.details,
        )
    except OfficeRevertConflict as exc:
        # A rejected change collided with a later edit; reversing it would
        # destroy the newer edit. 409 → the frontend asks the user to re-review.
        # Structured drift signal: can_force is False for office (no force path).
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OFFICE_DRIFTED",
                "message": str(exc),
                "can_force": False,
            },
        )
    except OfficeEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"office apply failed: {exc}")

    return result


@office_router.post("/sessions/{session_id}/file-docx-review-artifact")
async def build_docx_native_review_artifact(
    request: Request,
    session_id: str,
    body: BuildDocxReviewArtifactRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Build a native tracked-changes `.docx` artifact from suggested edits."""
    tree = await _load_and_verify(session_id, user_id)
    workspace_root = _resolve_workspace_root(
        request=request, user_id=user_id, workspace_id=tree.header.workspace_id,
    )
    office = _get_office_review(request)
    if office.fmt_from_path(body.path) != "docx":
        raise HTTPException(status_code=400, detail="not a docx file")

    office = _get_office_review(request)
    try:
        return office.build_docx_artifact(
            workspace_path=str(workspace_root),
            session_id=session_id,
            path=body.path,
            before_ref=body.before_ref,
            operations=body.operations,
            author=body.author,
        )
    except UnsupportedDocxChange as exc:
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc}") from exc
    except UnsupportedDocxGeometry as exc:
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc}") from exc
    except DocxReviewEngineGap as exc:
        raise HTTPException(status_code=500, detail=f"{exc.code}: {exc}") from exc
    except OfficeBlobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except OfficeEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@office_router.get("/sessions/{session_id}/file-docx-review-artifact/raw")
async def get_docx_native_review_artifact_raw(
    request: Request,
    session_id: str,
    ref: str,
    name: str | None = None,
    user_id: str = Depends(get_current_user),
) -> Response:
    """Stream a generated native `.docx` review artifact by blob ref."""
    tree = await _load_and_verify(session_id, user_id)
    workspace_root = _resolve_workspace_root(
        request=request,
        user_id=user_id,
        workspace_id=tree.header.workspace_id,
    )
    store = get_web_deps(request).upload_fns["get_tracking_store"](workspace_root)
    artifact = store.get_docx_review_artifact(session_id=session_id, review_docx_ref=ref)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"DOCX review artifact not found: {ref}")

    payload = store.read_blob_bytes(ref)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"DOCX review artifact blob missing: {ref}")

    filename = name or Path(artifact.path).name
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _content_disposition_inline(filename)},
    )


@office_router.post("/sessions/{session_id}/file-docx-save-reviewed")
async def save_docx_native_reviewed(
    request: Request,
    session_id: str,
    body: SaveDocxReviewedRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Persist a reviewed `.docx` artifact back to the workspace file."""
    tree = await _load_and_verify(session_id, user_id)
    workspace_root = _resolve_workspace_root(
        request=request,
        user_id=user_id,
        workspace_id=tree.header.workspace_id,
    )
    office = _get_office_review(request)
    if office.fmt_from_path(body.path) != "docx":
        raise HTTPException(status_code=400, detail="not a docx file")

    office = _get_office_review(request)
    try:
        return office.save_docx_reviewed(
            workspace_path=str(workspace_root),
            session_id=session_id,
            path=body.path,
            review_docx_ref=body.review_docx_ref,
            before_ref=body.before_ref,
            before_sha=body.before_sha,
            content_base64=body.content_base64,
        )
    except OfficeBlobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (DocxArtifactMismatch, DocxSourceDrift) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except OfficeEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@office_router.post("/sessions/{session_id}/office-review-html")
async def office_review_html(
    request: Request,
    session_id: str,
    body: OfficeReviewHtmlRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Synthesize the track-changes (review) document for an edited office file.

    Returns a TipTap JSON document whose ``insertion``/``deletion`` marks (each carrying
    a ``changeId`` that equals the ``officeDetail`` index) make deletions/replacements
    visible inline in the frontend tiptap-track-changes editor. This legacy path now
    serves only ``.xlsx`` / ``.pptx`` reviews. Built from the before-blob (``before_ref``)
    + the edit ``details``; when those are absent it falls back to a plain render of the
    current/before file. Implemented in-process by
    :mod:`prismclaw.channels.web.routers.office_review` (no shell-out).
    """
    tree = await _load_and_verify(session_id, user_id)
    resolved_ws = body.workspace_id or tree.header.workspace_id
    workspace_root = _resolve_workspace_root(
        request=request, user_id=user_id, workspace_id=resolved_ws,
    )
    office = _get_office_review(request)
    fmt = office.fmt_from_path(body.path)
    if fmt is None:
        raise HTTPException(status_code=400, detail="only .xlsx/.pptx files are supported")

    store = get_web_deps(request).upload_fns["get_tracking_store"](workspace_root)
    before_bytes = store.read_blob_bytes(body.before_ref) if body.before_ref else None

    full_path = (workspace_root / body.path).resolve()
    try:
        full_path.relative_to(workspace_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path: outside workspace root")

    current_bytes = full_path.read_bytes() if full_path.exists() else None

    doc = synthesize_review_doc(before_bytes, current_bytes, fmt, body.details)
    inserted, deleted = count_marks(doc)
    return {
        "ok": True,
        "doc": doc,
        "inserted_count": inserted,
        "deleted_count": deleted,
        "review": True,
    }


@office_router.post("/sessions/{session_id}/file-revert")
async def revert_file(
    request: Request,
    session_id: str,
    body: FileRevertRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Reject a file: return it to its agent-edit *baseline* (the pre-edit state).

    Driven by the conversation ReviewSet, not by a single latest edit record. This
    is what makes "Reject All" mean "discard the agent's changes" rather than
    "undo the last step". A newly created file (baseline was empty) is deleted.

    If the live file was edited out-of-band since the revision was recorded, the
    revert is refused with 409 unless ``force`` is set — we never blindly overwrite
    a file we no longer recognise.
    """
    tree = await _load_and_verify(session_id, user_id)
    workspace_id = tree.header.workspace_id

    # Resolve workspace_path using the same pattern as other endpoints
    workspace_root = _resolve_workspace_root(
        request=request, user_id=user_id, workspace_id=workspace_id,
    )
    # ``session_id`` here is the conversation the user reviews from. Sub-agent
    # edits were registered under the same conversation via parent_session_id.
    office = _get_office_review(request)
    try:
        result = office.revert_file(
            workspace_root=str(workspace_root),
            session_id=session_id,
            path=body.path,
            force=body.force,
        )
    except ReconcileConflict as exc:
        # Structured drift signal: the frontend renders a "force revert" action
        # when can_force is True (text files). Office drift returns can_force
        # False — reversing it would destroy later edits, so the user re-reviews.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FILE_DRIFTED",
                "message": f"file drifted before review: {exc.path}. Re-fetch and retry, or force=true to override.",
                "can_force": True,
            },
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"backup integrity check failed: {exc}",
        )
    except ReviewSetNotFound:
        raise HTTPException(status_code=404, detail=f"No review record found for {body.path}")

    logger.info("file-revert.success", extra={"session_id": session_id, "path": body.path})
    return result


@office_router.post("/sessions/{session_id}/file-write-content")
async def write_file_content(
    request: Request,
    session_id: str,
    body: FileWriteContentRequest,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> dict:
    """Save manually edited file content from the frontend Review/Edit mode.

    This is the frontend counterpart of the backend WriteTool — it allows the
    user to directly modify a file's content and save it, with full backup
    tracking so the change can later be rolled back via file-revert.
    """
    # 校验会话有效（仍保留 session 校验），但根目录解析与预览 get_file_raw 完全一致：
    # 使用前端传入的 workspace_id（而非从 session 推导），保证预览/保存指向同一工作区。
    await _load_and_verify(session_id, user_id)
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(
        request=request, user_id=user_id, workspace_id=wid,
    )
    office = _get_office_review(request)

    # 与 get_file_raw 完全相同的目标解析逻辑，确保预览与保存指向同一文件
    targets = _candidate_file_targets(
        io_root=workspace_root, workspace_root=workspace_root, raw_path=body.path,
    )
    if not targets:
        raise HTTPException(status_code=400, detail="Invalid path")
    full_path = targets[0]

    # Guard: reject binary / office files (they need structured office:edit, not raw text)
    _OFFICE_EXTS = {".docx", ".xlsx", ".pptx"}
    _BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz", ".bz2"}
    suffix = full_path.suffix.lower()
    if suffix in _OFFICE_EXTS:
        raise HTTPException(status_code=400, detail="Office files (.docx/.xlsx/.pptx) cannot be edited as raw text; use office:edit instead")
    if suffix in _BINARY_EXTS:
        raise HTTPException(status_code=400, detail="Binary files cannot be edited as raw text")

    # Read old content (may not exist for newly created files)
    old_text = ""
    file_existed = full_path.exists()
    if file_existed:
        try:
            old_text = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Existing file is not UTF-8 text; cannot edit as text")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read existing file: {exc}")

    # Write new content
    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full_path.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}")

    before_sha = hashlib.sha256(old_text.encode("utf-8")).hexdigest()
    # Build file_change diff payload (same shape as WriteTool metadata)
    change = get_web_deps(request).upload_fns["build_file_change"](
        body.path, old_text, body.content,
        before_ref="", before_sha=before_sha,
    )

    # Register the edit into the conversation ReviewSet (baseline + head),
    # replacing the old ChangeTrackingStore FileEditRecord path.
    before_content: bytes | None = old_text.encode("utf-8") if old_text else (None if not file_existed else b"")
    after_content = body.content.encode("utf-8")
    office.register_edit(
        workspace_root=str(workspace_root),
        session_id=session_id,
        path=body.path,
        before_content=before_content,
        after_content=after_content,
        tool_call_id="manual_edit",
    )

    logger.info("file-write-content.success", extra={
        "session_id": session_id,
        "path": body.path,
    })

    return {
        "status": "ok",
        "path": body.path,
        "file_change": change,
        "before_ref": "",
        "before_sha": before_sha,
    }
