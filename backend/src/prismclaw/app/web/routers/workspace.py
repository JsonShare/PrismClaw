"""Workspace API routes – extracted from routes.py (router split)."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.deps import get_web_deps, user_root as _user_root
from prismclaw.app.web._routes_helpers import _clean_workspace_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workspace constants
# ---------------------------------------------------------------------------

_WORKSPACE_ENVS_DIR = ".envs"
_WORKSPACE_NODE_MODULES_DIR = "node_modules"
_WORKSPACE_SKILLS_DIR = "skills"
_WORKSPACE_SANDBOX_DIR = "sandbox"
_WORKSPACE_SESSIONS_DIR = "sessions"  # 🆕 session 级根目录
_SESSION_OUTPUTS_SUBDIR = "outputs"    # 🆕 sessions/<sid>/outputs/
_SESSION_UPLOADS_SUBDIR = "uploads"    # 🆕 sessions/<sid>/uploads/

# Sub-directory names excluded from recursive output scanning (any depth).
_EXCLUDED_OUTPUTS_SUBDIRS = frozenset({
    ".git",
    ".work",
    "node_modules",
    ".envs",
    "tmp",
    "uploads",
    "sandbox",
    "skills",
})

# Names directly under user_root that are NOT custom workspaces.
# T2 后 workspace 根只剩 inputs/ sessions/ .work/ + 用户项目文件，
# 系统子目录一律收进 .work/，保留名清单同步收缩。
_RESERVED_WORKSPACE_ROOT_NAMES = frozenset({
    "default",
    ".work",
    _WORKSPACE_SESSIONS_DIR,
    "inputs",
})


# ---------------------------------------------------------------------------
# Workspace metadata helpers
# ---------------------------------------------------------------------------

def _workspace_meta_file(user_root: Path) -> Path:
    """Path to the cloud workspace metadata store (``workspaces.json``).

    Replaces the legacy ``workspace_bindings.json``, which bound cloud workspace
    ids to local filesystem paths. Cloud workspaces are server-managed
    directories under ``user_root``; this file persists only display metadata
    (friendly name + creation timestamp) so listings survive restarts instead
    of falling back to the raw directory id.
    """
    return user_root / ".work" / "workspaces.json"


def _load_workspace_meta(user_root: Path) -> dict[str, dict[str, str]]:
    """Return cloud workspace metadata keyed by workspace id.

    Each value carries ``name`` and ``created_at_ms`` (both stored as strings
    for JSON stability). Missing/corrupt files yield an empty dict.
    """
    p = _workspace_meta_file(user_root)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    workspaces = raw.get("workspaces")
    if not isinstance(workspaces, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in workspaces.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        item: dict[str, str] = {}
        name_value = v.get("name")
        if isinstance(name_value, str) and name_value.strip():
            item["name"] = name_value.strip()
        created_value = v.get("created_at_ms")
        # create_workspace 写入的是数字字符串，这里必须同时接受 int/float 与
        # 数字字符串，否则 created_at_ms 在存取循环中被静默丢弃、永远取不回。
        if not isinstance(created_value, bool):
            try:
                created_num = float(created_value)
            except (TypeError, ValueError):
                created_num = None
            if created_num is not None and created_num >= 0:
                item["created_at_ms"] = str(int(created_num))
        if item:
            out[k] = item
    return out


def _save_workspace_meta(user_root: Path, meta: dict[str, dict[str, str]]) -> None:
    p = _workspace_meta_file(user_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"workspaces": meta, "updated_at_ms": int(time.time() * 1000)}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _workspace_created_ms(entry: dict[str, str], ws_dir: Path) -> float:
    """工作区创建时间（秒），用于列表排序。

    优先取元数据里持久化的 ``created_at_ms``；元数据缺失（元数据功能上线前创建、
    或目录为手工创建）时退回目录 birth time，再退回 mtime。尽力而为：读取失败
    返回 ``inf``，让该工作区稳定排到最后而不是破坏整个列表。
    """
    raw = entry.get("created_at_ms")
    if raw:
        try:
            return int(raw) / 1000
        except (TypeError, ValueError):
            pass
    try:
        st = ws_dir.stat()
    except OSError:
        return float("inf")
    birth = getattr(st, "st_birthtime", None)
    if isinstance(birth, (int, float)) and birth > 0:
        return float(birth)
    return float(st.st_mtime)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WorkspaceItem(BaseModel):
    id: str
    name: str


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceItem]


class CreateWorkspaceRequest(BaseModel):
    id: str
    name: str | None = None


class CreateWorkspaceResponse(BaseModel):
    workspace: WorkspaceItem


class OutputFileItem(BaseModel):
    workspace_id: str
    workspace_name: str
    rel_path: str
    name: str
    ext: str
    type: str
    size_bytes: int
    mtime_ms: int
    source_session_id: str | None = None
    source_session_title: str | None = None


class OutputsResponse(BaseModel):
    truncated: bool
    items: list[OutputFileItem]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

workspace_router = APIRouter(tags=["workspace"])


@workspace_router.get("/workspaces", response_model=WorkspacesResponse)
async def list_workspaces(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> WorkspacesResponse:
    workspace_base: Path = get_web_deps(request).workspace_base
    user_root = _user_root(workspace_base, user_id)

    items: list[WorkspaceItem] = [WorkspaceItem(id="default", name="默认工作区")]
    meta = _load_workspace_meta(user_root)
    # 自定义工作区按创建时间升序（先建的在前）；默认工作区固定置顶。
    custom: list[tuple[float, WorkspaceItem]] = []

    if user_root.is_dir():
        for p in user_root.iterdir():
            if not p.is_dir():
                continue
            # Skip the container's non-workspace entries (default is added
            # explicitly above; ".work" and any legacy flat folders are
            # never custom workspaces).
            if p.name in _RESERVED_WORKSPACE_ROOT_NAMES or p.name.startswith("."):
                continue
            try:
                wid = _clean_workspace_id(p.name)
            except HTTPException:
                continue
            if wid == "default":
                continue
            entry = meta.get(wid, {})
            name = entry.get("name") or wid
            custom.append((_workspace_created_ms(entry, p), WorkspaceItem(id=wid, name=name)))

    custom.sort(key=lambda t: (t[0], t[1].name.lower()))
    items.extend(item for _, item in custom)
    return WorkspacesResponse(workspaces=items)


def _categorize_ext(ext: str) -> str:
    """Map a file extension to a coarse category used by the outputs view."""
    e = (ext or "").strip().lower().lstrip(".")
    docs = {"doc", "docx", "pdf", "md", "txt", "rtf"}
    sheets = {"xls", "xlsx", "csv", "tsv"}
    slides = {"ppt", "pptx", "key"}
    images = {"png", "jpg", "jpeg", "gif", "svg", "webp"}
    web = {"html", "htm"}
    code = {"js", "jsx", "ts", "tsx", "mjs", "cjs", "py", "java", "jar", "css", "sql", "yml", "yaml", "xml", "json"}
    if e in docs:
        return "document"
    if e in sheets:
        return "spreadsheet"
    if e in slides:
        return "slide"
    if e in images:
        return "image"
    if e in web:
        return "web"
    if e in code:
        return "code"
    return "other"


@workspace_router.get("/workspaces/outputs", response_model=OutputsResponse)
async def list_workspace_outputs(
    request: Request,
    user_id: str = Depends(get_current_user),
    workspace_id: str = Query(default="all"),
) -> OutputsResponse:
    """Aggregate deliverable files across workspaces (scans ``outputs/`` only).

    - ``workspace_id=all`` (default) aggregates every workspace.
    - Excludes system sub-directories at any depth.
    - Purely read-only; never mutates the filesystem.
    """
    workspace_base: Path = get_web_deps(request).workspace_base
    user_root = _user_root(workspace_base, user_id)
    meta = _load_workspace_meta(user_root)

    targets: list[tuple[str, str]] = []
    if workspace_id and workspace_id != "all":
        try:
            wid = _clean_workspace_id(workspace_id)
        except HTTPException:
            wid = "default"
        targets.append((wid, meta.get(wid, {}).get("name") or wid))
    else:
        targets.append(("default", "默认工作区"))
        if user_root.is_dir():
            for p in user_root.iterdir():
                if not p.is_dir():
                    continue
                if p.name in _RESERVED_WORKSPACE_ROOT_NAMES or p.name.startswith("."):
                    continue
                try:
                    wid = _clean_workspace_id(p.name)
                except HTTPException:
                    continue
                if wid == "default":
                    continue
                targets.append((wid, meta.get(wid, {}).get("name") or wid))

    MAX_ITEMS = 2000
    items: list[OutputFileItem] = []
    for wid, wname in targets:
        sessions_dir = user_root / wid / _WORKSPACE_SESSIONS_DIR
        if not sessions_dir.is_dir():
            continue
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            sid = session_dir.name
            outputs_dir = session_dir / _SESSION_OUTPUTS_SUBDIR
            if not outputs_dir.is_dir():
                continue
            for p in outputs_dir.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(outputs_dir)
                if any(part in _EXCLUDED_OUTPUTS_SUBDIRS for part in rel.parts[:-1]):
                    continue
                try:
                    stat = p.stat()
                except OSError:
                    continue
                ext = rel.suffix.lstrip(".")
                # 路径相对 workspace 根：sessions/<sid>/outputs/<相对路径>，
                # 与 /api/files/raw、/api/files/download、导出接口解析口径一致。
                rel_path = (
                    f"{_WORKSPACE_SESSIONS_DIR}/{sid}/"
                    f"{_SESSION_OUTPUTS_SUBDIR}/{rel.as_posix()}"
                )
                items.append(
                    OutputFileItem(
                        workspace_id=wid,
                        workspace_name=wname,
                        rel_path=rel_path,
                        name=p.name,
                        ext=ext,
                        type=_categorize_ext(ext),
                        size_bytes=stat.st_size,
                        mtime_ms=int(stat.st_mtime * 1000),
                        source_session_id=sid,   # 🆕 直接从目录名拿到！
                        source_session_title=None,  # 如需标题再补 Redis 查询
                    )
                )
                if len(items) >= MAX_ITEMS:
                    break
            if len(items) >= MAX_ITEMS:
                break
        if len(items) >= MAX_ITEMS:
            break

    return OutputsResponse(truncated=len(items) >= MAX_ITEMS, items=items)


@workspace_router.post("/workspaces", response_model=CreateWorkspaceResponse)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> CreateWorkspaceResponse:
    wid = _clean_workspace_id(payload.id)
    if wid in _RESERVED_WORKSPACE_ROOT_NAMES or wid.startswith("."):
        # 这些名字与 user_root 根下的系统/staging 目录冲突，创建后既会互相覆盖、
        # 也不会出现在列表里（被 reserved 过滤），直接拒绝。
        raise HTTPException(status_code=400, detail=f"workspace id '{wid}' is reserved")

    workspace_base: Path = get_web_deps(request).workspace_base
    user_root = _user_root(workspace_base, user_id)
    ws_dir = user_root / wid
    if ws_dir.exists() and not ws_dir.is_dir():
        raise HTTPException(status_code=409, detail="Workspace path exists and is not a directory")
    ws_dir.mkdir(parents=True, exist_ok=True)

    name = payload.name.strip() if isinstance(payload.name, str) and payload.name.strip() else wid
    # Persist display metadata so the friendly name survives restarts instead
    # of degrading to the raw directory id on next list.
    meta = _load_workspace_meta(user_root)
    meta[wid] = {"name": name, "created_at_ms": str(int(time.time() * 1000))}
    _save_workspace_meta(user_root, meta)
    return CreateWorkspaceResponse(workspace=WorkspaceItem(id=wid, name=name))


@workspace_router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
) -> dict[str, bool]:
    wid = _clean_workspace_id(workspace_id)
    if wid == "default":
        raise HTTPException(status_code=400, detail="default workspace cannot be deleted")

    workspace_base: Path = get_web_deps(request).workspace_base
    user_root = _user_root(workspace_base, user_id)
    ws_dir = user_root / wid
    if not ws_dir.exists():
        raise HTTPException(status_code=404, detail="workspace not found")
    if not ws_dir.is_dir():
        raise HTTPException(status_code=400, detail="workspace path is not a directory")

    # Drop persisted display metadata so the deleted id doesn't reappear as a
    # ghost entry if the directory is later recreated without a name.
    meta = _load_workspace_meta(user_root)
    if wid in meta:
        del meta[wid]
        _save_workspace_meta(user_root, meta)

    # Clean persisted oversized tool results so they don't leak after delete.
    # Cloud workspaces own their tool_results subdir under ws_dir, so a single
    # cleanup target is sufficient (no separate bound-root path anymore).
    try:
        # 免除：通过 WebDeps 注入的函数引用获取，不再直接 import engine 内部
        _web_deps = get_web_deps(request)
        _cleanup = getattr(_web_deps, "cleanup_working_dir_fn", None) if _web_deps is not None else None
        if _cleanup is not None:
            await _cleanup(ws_dir)
    except Exception as _err:  # best-effort; never block the delete
        logger.warning("cleanup tool_results failed for %s: %s", ws_dir, _err)

    shutil.rmtree(ws_dir, ignore_errors=True)
    return {"ok": True}