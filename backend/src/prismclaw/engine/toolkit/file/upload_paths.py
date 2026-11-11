"""Shared upload filtering and path-splitting helpers.

This module is the single source of truth for upload-time decisions used by
the web channel (``routes.py``). It is intentionally free of any web-framework
dependency (no FastAPI ``HTTPException``) so it can be imported from any
layer.

Contents:
  - ``split_rel_path``: sanitize a folder-upload relative path into safe
    intermediate dir parts + basename.
  - ``BLOCKED_EXTS`` / ``is_blocked_type``: extension blocklist replacing the
    old MIME whitelist. Defends against accidental upload of executable
    content. NOTE: blocklist-by-extension defends against mistakes, not
    against deliberate spoofing (extension/MIME are user-controlled;
    extension-less ELF binaries are not caught). Execution-risk defense for
    the workspace belongs to the agent execution layer (bash/tool gating),
    not the upload layer.
  - ``NOISE_NAMES`` / ``NOISE_DIRS`` / ``is_noise_file``: drop OS metadata
    artifacts (``.DS_Store``, ``__MACOSX``). Hidden directories such as
    ``.git/`` are intentionally KEPT (user wants hidden files preserved).
  - ``DiskFull`` / ``TransferFailed`` / ``InvalidPath``: exceptions surfaced
    by sinks, mapped to ``skipped`` codes by the upload pipeline.
"""

from __future__ import annotations

from pathlib import Path

# Executable / dangerous extensions. Defends against accidental upload, not
# deliberate spoofing (see module docstring).
BLOCKED_EXTS: frozenset[str] = frozenset({
    ".exe", ".com", ".scr", ".msi",
    ".so", ".dylib", ".dll",
    ".reg", ".vbs", ".vbe", ".wsf", ".wsh", ".hta",
    # Bytecode / app bundles (completion of the blocklist).
    ".jar", ".class", ".pyc", ".app",
})

# OS metadata artifacts dropped on upload.
NOISE_NAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
NOISE_DIRS: frozenset[str] = frozenset({"__MACOSX"})


class InvalidPath(Exception):
    """Upload path is malformed or escapes the destination root."""


class DiskFull(Exception):
    """Insufficient disk space to receive the upload."""


class TransferFailed(Exception):
    """A chunk transfer failed (RPC timeout / WS error / agent error)."""


def split_rel_path(raw: str) -> tuple[list[str], str]:
    """Split an upload filename into ``(intermediate_dir_parts, safe_basename)``.

    Folder uploads arrive with a relative path (e.g. ``"a/b/c.txt"``) so the
    original directory structure can be preserved on the server. Each path
    component is sanitized to block path traversal (``/``, ``\\``, ``..``,
    null/control chars) while keeping unicode folder/file names (e.g. Chinese).
    Empty / dot / dot-dot components are dropped.
    """
    if not raw:
        return [], "file"
    parts = [p for p in raw.replace("\x00", "").replace("\\", "/").split("/")]
    safe: list[str] = []
    for part in parts:
        cleaned = part.strip().strip(".")
        if not cleaned:
            continue
        cleaned = "".join(
            c for c in cleaned if c not in ("/", "\\") and c.isprintable() and c not in "\t\n\r"
        )
        if cleaned in ("", ".", ".."):
            continue
        if len(cleaned) > 120:
            cleaned = cleaned[:120]
        safe.append(cleaned)
    if not safe:
        return [], "file"
    return safe[:-1], safe[-1]


def is_blocked_type(rel_path: str) -> bool:
    """Return True if the path's extension is in the executable blocklist.

    Extension-less files are NOT blocked (known limitation: an extension-less
    ELF binary is not caught). Execution-risk defense is the agent execution
    layer's responsibility.
    """
    ext = Path(rel_path).suffix.lower()
    return ext in BLOCKED_EXTS


def is_noise_file(rel_path: str) -> bool:
    """Return True if the path is an OS metadata artifact (e.g. ``.DS_Store``)
    or lives under a noise directory (e.g. ``__MACOSX/``)."""
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in NOISE_DIRS for p in parts):
        return True
    return bool(parts) and parts[-1] in NOISE_NAMES
