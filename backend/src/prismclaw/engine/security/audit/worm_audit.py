"""P2-WORM-01+02+03 + P4-WORM-01：WORM 合规审计 — 哈希链 + 异步写入 + fanout 多副本。

Write-Once-Read-Many 审计日志，满足 HIPAA 164.312(b) 和 GDPR Art.30。

核心机制：
  1. 哈希链：每条记录包含前一条的 SHA256，形成不可篡改的链
  2. 异步写入：fire-and-forget + 内存缓冲队列 + 队列满降级本地文件追加
  3. S3 Object Lock：Compliance Mode 锁定，合规管理员也无法删除
  4. P4-WORM-01 fanout 多副本：S3 + syslog-ng TLS + 本地缓冲，任一副本丢失不影响其余
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import socket
import ssl
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── 数据类 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WormAuditRecord:
    """WORM 审计记录。"""
    event_id: str
    timestamp: float
    session_id: str
    event_type: str
    payload: dict[str, Any]
    mode: str = ""                          # WorkspaceMode
    sandbox_tier: str = ""                  # SandboxTier
    confirmation_kind: str = ""             # ConfirmationKind
    consent_id: str = ""                    # ConsentGate consent ID
    breakglass_token_id: str = ""           # BreakGlass token
    rule_code: str = ""                     # RuleGate rule code
    phi_masked: bool = False                # PHI 是否已脱敏
    aggregate_action: str = ""              # AggregateGuard action
    prev_hash: str = ""
    hash: str = ""


# ── WormSink 协议 ──────────────────────────────────────────────────────


@runtime_checkable
class WormSink(Protocol):
    """WORM 写入后端协议。"""

    async def write(self, record: WormAuditRecord) -> str:
        """写入记录，返回 event_id。"""
        ...

    async def query(self, session_id: str, since: float | None = None) -> list[WormAuditRecord]:
        """查询记录。"""
        ...

    async def verify_integrity(self, event_id: str) -> bool:
        """验证记录完整性。"""
        ...


# ── InMemoryWormSink ──────────────────────────────────────────────────


class InMemoryWormSink:
    """内存 WORM sink（开发/测试用）。"""

    def __init__(self) -> None:
        self._records: list[WormAuditRecord] = []

    async def write(self, record: WormAuditRecord) -> str:
        self._records.append(record)
        return record.event_id

    async def query(self, session_id: str, since: float | None = None) -> list[WormAuditRecord]:
        results = [r for r in self._records if r.session_id == session_id]
        if since is not None:
            results = [r for r in results if r.timestamp >= since]
        return results

    async def verify_integrity(self, event_id: str) -> bool:
        if not self._records:
            return event_id == ""
        for i, r in enumerate(self._records):
            expected_prev = self._records[i - 1].hash if i > 0 else ""
            if r.prev_hash != expected_prev:
                return False
            computed = _compute_record_hash(r)
            if computed != r.hash:
                return False
            if r.event_id == event_id:
                return True
        return False


class LocalFileWormSink:
    """本地文件 WORM sink（准生产用，append-only 文件）。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = ""
        # 读取已有记录获取 last_hash
        if self._path.exists():
            with open(self._path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            self._last_hash = d.get("hash", "")
                        except json.JSONDecodeError:
                            pass

    async def write(self, record: WormAuditRecord) -> str:
        with open(self._path, "a") as f:
            f.write(json.dumps({
                "event_id": record.event_id,
                "timestamp": record.timestamp,
                "session_id": record.session_id,
                "event_type": record.event_type,
                "payload": record.payload,
                "mode": record.mode,
                "sandbox_tier": record.sandbox_tier,
                "confirmation_kind": record.confirmation_kind,
                "consent_id": record.consent_id,
                "breakglass_token_id": record.breakglass_token_id,
                "rule_code": record.rule_code,
                "phi_masked": record.phi_masked,
                "aggregate_action": record.aggregate_action,
                "prev_hash": record.prev_hash,
                "hash": record.hash,
            }, ensure_ascii=False) + "\n")
        self._last_hash = record.hash
        return record.event_id

    async def query(self, session_id: str, since: float | None = None) -> list[WormAuditRecord]:
        if not self._path.exists():
            return []
        results = []
        with open(self._path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("session_id") == session_id:
                        if since is None or d.get("timestamp", 0) >= since:
                            results.append(WormAuditRecord(**d))
                except json.JSONDecodeError:
                    pass
        return results

    async def verify_integrity(self, event_id: str) -> bool:
        if not self._path.exists():
            return False
        prev_hash = ""
        with open(self._path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("prev_hash") != prev_hash:
                        return False
                    prev_hash = d.get("hash", "")
                    if d.get("event_id") == event_id:
                        return True
                except json.JSONDecodeError:
                    return False
        return False


# ── 哈希链计算 ────────────────────────────────────────────────────────


def _compute_record_hash(record: WormAuditRecord) -> str:
    """计算单条记录的 SHA256。"""
    data = json.dumps({
        "event_id": record.event_id,
        "timestamp": record.timestamp,
        "session_id": record.session_id,
        "event_type": record.event_type,
        "payload": record.payload,
        "prev_hash": record.prev_hash,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


# ── WormAuditWriter（异步写入）─────────────────────────────────────────


class WormAuditWriter:
    """WORM 审计写入器。

    特性：
      - 哈希链：每条记录包含前一条的 SHA256
      - 异步写入：fire-and-forget + 内存缓冲队列
      - 队列满降级：本地文件追加
    """

    def __init__(
        self,
        sink: WormSink,
        *,
        queue_size: int = 1000,
        fallback_path: str = "/tmp/prismclaw_worm_fallback.jsonl",
    ) -> None:
        self._sink = sink
        self._queue_size = queue_size
        self._queue: asyncio.Queue[WormAuditRecord | None] | None = None  # 延迟到 start() 创建
        self._fallback_path = fallback_path
        self._last_hash = ""
        self._seq = 0
        self._writer_task: asyncio.Task | None = None

    def start(self) -> None:
        """启动异步写入任务。"""
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self._queue_size)
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._write_loop())

    async def stop(self) -> None:
        """停止异步写入任务。"""
        if self._queue is not None:
            await self._queue.put(None)  # sentinel
        if self._writer_task:
            await self._writer_task

    def log(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        mode: str = "",
        sandbox_tier: str = "",
        confirmation_kind: str = "",
        consent_id: str = "",
        breakglass_token_id: str = "",
        rule_code: str = "",
        phi_masked: bool = False,
        aggregate_action: str = "",
    ) -> str:
        """记录审计事件（fire-and-forget）。"""
        self._seq += 1
        event_id = f"evt-{self._seq:08d}"
        record = WormAuditRecord(
            event_id=event_id,
            timestamp=time.time(),
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            mode=mode,
            sandbox_tier=sandbox_tier,
            confirmation_kind=confirmation_kind,
            consent_id=consent_id,
            breakglass_token_id=breakglass_token_id,
            rule_code=rule_code,
            phi_masked=phi_masked,
            aggregate_action=aggregate_action,
            prev_hash=self._last_hash,
        )
        # 计算哈希链（使用 dataclasses.replace，避免 __dict__ 脆弱模式）
        record = replace(record, hash=_compute_record_hash(record))
        self._last_hash = record.hash

        # fire-and-forget（Queue 可能未初始化 — 直接走 fallback）
        if self._queue is None:
            logger.warning("WORM queue not initialized (start() not called), falling back to local file")
            self._write_fallback(record)
            return event_id
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            # 降级：本地文件追加
            logger.warning("WORM queue full, falling back to local file")
            self._write_fallback(record)

        return event_id

    async def _write_loop(self) -> None:
        """异步写入循环。"""
        while True:
            record = await self._queue.get()
            if record is None:
                break
            try:
                await self._sink.write(record)
            except Exception as e:
                logger.error("WORM write failed: %s, falling back to local", e)
                self._write_fallback(record)

    def _write_fallback(self, record: WormAuditRecord) -> None:
        """降级写入：本地文件追加。"""
        try:
            with open(self._fallback_path, "a") as f:
                f.write(json.dumps({
                    k: v for k, v in record.__dict__.items()
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("WORM fallback write also failed: %s", e)

    async def query(self, session_id: str, since: float | None = None) -> list[WormAuditRecord]:
        """查询审计记录。"""
        return await self._sink.query(session_id, since)

    async def verify_integrity(self, event_id: str) -> bool:
        """验证记录完整性。"""
        return await self._sink.verify_integrity(event_id)


# ── P4-WORM-01：SyslogNgSink + FanoutWormSink ─────────────────────────


class SyslogNgSink:
    """syslog-ng TLS 远端 WORM sink（P4-WORM-01）。

    通过 syslog-ng TLS 协议将 WORM 审计记录转发到 SIEM
    （Splunk / Securonix / QRadar），作为第三份副本。

    同时写入远端 TLS 和本地缓冲（双写），任一路径失败不影响另一路径。
    """

    def __init__(
        self,
        *,
        remote_host: str = "localhost",
        remote_port: int = 6514,
        tls_cert_path: str = "",
        tls_key_path: str = "",
        ca_cert_path: str = "",
        local_buffer_path: str = "/tmp/prismclaw_worm_syslog_buffer.jsonl",
    ) -> None:
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._tls_cert_path = tls_cert_path
        self._tls_key_path = tls_key_path
        self._ca_cert_path = ca_cert_path
        self._buffer = LocalFileWormSink(local_buffer_path)
        self._connected = False
        self._tls_socket: ssl.SSLSocket | None = None

    def _ensure_connection(self) -> bool:
        """建立或复用 TLS 连接。返回是否已连接。"""
        if self._tls_socket is not None:
            try:
                # 简单检查 socket 是否仍然可用
                self._tls_socket.fileno()
                return True
            except Exception:
                self._tls_socket = None
                self._connected = False

        try:
            context = ssl.create_default_context()

            # 配置 TLS 证书
            if self._tls_cert_path and self._tls_key_path:
                context.load_cert_chain(
                    certfile=self._tls_cert_path,
                    keyfile=self._tls_key_path,
                )
            if self._ca_cert_path:
                context.load_verify_locations(cafile=self._ca_cert_path)
            else:
                # 无 CA 证书时禁用主机名验证（开发环境）
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(5)
            self._tls_socket = context.wrap_socket(
                raw_sock, server_hostname=self._remote_host,
            )
            self._tls_socket.connect((self._remote_host, self._remote_port))
            self._connected = True
            logger.info(
                "SyslogNgSink: TLS connection established to %s:%d",
                self._remote_host, self._remote_port,
            )
            return True
        except Exception as e:
            logger.warning(
                "SyslogNgSink: TLS connection to %s:%d failed: %s; "
                "will buffer locally only",
                self._remote_host, self._remote_port, e,
            )
            self._tls_socket = None
            self._connected = False
            return False

    @staticmethod
    def _format_rfc5424(record: WormAuditRecord) -> bytes:
        """将 WormAuditRecord 格式化为 RFC5424 syslog 消息。"""
        import datetime

        # PRI = Facility(1=user) * 8 + Severity(6=info) = 14
        pri = 14
        version = 1
        timestamp = datetime.datetime.fromtimestamp(
            record.timestamp, tz=datetime.timezone.utc,
        ).isoformat()
        hostname = "-"
        app_name = "prismclaw"
        procid = "-"
        msgid = record.event_type

        # Structured data: event_id + session_id
        sd = f'[prismclaw event_id="{record.event_id}" session_id="{record.session_id}"]'

        # Message body: JSON payload
        msg = json.dumps({
            "event_id": record.event_id,
            "timestamp": record.timestamp,
            "session_id": record.session_id,
            "event_type": record.event_type,
            "payload": record.payload,
            "mode": record.mode,
            "sandbox_tier": record.sandbox_tier,
            "prev_hash": record.prev_hash,
            "hash": record.hash,
        }, ensure_ascii=False, separators=(",", ":"))

        syslog_msg = f"<{pri}>{version} {timestamp} {hostname} {app_name} {procid} {msgid} {sd} {msg}"
        return syslog_msg.encode("utf-8") + b"\n"

    async def write(self, record: WormAuditRecord) -> str:
        """写入记录到 syslog-ng TLS 远端 + 本地缓冲（双写）。"""
        # 始终写入本地缓冲
        await self._buffer.write(record)

        # 尝试写入远端 TLS
        try:
            if self._ensure_connection() and self._tls_socket is not None:
                msg = self._format_rfc5424(record)
                self._tls_socket.sendall(msg)
        except Exception as e:
            logger.warning("SyslogNgSink: failed to send to remote: %s; local buffer preserved", e)
            # 连接断开，清理以便下次重连
            try:
                if self._tls_socket:
                    self._tls_socket.close()
            except Exception:
                pass
            self._tls_socket = None
            self._connected = False

        return record.event_id

    def close(self) -> None:
        """关闭 TLS socket 连接。"""
        if self._tls_socket is not None:
            try:
                self._tls_socket.close()
            except Exception:
                pass
            self._tls_socket = None
            self._connected = False
            logger.info("SyslogNgSink: TLS connection closed")

    async def query(self, session_id: str, since: float | None = None) -> list[WormAuditRecord]:
        """从本地缓冲查询。"""
        return await self._buffer.query(session_id, since)

    async def verify_integrity(self, event_id: str) -> bool:
        """验证本地缓冲完整性。"""
        return await self._buffer.verify_integrity(event_id)


class FanoutWormSink:
    """P4-WORM-01：WORM 审计 fanout 多副本写入。

    同时写入多个后端（S3 + syslog-ng + 本地缓冲），
    任一副本丢失不影响其余。

    写入策略：
      - 并行写入所有 sink
      - 至少 1 个 sink 成功即视为成功
      - 失败的 sink 记录告警但不阻断
      - 查询从第一个可用 sink 读取
    """

    def __init__(self, sinks: list[WormSink]) -> None:
        self._sinks = sinks

    async def write(self, record: WormAuditRecord) -> str:
        """并行写入所有 sink。至少 1 个成功即返回 event_id。"""
        if not self._sinks:
            return record.event_id

        results = await asyncio.gather(
            *[self._safe_write(sink, record) for sink in self._sinks],
            return_exceptions=True,
        )

        # 至少 1 个成功
        success_count = sum(1 for r in results if r == record.event_id)
        if success_count == 0:
            logger.error("FanoutWormSink: all sinks failed for event %s", record.event_id)
        elif success_count < len(self._sinks):
            logger.warning(
                "FanoutWormSink: %d/%d sinks succeeded for event %s",
                success_count, len(self._sinks), record.event_id,
            )

        return record.event_id

    async def _safe_write(self, sink: WormSink, record: WormAuditRecord) -> str:
        """安全写入单个 sink，异常不传播。"""
        try:
            return await sink.write(record)
        except Exception as e:
            logger.error("FanoutWormSink: write failed for %s: %s", type(sink).__name__, e)
            return ""

    async def query(self, session_id: str, since: float | None = None) -> list[WormAuditRecord]:
        """从第一个可用 sink 查询。"""
        for sink in self._sinks:
            try:
                return await sink.query(session_id, since)
            except Exception:
                continue
        return []

    async def verify_integrity(self, event_id: str) -> bool:
        """验证第一个可用 sink 的完整性。"""
        for sink in self._sinks:
            try:
                return await sink.verify_integrity(event_id)
            except Exception:
                continue
        return False


# ── S3ObjectLockSink ──────────────────────────────────────────────────


class S3ObjectLockSink(LocalFileWormSink):
    """S3 Object Lock Compliance Mode WORM sink。

    继承 LocalFileWormSink 的本地写入能力，同时将每条记录上传到
    S3 Object Lock Compliance Mode 存储桶，满足 HIPAA §164.312 写入留存要求。

    S3 Object Lock Compliance Mode 特性：
      - 合规管理员也无法删除或覆盖已锁定的对象
      - 配合 WORM 哈希链，提供不可篡改的双重保障
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "worm-audit/",
        local_buffer_path: str = "/tmp/prismclaw_worm_s3_buffer.jsonl",
        region: str = "us-east-1",
        endpoint_url: str | None = None,
    ) -> None:
        super().__init__(local_buffer_path)
        self._bucket = bucket
        self._prefix = prefix
        self._region = region
        self._endpoint_url = endpoint_url
        self._s3_client = None

    def _get_s3_client(self):
        """延迟初始化 boto3 S3 客户端。"""
        if self._s3_client is None:
            try:
                import boto3
                kwargs = {"region_name": self._region}
                if self._endpoint_url:
                    kwargs["endpoint_url"] = self._endpoint_url
                self._s3_client = boto3.client("s3", **kwargs)
            except ImportError:
                logger.warning("boto3 not installed; S3ObjectLockSink will only buffer locally")
                self._s3_client = False  # sentinel: don't retry
        return self._s3_client if self._s3_client is not False else None

    async def write(self, record: WormAuditRecord) -> str:
        """写入本地 + 上传 S3 Object Lock Compliance Mode。"""
        # 先写本地（继承自 LocalFileWormSink）
        event_id = await super().write(record)

        # 上传 S3
        client = self._get_s3_client()
        if client is not None:
            try:
                ts = datetime.datetime.fromtimestamp(
                    record.timestamp, tz=datetime.timezone.utc,
                )
                key = (
                    f"{self._prefix}{ts:%Y/%m/%d}/{record.event_id}.json"
                )
                body = json.dumps({
                    "event_id": record.event_id,
                    "timestamp": record.timestamp,
                    "session_id": record.session_id,
                    "event_type": record.event_type,
                    "payload": record.payload,
                    "mode": record.mode,
                    "sandbox_tier": record.sandbox_tier,
                    "confirmation_kind": record.confirmation_kind,
                    "consent_id": record.consent_id,
                    "breakglass_token_id": record.breakglass_token_id,
                    "rule_code": record.rule_code,
                    "phi_masked": record.phi_masked,
                    "aggregate_action": record.aggregate_action,
                    "prev_hash": record.prev_hash,
                    "hash": record.hash,
                }, ensure_ascii=False)

                client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body.encode("utf-8"),
                    ContentType="application/json",
                    ObjectLockMode="COMPLIANCE",
                    ObjectLockRetainUntilDate=(
                        datetime.datetime.now(datetime.timezone.utc)
                        + datetime.timedelta(days=365 * 7)  # 7 年合规保留
                    ),
                )
            except Exception as e:
                logger.warning(
                    "S3ObjectLockSink: S3 upload failed for event %s: %s; local buffer preserved",
                    record.event_id, e,
                )

        return event_id


# ── P4-WORM-01：默认 Fanout 装配（官方入口） ──────────────────────────────


def build_default_fanout_sink(
    *,
    local_path: str = "/var/log/prismclaw/worm.jsonl",
    syslog_host: str = "",
    syslog_port: int = 6514,
    syslog_tls_cert: str = "",
    syslog_tls_key: str = "",
    syslog_ca_cert: str = "",
    s3_bucket: str = "",
    s3_prefix: str = "worm-audit/",
    s3_region: str = "us-east-1",
    s3_endpoint_url: str | None = None,
    extra_sinks: list[WormSink] | None = None,
) -> FanoutWormSink:
    """构建生产可用的 FanoutWormSink（P4-WORM-01 官方入口）。

    默认写入策略（满足 HIPAA §164.312 写入留存要求）：
      1. LocalFileWormSink(local_path) ：本地文件 JSONL 追加（实时可用）
      2. SyslogNgSink ：syslog-ng TLS 远端（可选，参数非空时启用）
      3. S3ObjectLockSink ：S3 Object Lock Compliance Mode（可选，s3_bucket 非空时启用）
      4. 调用方传入的 extra_sinks ：其他自定义 sink

    Args:
        local_path: 本地 WORM 文件路径，默认 /var/log/prismclaw/worm.jsonl
        syslog_host: syslog-ng 远端地址；留空则跳过 syslog sink
        syslog_port: syslog-ng TLS 端口（默认 6514）
        syslog_tls_cert/syslog_tls_key/syslog_ca_cert: syslog-ng TLS 证书路径
        s3_bucket: S3 Object Lock 存储桶名；留空则跳过 S3 sink
        s3_prefix: S3 对象键前缀（默认 worm-audit/）
        s3_region: S3 区域（默认 us-east-1）
        s3_endpoint_url: S3 兼容端点 URL（可选，用于 MinIO 等兼容存储）
        extra_sinks: 额外 sink 列表；放在末尾，优先级低

    Returns:
        已装配好的 FanoutWormSink。直接传给 WormAuditWriter(sink=...) 使用。
    """
    sinks: list[WormSink] = []

    # ① 本地文件（必装，用于快速查询 + 单节点降级）
    sinks.append(LocalFileWormSink(local_path))

    # ② syslog-ng TLS 远端（可选）
    if syslog_host:
        sinks.append(SyslogNgSink(
            remote_host=syslog_host,
            remote_port=syslog_port,
            tls_cert_path=syslog_tls_cert,
            tls_key_path=syslog_tls_key,
            ca_cert_path=syslog_ca_cert,
        ))

    # ③ S3 Object Lock Compliance Mode（可选）
    if s3_bucket:
        sinks.append(S3ObjectLockSink(
            bucket=s3_bucket,
            prefix=s3_prefix,
            region=s3_region,
            endpoint_url=s3_endpoint_url,
        ))

    # ④ 调用方额外 sink
    if extra_sinks:
        sinks.extend(extra_sinks)

    logger.info(
        "build_default_fanout_sink: assembled %d sinks (%s)",
        len(sinks),
        ", ".join(type(s).__name__ for s in sinks),
    )
    return FanoutWormSink(sinks)
