"""P10-SEC-03：RedisConsentStore — 跨实例共享的同意存储。

替代 ConsentGate 默认的 InMemoryConsentStore，使 B/S 多实例部署下
同意记录可在所有 worker 间共享（医疗/法律 PHI 场景强制要求）。

存储模型：
  每条同意记录存为 Redis key：
    consent:{session_id}:{purpose}:{tool_name}  →  JSON(ConsentRecord 字段)
  带 TTL（由 expires_at 推导），自动过期清理。

设计要点：
  - 同步 redis 客户端：ConsentGate.check/grant 当前是同步 API，
    PHI 场景触发频率低，同步阻塞可接受；避免为低频路径全局异步化。
  - 未配置/连接失败 → fail-closed：has_consent 返回 False（拒绝而非放行），
    避免 Redis 不可用时静默放行 PHI 访问。
  - grant/revoke 与原 InMemoryConsentStore 语义对齐，ConsentGate 无需分支。
"""
from __future__ import annotations

import json
import logging
import os
import time

from prismclaw.engine.security.consent.consent_gate import ConsentRecord
from prismclaw.engine.security.gate.types import ConsentStoreProtocol

logger = logging.getLogger(__name__)


def _key(session_id: str, purpose: str, tool_name: str) -> str:
    return f"consent:{session_id}:{purpose}:{tool_name}"


class RedisConsentStore(ConsentStoreProtocol):
    """基于 Redis 的同意存储（生产用）。"""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.getenv("PRISMCLAW_REDIS_URL") or ""
        self._client = None
        if self._redis_url:
            import redis

            self._client = redis.Redis.from_url(
                self._redis_url, socket_timeout=2.0, socket_connect_timeout=2.0,
            )
        else:
            logger.warning(
                "RedisConsentStore initialized without redis_url; "
                "consent checks will fail-closed (deny) until configured"
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    def _get_client(self):
        if self._client is None:
            # fail-closed：无客户端时调用方应处理为拒绝
            raise RuntimeError("RedisConsentStore not configured (no redis_url)")
        return self._client

    def record_consent(
        self, session_id: str, purpose: str, tool_name: str, granted: bool, *, consent_id: str = ""
    ) -> None:
        self.grant(
            session_id=session_id, user_id="", purpose=purpose, tool_name=tool_name,
            granted=granted, consent_id=consent_id,
        )

    def grant(
        self,
        *,
        session_id: str,
        user_id: str,
        purpose: str,
        tool_name: str,
        scope: str = "",
        workspace_id: str = "",
        ttl_seconds: int | None = None,
        granted: bool = True,
        consent_id: str = "",
    ) -> ConsentRecord:
        """授予（或撤销，granted=False）同意，写入 Redis。"""
        cid = consent_id or self.generate_consent_id()
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        record = ConsentRecord(
            consent_id=cid,
            session_id=session_id,
            user_id=user_id,
            purpose=purpose,
            tool_name=tool_name,
            granted=granted,
            scope=scope,
            workspace_id=workspace_id,
            expires_at=expires_at,
        )
        client = self._get_client()
        key = _key(session_id, purpose, tool_name)
        # 撤销时仍写入记录（granted=False）保持审计可追溯；TTL 延长至 30d 留存
        ttl = ttl_seconds or (30 * 86_400 if not granted else None)
        client.set(key, json.dumps(record.__dict__, default=str), ex=ttl)
        return record

    def has_consent(self, session_id: str, purpose: str, tool_name: str) -> bool:
        """检查有效同意。fail-closed：任何异常/未配置均返回 False。"""
        if self._client is None:
            logger.error("RedisConsentStore.has_consent: not configured → fail-closed deny")
            return False
        try:
            client = self._client
            # 精确工具名匹配
            exact = client.get(_key(session_id, purpose, tool_name))
            if exact is not None:
                rec = self._parse(exact)
                if rec is not None and rec.granted and not rec.is_expired:
                    return True
            # 通配工具名 "*" 匹配（该 purpose 已对所有工具授予）
            wild = client.get(_key(session_id, purpose, "*"))
            if wild is not None:
                rec = self._parse(wild)
                if rec is not None and rec.granted and not rec.is_expired:
                    return True
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConsentStore.has_consent error (fail-closed): %s", exc)
            return False

    def list_active(self, session_id: str, user_id: str) -> list[ConsentRecord]:
        """列出 session 下所有有效同意。fail-closed：异常返回空列表。"""
        if self._client is None:
            return []
        try:
            client = self._client
            records: list[ConsentRecord] = []
            pattern = f"consent:{session_id}:*"
            for key in client.scan_iter(match=pattern):
                raw = client.get(key)
                if raw is None:
                    continue
                rec = self._parse(raw)
                if rec is None:
                    continue
                if rec.session_id == session_id and rec.user_id == user_id and rec.granted and not rec.is_expired:
                    records.append(rec)
            return records
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConsentStore.list_active error: %s", exc)
            return []

    def revoke(self, consent_id: str) -> None:
        """撤销：按 consent_id 扫描并标记 granted=False。"""
        if self._client is None:
            return
        try:
            client = self._client
            pattern = "consent:*"
            for key in client.scan_iter(match=pattern):
                raw = client.get(key)
                if raw is None:
                    continue
                rec = self._parse(raw)
                if rec is not None and rec.consent_id == consent_id:
                    self.grant(
                        session_id=rec.session_id, user_id=rec.user_id,
                        purpose=rec.purpose, tool_name=rec.tool_name,
                        scope=rec.scope, workspace_id=rec.workspace_id,
                        granted=False, consent_id=rec.consent_id,
                    )
                    return
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConsentStore.revoke error: %s", exc)

    @staticmethod
    def _parse(raw: bytes | str) -> ConsentRecord | None:
        try:
            data = json.loads(raw)
            return ConsentRecord(
                consent_id=data.get("consent_id", ""),
                session_id=data.get("session_id", ""),
                user_id=data.get("user_id", ""),
                purpose=data.get("purpose", ""),
                tool_name=data.get("tool_name", ""),
                granted=bool(data.get("granted", False)),
                scope=data.get("scope", ""),
                workspace_id=data.get("workspace_id", ""),
                created_at=float(data.get("created_at", 0.0)),
                expires_at=(float(data["expires_at"]) if data.get("expires_at") is not None else None),
            )
        except Exception:  # noqa: BLE001
            return None
