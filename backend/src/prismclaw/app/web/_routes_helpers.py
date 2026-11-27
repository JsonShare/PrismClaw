"""Shared helper functions extracted from routes.py.

These utilities are used across multiple route modules (routes, chat, etc.)
and are centralised here to avoid circular imports and code duplication.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi import HTTPException, Request

from prismclaw.app.session_router import SessionRouter
from prismclaw.app.web.deps import get_web_deps
from prismclaw.app.web.workspace_ids import (
    InvalidWorkspaceIdError,
    clean_workspace_id,
)
from prismclaw.schema.storage.storage_types import SessionStore

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from prismclaw.app.web.chat.chat_protocol import SessionQueue
    from prismclaw.engine.hooks.memory_nudge_hook import MemoryNudgeHook
    from prismclaw.infra.settings import Settings
    from prismclaw.infra.settings._dream import EvolutionSettings
    from prismclaw.infra.task_manager import TaskManager
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.storage.storage_types import MemoryStore

# ---------------------------------------------------------------------------
# Module-level globals — set once via set_web_deps() at lifespan startup.
# ---------------------------------------------------------------------------
_store: SessionStore | None = None
_session_router: SessionRouter | None = None
_memory_store: MemoryStore | None = None
_task_manager: TaskManager | None = None
_redis_client: Redis | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_llm_client: LLMClient | None = None
_evolution_settings: EvolutionSettings | None = None
_nudge_hook: MemoryNudgeHook | None = None


# ---------------------------------------------------------------------------
# Lazy import for the module-level session queue defined in chat.py.
# We avoid a top-level import to prevent circular-import issues; the chat
# module sets _session_queue at import time, so a deferred import is safe.
# ---------------------------------------------------------------------------
def _get_chat_session_queue() -> SessionQueue:
    from prismclaw.app.web.chat.chat_protocol import _session_queue

    return _session_queue


def set_web_deps(
    store: SessionStore,
    session_router: SessionRouter,
    *,
    memory_store: MemoryStore | None = None,
    task_manager: TaskManager | None = None,
    redis_client: Redis | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    llm_client: LLMClient | None = None,
    evolution_settings: EvolutionSettings | None = None,
    nudge_hook: MemoryNudgeHook | None = None,
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
    user's container (``user_root``); the concrete folders (outbox/inbox/
    tmp/...) live *inside* that sub-directory — never flattened at user_root.
    There is no local-filesystem binding: cloud workspaces always resolve to a
    server-side directory under ``workspace_base``.
    """
    from prismclaw.infra.workspace_paths import resolve_workspace_dir

    base = resolve_workspace_dir(user_id, workspace_id)
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def _resolve_workspace_path(root: Path, raw_path: str) -> Path:
    """工作区相对路径的**唯一解析入口**：先做会话段归一化，再做越界校验。

    前端传的是语义路径（会话段用原始 session_id，含 ``:``），磁盘上用的是
    归一化目录名（``:`` → ``-``）。归一化必须发生在解析之前，且只此一处，
    否则每个路由都要各自记得转换（会话 inbox/outbox 曾因此恒 404）。
    """
    from prismclaw.infra.workspace_paths import normalize_session_rel_path

    rel = (raw_path or "").lstrip("/")
    return _resolve_within_root(root, normalize_session_rel_path(rel))


def _resolve_within_root(root: Path, relative: str) -> Path:
    root_abs = root.expanduser().resolve()
    candidate = (root_abs / relative).expanduser()
    resolved = candidate.resolve() if candidate.exists() else candidate
    try:
        resolved.relative_to(root_abs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes workspace root") from exc
    return resolved


def _get_web_settings(request: Request) -> object | None:
    web_deps = get_web_deps(request)
    settings_full = web_deps.settings_full
    if settings_full is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    channels = getattr(settings_full, "channels", None)
    if channels is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    return getattr(channels, "web", None)


def _get_settings_full(request: Request) -> Settings:
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
    if base.startswith(("image/", "video/", "audio/", "text/")) or base in (
        "application/pdf",
        "application/json",
        "application/xml",
        "application/zip",
        "application/gzip",
        "application/x-tar",
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
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


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
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def _candidate_file_targets(
    *,
    workspace_root: Path,
    raw_path: str,
) -> list[Path]:
    from prismclaw.engine.toolkit.file.workspace import clean_rel_path

    s = (raw_path or "").strip()
    if not s:
        return []
    s = s.replace("\\", "/")
    if s.startswith("file://"):
        s = s[len("file://") :]

    workspace_root_abs = workspace_root.expanduser().resolve()

    is_abs = bool(re.match(r"^[A-Za-z]:/", s)) or s.startswith("/")
    if is_abs:
        try:
            abs_resolved = Path(s).expanduser().resolve()
        except Exception:
            abs_resolved = Path(s).expanduser()
        try:
            return [
                _resolve_workspace_path(
                    workspace_root_abs, str(abs_resolved.relative_to(workspace_root_abs))
                )
            ]
        except ValueError:
            return []

    rel = clean_rel_path(s)
    if not rel:
        return []
    try:
        return [_resolve_workspace_path(workspace_root_abs, rel)]
    except HTTPException:
        return []
