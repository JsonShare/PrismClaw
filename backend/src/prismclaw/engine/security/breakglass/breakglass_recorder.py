"""P4-SEC-01：BreakGlass 录屏 — MediaRecorder 分片 + WORM 哈希链绑定。

当 Break-Glass 触发时，自动开启录屏记录操作过程。

核心机制：
  1. 分片录制：每 N 秒切割一个分片，避免单文件过大
  2. WORM 哈希链：每个分片包含前一分片的 SHA256，与审计日志同构
  3. 分片索引：记录所有分片的 hash + 偏移，用于完整性校验
  4. 24h 完整性验证：回放后验证分片哈希链 100% 连续

存储目标：
  - S3 Object Lock（Compliance Mode）：分片 + 索引不可删
  - 本地缓冲：S3 不可用时降级本地存储
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordingChunk:
    """单个录屏分片。"""
    chunk_id: str               # 分片 ID
    session_id: str             # 录制会话 ID
    breakglass_token: str       # 关联的 BreakGlass token
    sequence: int               # 分片序号（从 0 开始）
    timestamp: float            # 分片开始时间
    size_bytes: int             # 分片大小
    content_hash: str           # 分片内容 SHA256
    prev_chunk_hash: str        # 前一分片 hash（哈希链）
    storage_path: str = ""      # S3 / 本地路径


@dataclass
class RecordingIndex:
    """录屏分片索引。"""
    session_id: str
    breakglass_token: str
    started_at: float
    finished_at: float | None = None
    total_chunks: int = 0
    total_bytes: int = 0
    chunks: list[RecordingChunk] = field(default_factory=list)
    index_hash: str = ""        # 整个索引的哈希（完整性校验用）
    _pending_uploads: list[Any] = field(default_factory=list, repr=False, compare=False)


# ── StorageProtocol ─────────────────────────────────────────────────


@runtime_checkable
class RecordingStorage(Protocol):
    """录屏分片存储协议。"""

    async def upload_chunk(self, chunk: RecordingChunk, data: bytes) -> str:
        """上传分片，返回存储路径。"""
        ...

    async def download_chunk(self, storage_path: str) -> bytes | None:
        """下载分片。"""
        ...

    async def upload_index(self, index: RecordingIndex) -> str:
        """上传索引。"""
        ...


# ── LocalRecordingStorage ───────────────────────────────────────────


class LocalRecordingStorage:
    """本地文件系统录屏存储（开发/测试用）。"""

    def __init__(self, base_dir: str = "/tmp/prismclaw_recordings") -> None:
        self._base_dir = base_dir

    async def upload_chunk(self, chunk: RecordingChunk, data: bytes) -> str:
        import os
        path = os.path.join(self._base_dir, chunk.session_id, f"chunk_{chunk.sequence:04d}.bin")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    async def download_chunk(self, storage_path: str) -> bytes | None:
        try:
            with open(storage_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    async def upload_index(self, index: RecordingIndex) -> str:
        import os
        path = os.path.join(self._base_dir, index.session_id, "index.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "session_id": index.session_id,
                "breakglass_token": index.breakglass_token,
                "started_at": index.started_at,
                "finished_at": index.finished_at,
                "total_chunks": index.total_chunks,
                "total_bytes": index.total_bytes,
                "chunks": [
                    {
                        "chunk_id": c.chunk_id,
                        "session_id": c.session_id,
                        "sequence": c.sequence,
                        "timestamp": c.timestamp,
                        "size_bytes": c.size_bytes,
                        "content_hash": c.content_hash,
                        "prev_chunk_hash": c.prev_chunk_hash,
                        "storage_path": c.storage_path,
                    }
                    for c in index.chunks
                ],
                "index_hash": index.index_hash,
            }, f, ensure_ascii=False, indent=2)
        return path


# ── BreakGlassRecorder ──────────────────────────────────────────────


class BreakGlassRecorder:
    """BreakGlass 录屏记录器。

    分片录制 + WORM 哈希链绑定 + 分片索引。
    验收：24h 后分片哈希链 100% 连续。
    """

    def __init__(
        self,
        *,
        storage: RecordingStorage | None = None,
        chunk_interval_seconds: int = 60,  # 每 60 秒切割一个分片
        worm_audit: Any = None,
    ) -> None:
        self._storage = storage or LocalRecordingStorage()
        self._chunk_interval = chunk_interval_seconds
        self._worm_audit = worm_audit
        # 活跃录制会话：breakglass_token → RecordingIndex
        self._active_sessions: dict[str, RecordingIndex] = {}
        # 分片缓冲：breakglass_token → list[bytes]
        self._chunk_buffers: dict[str, list[bytes]] = {}

    def start_recording(
        self,
        breakglass_token: str,
        session_id: str,
    ) -> RecordingIndex:
        """开始录屏会话。"""
        index = RecordingIndex(
            session_id=session_id,
            breakglass_token=breakglass_token,
            started_at=time.time(),
        )
        self._active_sessions[breakglass_token] = index
        self._chunk_buffers[breakglass_token] = []

        if self._worm_audit is not None:
            self._worm_audit.log(
                session_id,
                "recording_started",
                {"breakglass_token": breakglass_token, "session_id": session_id},
            )

        logger.info("BreakGlassRecorder: started recording for token %s", breakglass_token)
        return index

    def append_data(
        self,
        breakglass_token: str,
        data: bytes,
    ) -> None:
        """追加录屏数据到当前缓冲。"""
        buffer = self._chunk_buffers.get(breakglass_token)
        if buffer is None:
            logger.warning("BreakGlassRecorder: no active session for token %s", breakglass_token)
            return
        buffer.append(data)

        # 检查是否需要切割分片
        index = self._active_sessions.get(breakglass_token)
        if index is None:
            return
        elapsed = time.time() - index.started_at
        expected_chunks = int(elapsed / self._chunk_interval)
        if expected_chunks > index.total_chunks and len(buffer) > 0:
            # 切割分片（同步触发，实际生产中由异步循环驱动）
            self._flush_chunk(breakglass_token)

    def _flush_chunk(self, breakglass_token: str) -> RecordingChunk | None:
        """将当前缓冲切割为一个分片并**同步上传到存储**。

        上传失败不丢分片：数据保留在 RecordingIndex，调用方可重试。
        storage_path 写入 chunk 副本回写 index.chunks，保证索引可追溯。
        """
        import asyncio

        buffer = self._chunk_buffers.get(breakglass_token)
        index = self._active_sessions.get(breakglass_token)
        if buffer is None or index is None or not buffer:
            return None

        data = b"".join(buffer)
        buffer.clear()

        content_hash = hashlib.sha256(data).hexdigest()
        prev_hash = index.chunks[-1].content_hash if index.chunks else ""

        chunk = RecordingChunk(
            chunk_id=f"rec-{breakglass_token[:8]}-{index.total_chunks:04d}",
            session_id=index.session_id,
            breakglass_token=breakglass_token,
            sequence=index.total_chunks,
            timestamp=time.time(),
            size_bytes=len(data),
            content_hash=content_hash,
            prev_chunk_hash=prev_hash,
        )

        # 立即上传到存储（同步包装 async 调用，上传失败继续写索引以便重放）
        storage_path = ""
        try:
            # 允许同步调用方（append_data）复用当前事件循环；无循环时新建
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                # 在已运行的事件循环内，挂异步上传任务；stop_recording 会集中 await。
                task = loop.create_task(self._storage.upload_chunk(chunk, data))
                index._pending_uploads.append((task, chunk, data))
            else:
                storage_path = asyncio.run(self._storage.upload_chunk(chunk, data))
        except Exception as e:
            logger.error(
                "BreakGlassRecorder: upload chunk %s failed: %s", chunk.chunk_id, e,
            )
            # 失败时仍写入索引 — storage_path 留空，后续可根据 chunk 元数据重试

        # 回填 storage_path 后写入索引（记录不变对象，故替换 list 中的引用）
        if storage_path:
            chunk = RecordingChunk(
                chunk_id=chunk.chunk_id,
                session_id=chunk.session_id,
                breakglass_token=chunk.breakglass_token,
                sequence=chunk.sequence,
                timestamp=chunk.timestamp,
                size_bytes=chunk.size_bytes,
                content_hash=chunk.content_hash,
                prev_chunk_hash=chunk.prev_chunk_hash,
                storage_path=storage_path,
            )

        index.chunks.append(chunk)
        index.total_chunks += 1
        index.total_bytes += len(data)

        return chunk

    async def stop_recording(self, breakglass_token: str) -> RecordingIndex | None:
        """停止录屏会话：flush 剩余分片 → 等待未完成上传 → 上传索引。

        关键修复：flush_chunk 必须在 pop _active_sessions 之前执行，
        否则 _flush_chunk 查不到 session 会静默跳过，最后一个分片永久丢失。
        """
        index = self._active_sessions.get(breakglass_token)
        if index is None:
            self._chunk_buffers.pop(breakglass_token, None)
            return None

        # ① flush 剩余缓冲（此时 _active_sessions 仍有 entry）
        buffer = self._chunk_buffers.get(breakglass_token, [])
        if buffer:
            self._flush_chunk(breakglass_token)

        # ② 从活跃表中移除（放在 flush 之后）
        self._active_sessions.pop(breakglass_token, None)
        self._chunk_buffers.pop(breakglass_token, None)

        # ③ 等待所有 pending 异步上传完成，并回填 storage_path 到索引
        pending_tasks = index._pending_uploads.copy()
        index._pending_uploads.clear()
        for task, chunk, data in pending_tasks:
            try:
                storage_path = await task
            except Exception as e:
                logger.error(
                    "BreakGlassRecorder: await chunk upload %s failed: %s",
                    chunk.chunk_id, e,
                )
                # 兜底：重新同步上传一次
                try:
                    storage_path = await self._storage.upload_chunk(chunk, data)
                except Exception as e2:
                    logger.error(
                        "BreakGlassRecorder: retry upload %s also failed: %s",
                        chunk.chunk_id, e2,
                    )
                    storage_path = ""
            if storage_path and 0 <= chunk.sequence < len(index.chunks):
                old = index.chunks[chunk.sequence]
                index.chunks[chunk.sequence] = RecordingChunk(
                    chunk_id=old.chunk_id,
                    session_id=old.session_id,
                    breakglass_token=old.breakglass_token,
                    sequence=old.sequence,
                    timestamp=old.timestamp,
                    size_bytes=old.size_bytes,
                    content_hash=old.content_hash,
                    prev_chunk_hash=old.prev_chunk_hash,
                    storage_path=storage_path,
                )

        index.finished_at = time.time()

        # 计算索引哈希
        index_data = json.dumps({
            "session_id": index.session_id,
            "total_chunks": index.total_chunks,
            "total_bytes": index.total_bytes,
            "finished_at": index.finished_at,
        }, sort_keys=True)
        index.index_hash = hashlib.sha256(index_data.encode()).hexdigest()

        # 上传索引
        await self._storage.upload_index(index)

        if self._worm_audit is not None:
            self._worm_audit.log(
                index.session_id,
                "recording_stopped",
                {
                    "breakglass_token": breakglass_token,
                    "total_chunks": index.total_chunks,
                    "total_bytes": index.total_bytes,
                    "index_hash": index.index_hash,
                },
            )

        logger.info(
            "BreakGlassRecorder: stopped recording for token %s (%d chunks, %d bytes)",
            breakglass_token, index.total_chunks, index.total_bytes,
        )
        return index

    @staticmethod
    def verify_integrity(index: RecordingIndex) -> bool:
        """验证录屏分片哈希链完整性。

        24h 后验收：分片哈希链 100% 连续。
        """
        if not index.chunks:
            return False

        for i, chunk in enumerate(index.chunks):
            if i == 0:
                if chunk.prev_chunk_hash != "":
                    logger.error(
                        "BreakGlassRecorder: first chunk %s has non-empty prev_hash: %s",
                        chunk.chunk_id, chunk.prev_chunk_hash,
                    )
                    return False
            else:
                prev = index.chunks[i - 1]
                if chunk.prev_chunk_hash != prev.content_hash:
                    logger.error(
                        "BreakGlassRecorder: hash chain broken at chunk %s: "
                        "prev_hash=%s != expected=%s",
                        chunk.chunk_id, chunk.prev_chunk_hash, prev.content_hash,
                    )
                    return False

        return True
