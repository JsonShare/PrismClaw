"""Self-contained logging bootstrap for PrismClaw-managed MCP servers.

Zero-dependency (stdlib only) so self-built MCP servers (standalone uv
packages / runtime plugins) can import this module without the prismclaw
package installed. The client injects PRISMCLAW_MCP_SERVER_LOG_DIR before
spawning the server process; call :func:`install_from_env` early at startup.

Layout produced (mirrors the main app policy):
    <dir>/log.log     INFO and above
    <dir>/error.log   WARNING and above
    <dir>/log-YYYYMMDD.log.zip   size-rolled archives (>500MB default)
"""

from __future__ import annotations

import logging
import os
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ARCHIVE_KEEP = 14
_MARK_FULL = "_mcp_logkit_emitted_full"
_MARK_ERROR = "_mcp_logkit_emitted_error"

_FMT = (
    "%(asctime)s.%(msecs)03d | "
    "%(levelname)-8s | "
    "%(process)d | "
    "%(name)s:%(funcName)s:%(lineno)d | "
    "%(message)s"
)


class _ZipRotatingFileHandler(RotatingFileHandler):
    """Roll by size and zip the rolled file as log-YYYYMMDD.log.zip."""

    def __init__(self, filename: str | Path, *, max_bytes: int, **kwargs) -> None:
        super().__init__(filename, maxBytes=max_bytes, **kwargs)

    def doRollover(self) -> None:  # noqa: D102
        if self.stream:
            self.stream.close()
            self.stream = None

        base_path = Path(self.baseFilename)
        if base_path.exists():
            date_str = datetime.now().strftime("%Y%m%d")
            stem = base_path.stem
            target = base_path.parent / f"{stem}-{date_str}.log.zip"
            seq = 1
            while target.exists():
                target = base_path.parent / f"{stem}-{date_str}.log.{seq}.zip"
                seq += 1
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.write(base_path, arcname=base_path.name)
            base_path.unlink()
            self._prune_old_archives(base_path)

        if not self.delay:
            self.stream = self._open()

    def _prune_old_archives(self, base_path: Path) -> None:
        archives = sorted(
            base_path.parent.glob(f"{base_path.stem}-*.log*.zip"),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(archives) - _ARCHIVE_KEEP
        for old in archives[:excess]:
            try:
                old.unlink()
            except OSError:
                pass


class _OncePerHandlerFilter(logging.Filter):
    """Deduplicate a record reaching the same handler via the logger chain."""

    def __init__(self, mark: str) -> None:
        super().__init__()
        self._mark = mark

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, self._mark, False):
            return False
        setattr(record, self._mark, True)
        return True


def _resolve_max_bytes() -> int:
    raw = (os.getenv("PRISMCLAW_LOG_MAX_MB") or "").strip()
    try:
        mb = int(raw) if raw else 500
    except ValueError:
        mb = 500
    return mb * 1024 * 1024


def install_from_env(env_var: str = "PRISMCLAW_MCP_SERVER_LOG_DIR") -> logging.Logger | None:
    """Install file logging for this server process from the injected env.

    Returns the root logger unchanged (None-logged) when the env var is
    absent or the directory is not writable — the server keeps running with
    stderr-only logging in that case.
    """
    log_dir_raw = (os.getenv(env_var) or "").strip()
    if not log_dir_raw:
        return None
    log_dir = Path(log_dir_raw)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".write_test"
        probe.touch()
        probe.unlink()
    except Exception:
        return None

    formatter = logging.Formatter(fmt=_FMT, datefmt="%Y-%m-%d %H:%M:%S")
    max_bytes = _resolve_max_bytes()

    handlers: list[logging.Handler] = []
    for filename, level, mark in (
        ("log.log", logging.INFO, _MARK_FULL),
        ("error.log", logging.WARNING, _MARK_ERROR),
    ):
        h = _ZipRotatingFileHandler(
            log_dir / filename,
            max_bytes=max_bytes,
            encoding="utf-8",
            delay=True,
        )
        h.setFormatter(formatter)
        h.setLevel(level)
        h.addFilter(_OncePerHandlerFilter(mark))
        handlers.append(h)

    root = logging.getLogger()
    # 是否已自行配置过日志（basicConfig / setLevel）必须在挂 handler 之前检测，
    # 否则 handlers 非空会令该判断永远为假。
    had_own_config = bool(root.handlers) or root.level != logging.WARNING
    for h in handlers:
        root.addHandler(h)

    # stderr 仍保留（客户端 pump 会把子进程 stderr 收进 mcp_client_<name>）
    if not any(isinstance(x, logging.StreamHandler) and not isinstance(x, RotatingFileHandler)
               for x in root.handlers):
        stderr_h = logging.StreamHandler()
        stderr_h.setFormatter(formatter)
        root.addHandler(stderr_h)

    if not had_own_config:
        raw_level = (os.getenv("PRISMCLAW_MCP_SERVER_LOG_LEVEL") or "INFO").strip().upper()
        root.setLevel(getattr(logging, raw_level, logging.INFO))

    # 第三方库噪音收敛
    for name in ("httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return root


__all__ = ["install_from_env"]
