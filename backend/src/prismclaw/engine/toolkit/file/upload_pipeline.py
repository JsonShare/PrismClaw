"""Unified upload pipeline + UploadSink strategies.

``upload_files`` orchestrates the upload of a batch of files through a sink:
  1. pre-check each file against the blocklist / noise filter (single source
     of truth in ``upload_paths``);
  2. stream the file to the sink in fixed-size chunks;
  3. on any per-file failure, abort that file and record it as ``skipped`` —
     never abort the whole batch.

``LocalSink`` writes directly to a workspace directory (no tmp staging: a
failed write simply unlinks the partial destination).

Framework-free: the pipeline depends only on a duck-typed ``UploadInput``
(``filename`` / ``content_type`` / async ``read`` / async ``close``), which
FastAPI's ``UploadFile`` satisfies without the pipeline importing FastAPI.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from prismclaw.engine.toolkit.file.upload_paths import (
    DiskFull,
    InvalidPath,
    TransferFailed,
    is_blocked_type,
    is_noise_file,
    split_rel_path,
)

logger = logging.getLogger(__name__)

# Chunk size for streaming. 4 MiB bounds per-chunk memory; the local sink
# streams straight to disk so this only caps how much of a file is held in
# memory at a time.
CHUNK_SIZE = 4 * 1024 * 1024


@runtime_checkable
class UploadInput(Protocol):
    """Duck-typed upload source. FastAPI's ``UploadFile`` conforms."""

    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...

    async def close(self) -> None: ...


class UploadSink(Protocol):
    """Strategy for placing uploaded bytes. ``begin`` → ``write_chunk``* →
    ``commit``/``abort``. ``upload_id`` is opaque to the sink (used as a
    session key)."""

    async def begin(self, upload_id: str, rel: str, total_size: int | None) -> None: ...

    async def write_chunk(self, upload_id: str, seq: int, data: bytes) -> None: ...

    async def commit(self, upload_id: str) -> dict: ...

    async def abort(self, upload_id: str) -> None: ...


def _new_upload_id() -> str:
    return uuid.uuid4().hex


def _unique_dest(dest: Path) -> Path:
    """若同名文件已存在，追加 `` <n>`` 序号后缀，避免覆盖，返回可用目标路径。

    ``LocalSink.begin`` 直接 ``dest.open("wb")`` 写文件，若一次上传多个同名
    文件（如两页报告的连续截图 `image.png` + `image.png`），后者会静默覆盖
    前者——两个文件最终落到同一条 ``path``，前端据此回填的 ``source_path``
    也相同，导致图片预览「无法切换」、模型识别「两张一样」（都只读到被覆盖
    的那一张）。medical/persistence/storage.py 的 ``save_upload_bytes`` 已有
    相同先例（同名加序号）。序号加在扩展名前（`image (1).png`），既不改扩展
    名语义，也不影响 ``split_rel_path`` 的路径安全约束。
    """
    if not dest.exists():
        return dest
    parent = dest.parent
    stem = dest.stem
    suffix = dest.suffix
    for n in range(1, 10_000):
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    # 同目录同名文件数达上限（理论不可达），退回原名，宁可覆盖也不崩溃。
    return dest


def _skip_code(exc: Exception) -> str:
    if isinstance(exc, DiskFull):
        return "disk_full"
    if isinstance(exc, InvalidPath):
        return "invalid_path"
    if isinstance(exc, TransferFailed):
        return "transfer_failed"
    return "transfer_failed"


async def _close(uf: UploadInput) -> None:
    try:
        await uf.close()
    except Exception:
        pass


async def _safe_abort(sink: UploadSink, upload_id: str) -> None:
    try:
        await sink.abort(upload_id)
    except Exception:
        pass


async def upload_files(
    files: list[UploadInput],
    sink: UploadSink,
) -> tuple[list[dict], list[dict]]:
    """Upload ``files`` through ``sink``.

    Returns ``(uploaded, skipped)``:
      - ``uploaded``: list of ``{path, size, original_name, mime_type}``
      - ``skipped``: list of ``{name, code}`` where code is one of
        ``blocked_type`` / ``noise`` / ``disk_full`` / ``invalid_path`` /
        ``transfer_failed``.

    A per-file failure (including unexpected errors) is recorded as skipped and
    does not abort the batch. Each file's ``UploadInput`` is closed regardless
    of outcome.
    """
    uploaded: list[dict] = []
    skipped: list[dict] = []

    for uf in files:
        rel = (uf.filename or "file").replace("\x00", "")
        try:
            if is_blocked_type(rel):
                skipped.append({"name": rel, "code": "blocked_type"})
                continue
            if is_noise_file(rel):
                skipped.append({"name": rel, "code": "noise"})
                continue

            upload_id = _new_upload_id()
            try:
                await sink.begin(upload_id, rel, getattr(uf, "size", None))
                seq = 0
                while True:
                    chunk = await uf.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    await sink.write_chunk(upload_id, seq, chunk)
                    seq += 1
                result = await sink.commit(upload_id)
                result.setdefault("original_name", rel)
                result.setdefault("mime_type", uf.content_type)
                uploaded.append(result)
            except (DiskFull, InvalidPath, TransferFailed) as exc:
                await _safe_abort(sink, upload_id)
                skipped.append({"name": rel, "code": _skip_code(exc)})
            except Exception as exc:
                # Unknown per-file failure: isolate it, do not crash the batch.
                await _safe_abort(sink, upload_id)
                logger.warning("upload failed unexpectedly: name=%s err=%r", rel, exc)
                skipped.append({"name": rel, "code": "transfer_failed"})
        finally:
            await _close(uf)

    return uploaded, skipped


class LocalSink:
    """``UploadSink`` that streams directly into a workspace directory.

    ``workspace_root`` 是工作区根目录（commit 时返回它的相对路径，供前端 +
    附件解析用）；``dest_root`` 是实际写盘目录（可以是 workspace 根下任意
    子目录：inputs/、sessions/<sid>/uploads/、…）。两者可以相同（兼容旧用法）。

    Path safety: ``split_rel_path`` neutralizes lexical traversal (``..`` /
    ``\\`` / control chars); an additional ``resolve().relative_to()`` check
    guards against symlink escape. Violations raise ``InvalidPath``.

    No size cap: disk pressure is enforced only via the ``DiskFull`` pre-check
    when ``total_size`` is known upfront.
    """

    def __init__(self, workspace_root: Path, dest_root: Path | None = None) -> None:
        self._workspace_root = workspace_root.expanduser().resolve()
        self._dest_root = (dest_root or workspace_root).expanduser().resolve()
        self._open: dict[str, dict] = {}

    async def begin(self, upload_id: str, rel: str, total_size: int | None) -> None:
        parts, basename = split_rel_path(rel)
        target = self._dest_root
        for part in parts:
            target = target / part
        target.mkdir(parents=True, exist_ok=True)
        dest = target / basename

        # 同名去重：避免后上传的文件静默覆盖先上传的同名文件（见 _unique_dest）。
        dest = _unique_dest(dest)

        # Symlink / escape defense (split_rel_path already blocked "..").
        try:
            dest.resolve().relative_to(self._dest_root.resolve())
        except ValueError as exc:
            raise InvalidPath(f"路径越出上传目录：{rel}") from exc

        # Disk pre-check when size is known.
        if total_size is not None and total_size > 0:
            free = shutil.disk_usage(self._dest_root).free
            if free < total_size:
                raise DiskFull(f"磁盘空间不足：需要 {total_size} 字节，可用 {free} 字节")

        fh = dest.open("wb")
        self._open[upload_id] = {"fh": fh, "dest": dest, "size": 0}

    async def write_chunk(self, upload_id: str, seq: int, data: bytes) -> None:
        del seq  # ordering is enforced in-order by the pipeline; local writes need no seq check
        session = self._open.get(upload_id)
        if session is None:
            return  # already aborted; ignore
        session["fh"].write(data)
        session["size"] += len(data)

    async def commit(self, upload_id: str) -> dict:
        session = self._open.pop(upload_id, None)
        if session is None:
            raise InvalidPath("上传会话不存在")
        session["fh"].close()
        dest: Path = session["dest"]
        try:
            rel_path = str(dest.resolve().relative_to(self._workspace_root))
        except ValueError:
            rel_path = str(dest.relative_to(self._workspace_root))
        return {"path": rel_path, "size": session["size"]}

    async def abort(self, upload_id: str) -> None:
        session = self._open.pop(upload_id, None)
        if session is None:
            return
        try:
            session["fh"].close()
        except Exception:
            pass
        try:
            session["dest"].unlink(missing_ok=True)
        except Exception:
            pass
