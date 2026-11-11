"""Shared helper functions extracted from routes.py.

These utilities are used across multiple route modules (routes, chat, etc.)
and are centralised here to avoid circular imports and code duplication.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request

from prismclaw.app.session_router import SessionRouter
from prismclaw.app.web.deps import get_web_deps, user_root as _user_root
from prismclaw.app.web.workspace_ids import (
    InvalidWorkspaceIdError,
    clean_workspace_id,
)
from prismclaw.schema.storage.storage_types import SessionStore

# ---------------------------------------------------------------------------
# Module-level globals — set once via set_web_deps() at lifespan startup.
# ---------------------------------------------------------------------------
_store: SessionStore | None = None
_session_router: SessionRouter | None = None
_memory_store: Any = None
_task_manager: Any = None
_redis_client: Any = None
_session_factory: Any = None
_llm_client: Any = None
_evolution_settings: Any = None
_nudge_hook: Any = None

# Managed workspace sub-directory layout.
# uploads / outputs / tmp 已切 session 级（sessions/<sid>/uploads|outputs|tmp），
# 这里只保留 workspace 级共享目录。
_WORKSPACE_ENVS_DIR = ".envs"
_WORKSPACE_NODE_MODULES_DIR = "node_modules"
_WORKSPACE_SKILLS_DIR = "skills"
_WORKSPACE_SANDBOX_DIR = "sandbox"


# ---------------------------------------------------------------------------
# Lazy import for the module-level session queue defined in chat.py.
# We avoid a top-level import to prevent circular-import issues; the chat
# module sets _session_queue at import time, so a deferred import is safe.
# ---------------------------------------------------------------------------
def _get_chat_session_queue():
    from prismclaw.app.web.chat.chat_protocol import _session_queue
    return _session_queue


def ensure_managed_workspace_dirs(root: Path) -> None:
    """Create the constrained workspace directories (single source of truth).

    inputs/ is workspace-level shared (所有 session 只读可见)，
    sessions/<sid>/ 是 session 级隔离（每个会话独立 uploads/outputs/context_blocks/tool_results）。
    系统运行目录统一收进 `.work/`。
    """
    # 用户可见目录（留根）
    for name in ("inputs", "sessions"):
        (root / name).mkdir(parents=True, exist_ok=True)
    # 系统运行目录（收进 .work/）
    work_dirs = (_WORKSPACE_ENVS_DIR, _WORKSPACE_NODE_MODULES_DIR,
                 _WORKSPACE_SKILLS_DIR, _WORKSPACE_SANDBOX_DIR)
    for name in work_dirs:
        (root / ".work" / name).mkdir(parents=True, exist_ok=True)


def set_web_deps(
    store: SessionStore,
    session_router: SessionRouter,
    *,
    memory_store: Any = None,
    task_manager: Any = None,
    redis_client: Any = None,
    session_factory: Any = None,
    llm_client: Any = None,
    evolution_settings: Any = None,
    nudge_hook: Any = None,
) -> None:
    global _store, _session_router, _memory_store, _task_manager
    global _redis_client, _session_factory, _llm_client, _evolution_settings, _nudge_hook
    _store = store
    _session_router = session_router
    _memory_store = memory_store
    _task_manager = task_manager
    _redis_client = redis_client
    _session_factory = session_factory
    _llm_client = llm_client
    _evolution_settings = evolution_settings
    _nudge_hook = nudge_hook


def _get_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("Web deps not initialised")
    return _store


def _get_router() -> SessionRouter:
    if _session_router is None:
        raise RuntimeError("Web deps not initialised")
    return _session_router


def _clean_workspace_id(workspace_id: str | None) -> str:
    try:
        return clean_workspace_id(workspace_id)
    except InvalidWorkspaceIdError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace_id") from exc


def _resolve_workspace_root(
    *,
    request: Request,
    user_id: str,
    workspace_id: str,
) -> Path:
    """Resolve a cloud workspace's server-managed directory.

    Every workspace ("default" or a custom id) is a direct sub-directory of the
    user's container (``user_root``); the concrete folders (outputs/uploads/
    tmp/...) live *inside* that sub-directory — never flattened at user_root.
    There is no local-filesystem binding: cloud workspaces always resolve to a
    server-side directory under ``workspace_base``.
    """
    workspace_base: Path = get_web_deps(request).workspace_base
    user_root = _user_root(workspace_base, user_id)

    if workspace_id == "default":
        base = user_root / "default"
    else:
        base = user_root / workspace_id
    base.mkdir(parents=True, exist_ok=True)

    ensure_managed_workspace_dirs(base)
    return base.resolve()


def _resolve_within_root(root: Path, relative: str) -> Path:
    root_abs = root.expanduser().resolve()
    candidate = (root_abs / relative).expanduser()
    resolved = candidate.resolve() if candidate.exists() else candidate
    try:
        resolved.relative_to(root_abs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes workspace root") from exc
    return resolved


def _get_web_settings(request: Request) -> Any:
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    if settings_full is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    channels = getattr(settings_full, "channels", None)
    if channels is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    return getattr(channels, "web", None)


def _get_settings_full(request: Request) -> Any:
    web_deps = get_web_deps(request)
    return web_deps.settings_full


# ---------------------------------------------------------------------------
# MIME / CSP / Content-Disposition helpers (used by files routes)
# ---------------------------------------------------------------------------

_CSP_SANDBOX = "default-src 'none'; sandbox allow-scripts allow-same-origin"

_TEXT_EXT_TO_MIME: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".rst": "text/plain",
    ".tex": "text/plain",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".ts": "text/plain",
    ".tsx": "text/plain",
    ".jsx": "text/plain",
    ".css": "text/css",
    ".scss": "text/css",
    ".less": "text/css",
    ".json": "application/json",
    ".jsonc": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".sh": "text/plain",
    ".bash": "text/plain",
    ".zsh": "text/plain",
    ".sql": "text/plain",
    ".csv": "text/csv",
    ".log": "text/plain",
    ".env": "text/plain",
    ".gitignore": "text/plain",
    ".dockerfile": "text/plain",
    ".makefile": "text/plain",
    ".go": "text/plain",
    ".java": "text/plain",
    ".c": "text/plain",
    ".cpp": "text/plain",
    ".h": "text/plain",
    ".hpp": "text/plain",
    ".rs": "text/plain",
    ".rb": "text/plain",
    ".php": "text/plain",
    ".lua": "text/plain",
    ".scala": "text/plain",
    ".kt": "text/plain",
    ".swift": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
}


def _raw_text_media_type(rel: str) -> str:
    """为 raw 文件预览推导 MIME。

    优先级：扩展名/点文件名兜底（覆盖 mimetypes 误判如 .ts→video/mp2t、以及无法识别的
    .md/.env/.gitignore 等）> mimetypes 猜测（文本类补 charset）> application/octet-stream。
    """
    p = Path(rel)
    ext = p.suffix.lower()
    name = p.name
    mapped = _TEXT_EXT_TO_MIME.get(ext) or _TEXT_EXT_TO_MIME.get(name)
    if mapped:
        return f"{mapped}; charset=utf-8"
    guessed = mimetypes.guess_type(rel)[0]
    if guessed:
        if guessed.startswith("text/") or guessed in (
            "application/json",
            "application/xml",
            "application/javascript",
            "application/x-yaml",
        ):
            return guessed if "; charset=" in guessed else f"{guessed}; charset=utf-8"
        return guessed
    return "application/octet-stream"


def _csp_for_mime(media_type: str | None) -> str | None:
    """Return CSP header for the raw file endpoint, or None if safe to omit.

    Aggressive CSP (sandbox) is applied only to text/html to prevent XSS from
    uploaded HTML files. Images, PDFs, video/audio, plain text, and code files
    skip CSP entirely so inline preview (PDF.js, browser image viewer, etc.)
    works without restriction.
    """
    base = (media_type or "").split(";")[0].strip().lower()
    if not base:
        return _CSP_SANDBOX
    if base == "text/html":
        return _CSP_SANDBOX
    if (
        base.startswith(("image/", "video/", "audio/", "text/"))
        or base in ("application/pdf", "application/json", "application/xml",
                    "application/zip", "application/gzip", "application/x-tar")
    ):
        return None
    return _CSP_SANDBOX


def _content_disposition_inline(filename: str) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1]
    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    suffix = Path(name).suffix
    if not ascii_fallback:
        ascii_fallback = "file" + suffix
    if suffix and ascii_fallback.lower() == suffix.lstrip(".").lower():
        ascii_fallback = "file" + suffix
    ascii_fallback = ascii_fallback.replace('"', "_")
    encoded = quote(name, safe="!#$&+-.^_`|~")
    return f'inline; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def _content_disposition_attachment(filename: str) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1]
    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    suffix = Path(name).suffix
    if not ascii_fallback:
        ascii_fallback = "file" + suffix
    if suffix and ascii_fallback.lower() == suffix.lstrip(".").lower():
        ascii_fallback = "file" + suffix
    ascii_fallback = ascii_fallback.replace('"', "_")
    encoded = quote(name, safe="!#$&+-.^_`|~")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def _candidate_file_targets(
    *,
    io_root: Path,
    workspace_root: Path,
    raw_path: str,
) -> list[Path]:
    s = (raw_path or "").strip()
    if not s:
        return []
    if (
        (s.startswith('"') and s.endswith('"'))
        or (s.startswith("'") and s.endswith("'"))
        or (s.startswith("`") and s.endswith("`"))
    ):
        s = s[1:-1].strip()
    s = s.replace("\\", "/")
    if s.startswith("file://"):
        s = s[len("file://") :]

    io_root_abs = io_root.expanduser().resolve()
    workspace_root_abs = workspace_root.expanduser().resolve()

    rel_candidates: list[str] = []
    abs_candidate: Path | None = None
    is_abs = bool(re.match(r"^[A-Za-z]:/", s)) or s.startswith("/") or s.startswith("//")
    if not is_abs:
        for base in (io_root_abs, workspace_root_abs):
            base_posix = base.as_posix()
            if base_posix.startswith("/") and (s == base_posix[1:] or s.startswith(base_posix[1:] + "/")):
                s = "/" + s
                is_abs = True
                break
    if is_abs:
        abs_candidate = Path(s)

    if abs_candidate is not None:
        try:
            abs_resolved = abs_candidate.expanduser().resolve()
        except Exception:
            abs_resolved = abs_candidate.expanduser()
        for base in (io_root_abs, workspace_root_abs):
            try:
                rel_candidates.append(str(abs_resolved.relative_to(base)))
            except ValueError:
                continue

    unbound_uploads_root = io_root_abs.name == "uploads" and io_root_abs.parent == workspace_root_abs
    if unbound_uploads_root:
        if s.startswith("uploads/"):
            rel_candidates.append(s[len("uploads/") :])
        elif s.startswith("/uploads/"):
            rel_candidates.append(s[len("/uploads/") :])

    rel_candidates.append(s.lstrip("/"))

    if io_root_abs == workspace_root_abs:
        stripped = s.lstrip("/")
        if stripped and not stripped.startswith("uploads/"):
            rel_candidates.append(f"uploads/{stripped}")

    uniq_rels: list[str] = []
    for r in rel_candidates:
        rr = (r or "").strip().lstrip("/")
        if not rr:
            continue
        if rr.startswith("../"):
            continue
        if rr not in uniq_rels:
            uniq_rels.append(rr)

    targets: list[Path] = []
    for rr in uniq_rels:
        try:
            targets.append(_resolve_within_root(workspace_root_abs if rr.startswith(".work/") else io_root_abs, rr))
        except HTTPException:
            continue
    return targets
