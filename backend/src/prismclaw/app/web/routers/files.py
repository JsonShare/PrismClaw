"""Files-related API routes (extracted from routes.py)."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from prismclaw.app.auth.auth import verify_jwt
from prismclaw.app.web.deps import get_web_deps
from prismclaw.app.web._routes_helpers import (
    _candidate_file_targets,
    _clean_workspace_id,
    _csp_for_mime,
    _content_disposition_attachment,
    _content_disposition_inline,
    _raw_text_media_type,
    _resolve_workspace_root,
    _resolve_within_root,
)
from prismclaw.app.auth.auth import get_current_user

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FileEntry(BaseModel):
    name: str
    path: str
    kind: str
    size_bytes: int | None = None
    mtime_ms: int | None = None


class ListFilesResponse(BaseModel):
    workspace_id: str
    path: str
    entries: list[FileEntry]
    offset: int = 0
    limit: int = 0
    has_more: bool = False


class FileTextResponse(BaseModel):
    path: str
    text: str
    truncated: bool = False


class SimpleOkResponse(BaseModel):
    ok: bool = True


class MkdirRequest(BaseModel):
    path: str


class WriteTextRequest(BaseModel):
    path: str
    content: str
    overwrite: bool = True


class DeleteRequest(BaseModel):
    path: str
    recursive: bool = False


class MoveRequest(BaseModel):
    src: str
    dst: str
    overwrite: bool = False


class SearchMatch(BaseModel):
    path: str
    kind: str


class SearchResponse(BaseModel):
    workspace_id: str
    query: str
    matches: list[SearchMatch]
    truncated: bool = False


class UploadItem(BaseModel):
    original_name: str
    path: str
    size_bytes: int
    mime_type: str | None = None


class SkippedItem(BaseModel):
    """A file the upload pipeline rejected, with a machine-readable ``code``.

    Codes (single source of truth in ``upload_paths`` / ``upload_pipeline``):
      ``blocked_type``    — extension is in the executable blocklist
      ``noise``           — OS metadata artifact (``.DS_Store`` / ``__MACOSX`` / …)
      ``disk_full``       — workspace cannot hold the file (sink pre-check)
      ``invalid_path``    — sanitized path escapes the destination root
      ``transfer_failed`` — chunk RPC failure / decode error / unexpected error
    """

    name: str
    code: str


class UploadResponse(BaseModel):
    files: list[UploadItem]
    skipped: list[SkippedItem] = []
    session_id: str | None = None  # 自动生成时返回（无 session_id 上传场景）


# ---------------------------------------------------------------------------
# Helper: extract user_id from request for download fallback
# ---------------------------------------------------------------------------

def _get_user_from_request(request: Request) -> str:
    """从 Authorization Bearer 或 _token 查询参数中提取 user_id（下载 fallback 兼容）。"""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        settings = get_web_deps(request).web_settings
        user_id = verify_jwt(token, settings.jwt_secret)
        if user_id is not None:
            return user_id

    raw_token = request.query_params.get("_token", "")
    if raw_token:
        settings = get_web_deps(request).web_settings
        user_id = verify_jwt(raw_token, settings.jwt_secret)
        if user_id is not None:
            return user_id

    raise HTTPException(status_code=401, detail="Missing or invalid token")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

files_router = APIRouter(prefix="/files", tags=["files"])


@files_router.get("/list", response_model=ListFilesResponse)
async def list_files(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    path: str = "",
    kind: str = "all",
    offset: int = 0,
    limit: int = 500,
    query: str | None = None,
) -> ListFilesResponse:
    wid = _clean_workspace_id(workspace_id)
    rel = (path or "").lstrip("/")
    if limit <= 0:
        limit = 500
    if limit > 5000:
        limit = 5000
    if offset < 0:
        offset = 0

    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    target = _resolve_within_root(workspace_root, rel)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    only_dirs = kind == "dirs"
    only_files = kind == "files"
    q = query.strip().lower() if isinstance(query, str) and query.strip() else None

    entries_all: list[FileEntry] = []
    try:
        for p in target.iterdir():
            name = p.name
            if name in (".git", ".DS_Store", ".work"):
                continue
            if q and q not in name.lower():
                continue
            if only_dirs and not p.is_dir():
                continue
            if only_files and not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                st = None
            rel_path = str(p.relative_to(workspace_root.expanduser().resolve()))
            if p.is_dir():
                entries_all.append(
                    FileEntry(
                        name=name,
                        path=rel_path,
                        kind="dir",
                        mtime_ms=int(st.st_mtime * 1000) if st is not None else None,
                    )
                )
            elif p.is_file():
                entries_all.append(
                    FileEntry(
                        name=name,
                        path=rel_path,
                        kind="file",
                        size_bytes=int(st.st_size) if st is not None else None,
                        mtime_ms=int(st.st_mtime * 1000) if st is not None else None,
                    )
                )
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to list directory")

    entries_all.sort(key=lambda e: (e.kind != "dir", (e.name or "").lower()))
    page = entries_all[offset : offset + limit]
    has_more = offset + limit < len(entries_all)
    return ListFilesResponse(
        workspace_id=wid,
        path=rel,
        entries=page,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


@files_router.get("/text", response_model=FileTextResponse)
async def get_file_text(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    path: str = "",
) -> FileTextResponse:
    wid = _clean_workspace_id(workspace_id)
    rel = (path or "").lstrip("/")
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    targets = _candidate_file_targets(io_root=workspace_root, workspace_root=workspace_root, raw_path=path)
    target = next((t for t in targets if t.exists() and t.is_file()), None)
    if target is None:
        tried = "; ".join(str(t) for t in targets) or "(no candidates)"
        raise HTTPException(status_code=404, detail=f"File not found (workspace_root={workspace_root}; tried: {tried})")

    max_bytes = 200_000
    try:
        data = target.read_bytes()
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to read file")

    truncated = False
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="Not a UTF-8 text file")

    return FileTextResponse(path=rel, text=text, truncated=truncated)


@files_router.get("/raw")
async def get_file_raw(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    path: str = "",
) -> Response:
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    targets = _candidate_file_targets(io_root=workspace_root, workspace_root=workspace_root, raw_path=path)
    target = next((t for t in targets if t.exists() and t.is_file()), None)
    if target is None:
        tried = "; ".join(str(t) for t in targets) or "(no candidates)"
        raise HTTPException(status_code=404, detail=f"File not found (workspace_root={workspace_root}; tried: {tried})")

    try:
        size = target.stat().st_size
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to stat file")
    if size > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large to preview")

    media_type = _raw_text_media_type(str(target))
    csp = _csp_for_mime(media_type)
    headers = {"Content-Disposition": _content_disposition_inline(target.name)}
    if csp:
        headers["Content-Security-Policy"] = csp
    return FileResponse(
        path=str(target),
        media_type=media_type or "application/octet-stream",
        headers=headers,
    )


@files_router.get("/download")
async def download_file(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    path: str = "",
) -> Response:
    """Download a file with Content-Disposition: attachment."""
    wid = _clean_workspace_id(workspace_id)
    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    targets = _candidate_file_targets(io_root=workspace_root, workspace_root=workspace_root, raw_path=path)
    target = next((t for t in targets if t.exists() and t.is_file()), None)
    if target is None:
        tried = "; ".join(str(t) for t in targets) or "(no candidates)"
        raise HTTPException(status_code=404, detail=f"File not found (workspace_root={workspace_root}; tried: {tried})")

    media_type = _raw_text_media_type(str(target))
    return FileResponse(
        path=str(target),
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": _content_disposition_attachment(target.name)},
    )


@files_router.get("/office_preview")
async def get_office_preview_pdf(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    path: str = "",
) -> Response:
    wid = _clean_workspace_id(workspace_id)
    rel = (path or "").lstrip("/")
    ext = Path(rel).suffix.lower()
    if ext not in {".xlsx", ".xls", ".pptx", ".ppt", ".docx", ".doc"}:
        raise HTTPException(status_code=415, detail="Not a supported office file")

    workspace_root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    targets = _candidate_file_targets(io_root=workspace_root, workspace_root=workspace_root, raw_path=path)
    target = next((t for t in targets if t.exists() and t.is_file()), None)
    if target is None:
        tried = "; ".join(str(t) for t in targets) or "(no candidates)"
        raise HTTPException(status_code=404, detail=f"File not found (workspace_root={workspace_root}; tried: {tried})")

    try:
        st = target.stat()
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to stat file")
    if st.st_size > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large to preview")

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise HTTPException(status_code=501, detail="LibreOffice is not installed (soffice not found)")

    preview_dir = workspace_root / ".work" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    key_src = f"{target.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")
    key = hashlib.sha256(key_src).hexdigest()
    out_pdf = preview_dir / f"{key}.pdf"

    if not out_pdf.exists():
        with tempfile.TemporaryDirectory(prefix="office_preview_", dir=str(preview_dir)) as tmp:
            tmp_dir = Path(tmp)
            cmd = [
                soffice,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_dir),
                str(target),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=504, detail="Office preview timed out")
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                raise HTTPException(status_code=500, detail=err or "Office preview conversion failed")

            generated = tmp_dir / f"{target.stem}.pdf"
            if not generated.exists():
                alt = next(tmp_dir.glob("*.pdf"), None)
                if alt is None:
                    raise HTTPException(status_code=500, detail="Office preview output not found")
                generated = alt
            try:
                shutil.move(str(generated), str(out_pdf))
            except OSError:
                raise HTTPException(status_code=500, detail="Failed to store preview")

    return FileResponse(
        path=str(out_pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition_inline(f"{target.stem}.pdf")},
    )


@files_router.post("/upload", response_model=UploadResponse)
async def upload_to_directory(
    request: Request,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    dir: str = "",
    session_id: str | None = None,
) -> UploadResponse:
    """Upload a batch of files into a workspace directory.

    三种落盘模式（按优先级）：
    1. 有 session_id → session 私有上传（sessions/<sid>/uploads/），chat attach 场景
    2. 无 session_id 但有 dir → workspace 级共享输入（inputs/<dir>/）
    3. 无 session_id 无 dir → 自动创建 session_id（返回给前端）+ session 私有上传

    目录不存在时自动 mkdir(parents=True)，upload_files 管线零改动。
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    import uuid as _uuid
    from prismclaw.infra.workspace_paths import resolve_session_uploads_dir
    from prismclaw.schema.session.ids import build_web_session_key

    wid = _clean_workspace_id(workspace_id)
    rel_dir = (dir or "").lstrip("/")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)

    auto_session_id: str | None = None
    if session_id:
        # 1. session 私有上传（resolve_session_uploads_dir 内部做 sanitize）
        dest_root = resolve_session_uploads_dir(user_id, wid, session_id)
    elif rel_dir:
        # 2. workspace 级共享输入
        dest_root = root / "inputs"
    else:
        # 3. 自动创建 session_id —— 新任务先上传文件再咨询的场景
        session_id = f"{build_web_session_key(user_id, wid)}:s:{_uuid.uuid4().hex}"
        auto_session_id = session_id
        dest_root = resolve_session_uploads_dir(user_id, wid, session_id)

    # 确保目录存在（LocalSink.begin 只 mkdir dest_root/<rel> 子路径，不负责 session_root）
    dest_root.mkdir(parents=True, exist_ok=True)

    _upload = get_web_deps(request).upload_fns
    # LocalSink(workspace_root, dest_root) —— commit 返回 workspace 根相对路径
    sink = _upload["LocalSink"](root, dest_root)
    uploaded, skipped = await _upload["upload_files"](files, sink)
    return UploadResponse(
        files=[
            UploadItem(
                original_name=u["original_name"],
                path=u["path"],
                size_bytes=u["size"],
                mime_type=u.get("mime_type"),
            )
            for u in uploaded
        ],
        skipped=[SkippedItem(name=s["name"], code=s["code"]) for s in skipped],
        session_id=auto_session_id,
    )


@files_router.post("/mkdir", response_model=SimpleOkResponse)
async def mkdir(
    payload: MkdirRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> SimpleOkResponse:
    wid = _clean_workspace_id(workspace_id)
    rel = (payload.path or "").lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="path required")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    target = _resolve_within_root(root, rel)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to create directory")
    return SimpleOkResponse()


@files_router.post("/write_text", response_model=SimpleOkResponse)
async def write_text(
    payload: WriteTextRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> SimpleOkResponse:
    wid = _clean_workspace_id(workspace_id)
    rel = (payload.path or "").lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="path required")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    target = _resolve_within_root(root, rel)
    if target.exists() and not payload.overwrite:
        raise HTTPException(status_code=409, detail="File exists")
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=400, detail="Path is a directory")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to write file")
    return SimpleOkResponse()


@files_router.post("/delete", response_model=SimpleOkResponse)
async def delete_path(
    payload: DeleteRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> SimpleOkResponse:
    wid = _clean_workspace_id(workspace_id)
    rel = (payload.path or "").lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="path required")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    target = _resolve_within_root(root, rel)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    try:
        if target.is_dir():
            if not payload.recursive and any(target.iterdir()):
                raise HTTPException(status_code=400, detail="Directory not empty")
            import shutil

            shutil.rmtree(target)
        else:
            target.unlink()
    except HTTPException:
        raise
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to delete path")
    return SimpleOkResponse()


@files_router.post("/move", response_model=SimpleOkResponse)
async def move_path(
    payload: MoveRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
) -> SimpleOkResponse:
    wid = _clean_workspace_id(workspace_id)
    src = (payload.src or "").lstrip("/")
    dst = (payload.dst or "").lstrip("/")
    if not src or not dst:
        raise HTTPException(status_code=400, detail="src/dst required")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    src_p = _resolve_within_root(root, src)
    dst_p = _resolve_within_root(root, dst)
    if not src_p.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    if dst_p.exists() and not payload.overwrite:
        raise HTTPException(status_code=409, detail="Destination exists")
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if dst_p.exists() and payload.overwrite:
            if dst_p.is_dir():
                import shutil

                shutil.rmtree(dst_p)
            else:
                dst_p.unlink()
        src_p.replace(dst_p)
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to move path")
    return SimpleOkResponse()


@files_router.get("/search", response_model=SearchResponse)
async def search_files(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = "default",
    query: str = "",
    path: str = "",
    limit: int = 200,
) -> SearchResponse:
    wid = _clean_workspace_id(workspace_id)
    q = (query or "").strip()
    if not q:
        return SearchResponse(workspace_id=wid, query="", matches=[], truncated=False)
    if limit <= 0:
        limit = 200
    if limit > 2000:
        limit = 2000
    rel = (path or "").lstrip("/")
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)
    start = _resolve_within_root(root, rel) if rel else root
    if not start.exists() or not start.is_dir():
        raise HTTPException(status_code=404, detail="Path not found")
    matches: list[SearchMatch] = []
    truncated = False
    ql = q.lower()
    try:
        for p in start.rglob("*"):
            if len(matches) >= limit:
                truncated = True
                break
            name = p.name
            if name in (".git", ".DS_Store", ".work"):
                continue
            if ql not in name.lower():
                continue
            try:
                rel_path = str(p.relative_to(root.expanduser().resolve()))
            except Exception:
                continue
            matches.append(SearchMatch(path=rel_path, kind="dir" if p.is_dir() else "file"))
    except OSError:
        raise HTTPException(status_code=500, detail="Search failed")
    return SearchResponse(workspace_id=wid, query=q, matches=matches, truncated=truncated)


@files_router.get("/export")
async def export_workspace_outputs(
    request: Request,
    user_id: str = Depends(_get_user_from_request),
    workspace_id: str = Query(default="default"),
    session_id: str | None = Query(default=None),
) -> StreamingResponse:
    """Stream workspace output files as a zip archive.

    If `file_paths` query params are provided, only those specific files are included.
    Otherwise, all files under sessions/*/outputs/ are exported.
    """
    wid = _clean_workspace_id(workspace_id)
    root = _resolve_workspace_root(request=request, user_id=user_id, workspace_id=wid)

    # Parse file_paths from query params (multiple values supported)
    file_paths = request.query_params.getlist("file_paths")
    file_paths = [fp for fp in file_paths if fp.strip()]

    sessions_dir = root / "sessions"
    export_files: list[tuple[str, Path]] = []
    root_resolved = root.resolve()

    if file_paths:
        for rel_path in file_paths:
            normalized = rel_path.strip().replace("\\", "/")
            resolved = (root / normalized).resolve()
            if resolved.exists() and resolved.is_file() and str(resolved).startswith(str(root_resolved)):
                export_files.append((normalized, resolved))
    elif sessions_dir.is_dir():
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            outputs_dir = session_dir / "outputs"
            if not outputs_dir.is_dir():
                continue
            for p in outputs_dir.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(root))
                    export_files.append((rel, p))

    if not export_files:
        raise HTTPException(status_code=404, detail="No output files found")

    def _entry_name(rel_path: str) -> str:
        """去掉 sessions/<sid>/outputs/ 前缀，解压后直接看到交付物内容。"""
        import re as _re
        m = _re.match(r"^sessions/[^/]+/outputs/(.+)$", rel_path)
        if m:
            return m.group(1)
        return rel_path

    def _generate():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, filepath in export_files:
                zf.write(filepath, _entry_name(arcname))
        buf.seek(0)
        while True:
            chunk = buf.read(65536)
            if not chunk:
                break
            yield chunk

    # 带了 file_paths 就是「本轮交付物打包」，名字体现数量与时间；没带则是全量
    # 导出整个工作区，沿用原名。
    if file_paths:
        zip_name = f"交付物-{len(export_files)}个-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    else:
        zip_name = f"workspace-{wid}-outputs.zip"

    return StreamingResponse(
        _generate(),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition_attachment(zip_name)},
    )
