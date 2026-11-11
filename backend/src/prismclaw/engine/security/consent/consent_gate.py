"""P2-SEC-06：ConsentGate 基础版 — 声明驱动的同意管理。

HIPAA 164.508 / GDPR Art.6 合规：工具必须声明用途（declared_purposes），
用户必须同意后才能访问 PHI/敏感数据。

核心概念：
  - declared_purposes: 工具声明用途列表（如 ["prescription_submission", "phi_access"]）
  - ConsentRecord: 单次同意记录（user_id + purpose + tool_name + granted + scope）
  - ConsentGate.check(): 检查是否已有有效同意

Phase 2 基础版使用 InMemoryConsentStore。
Phase 2b 替换为 PostgresConsentStore。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from prismclaw.engine.security.gate.types import ConsentStoreProtocol


@dataclass(frozen=True)
class ConsentRecord:
    """单次同意记录。"""
    consent_id: str
    session_id: str
    user_id: str
    purpose: str
    tool_name: str
    granted: bool
    scope: str = ""  # 数据范围限定（如 "phi_read_only", "prescription_submit"）
    workspace_id: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None  # None=永不过期

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


@dataclass
class ConsentCheckResult:
    """ConsentGate 检查结果。"""
    allowed: bool
    reason: str = ""
    consent_id: str = ""
    missing_purposes: list[str] = field(default_factory=list)


class InMemoryConsentStore(ConsentStoreProtocol):
    """内存同意存储（开发/测试用）。"""

    def __init__(self) -> None:
        self._records: dict[str, ConsentRecord] = {}

    def record_consent(self, session_id: str, purpose: str, tool_name: str, granted: bool, *, consent_id: str = "") -> None:
        record = ConsentRecord(
            consent_id=consent_id or self.generate_consent_id(),
            session_id=session_id,
            user_id="",  # 由 ConsentGate 填充
            purpose=purpose,
            tool_name=tool_name,
            granted=granted,
        )
        self._records[record.consent_id] = record

    def has_consent(self, session_id: str, purpose: str, tool_name: str) -> bool:
        for r in self._records.values():
            if (r.session_id == session_id
                    and r.purpose == purpose
                    and (r.tool_name == tool_name or r.tool_name == "*")
                    and r.granted
                    and not r.is_expired):
                return True
        return False

    def grant(self, *, session_id: str, user_id: str, purpose: str, tool_name: str,
              scope: str = "", workspace_id: str = "", ttl_seconds: int | None = None) -> ConsentRecord:
        """授予同意。"""
        record = ConsentRecord(
            consent_id=self.generate_consent_id(),
            session_id=session_id,
            user_id=user_id,
            purpose=purpose,
            tool_name=tool_name,
            granted=True,
            scope=scope,
            workspace_id=workspace_id,
            expires_at=time.time() + ttl_seconds if ttl_seconds else None,
        )
        self._records[record.consent_id] = record
        return record

    def list_active(self, session_id: str, user_id: str) -> list[ConsentRecord]:
        """列出 session 下所有有效的同意记录。"""
        return [
            r for r in self._records.values()
            if r.session_id == session_id and r.user_id == user_id and r.granted and not r.is_expired
        ]

    def revoke(self, consent_id: str) -> None:
        """撤销同意。"""
        r = self._records.get(consent_id)
        if r:
            self._records[consent_id] = ConsentRecord(
                consent_id=r.consent_id,
                session_id=r.session_id,
                user_id=r.user_id,
                purpose=r.purpose,
                tool_name=r.tool_name,
                granted=False,
                scope=r.scope,
                workspace_id=r.workspace_id,
                created_at=r.created_at,
                expires_at=r.expires_at,
            )


class ConsentGate:
    """声明驱动同意管理。

    工具必须声明用途（declared_purposes），用户同意后才能访问 PHI/敏感数据。
    未授权使用 → deny `consent_not_found`。
    未声明用途 → deny `no_declared_purpose`。
    """

    def __init__(self, store: ConsentStoreProtocol | None = None) -> None:
        self._store = store or InMemoryConsentStore()

    def check(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_name: str,
        declared_purposes: list[str],
        workspace_id: str = "",
    ) -> ConsentCheckResult:
        """检查是否已有有效同意。"""
        if not declared_purposes:
            return ConsentCheckResult(
                allowed=False,
                reason="no_declared_purpose",
            )

        missing: list[str] = []
        for purpose in declared_purposes:
            if not self._store.has_consent(session_id, purpose, tool_name):
                missing.append(purpose)

        if missing:
            return ConsentCheckResult(
                allowed=False,
                reason="consent_not_found",
                missing_purposes=missing,
            )

        return ConsentCheckResult(
            allowed=True,
            reason="consent_granted",
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
    ) -> ConsentRecord:
        """授予同意。统一委托 store.grant()，不再按 store 类型分支。"""
        return self._store.grant(
            session_id=session_id,
            user_id=user_id,
            purpose=purpose,
            tool_name=tool_name,
            scope=scope,
            workspace_id=workspace_id,
            ttl_seconds=ttl_seconds,
        )

    def revoke(self, consent_id: str) -> None:
        """撤销同意。"""
        self._store.revoke(consent_id)

    def list_active_consents(self, session_id: str, user_id: str) -> list[ConsentRecord]:
        """列出 session 下所有有效的同意记录。"""
        return self._store.list_active(session_id, user_id)

    def get_purpose_consistency_prompt(self, session_id: str, user_id: str) -> str:
        """生成 LLM 上下文注入的「目的一致性」提示。

        HIPAA 164.508 和 GDPR Art.6 的合规要求：
        LLM 每轮必须了解当前 session 下的有效同意及其使用目的。

        注入格式：
          [CONSENT INJECTION: 当前数据使用目的限于 {purpose 列表}]
          [CONSENT TIP: 禁止使用未在 purpose 列表中声明的数据用途，否则 ConsentGate 会阻断。]
        """
        records = self.list_active_consents(session_id, user_id)
        if not records:
            return ""
        # 按 purpose 去重，保留唯一集合
        purposes: set[str] = set()
        for r in records:
            purposes.add(r.purpose)
        if not purposes:
            return ""
        sorted_purposes = sorted(purposes)
        lines = [
            "[CONSENT INJECTION: 当前数据使用目的限于 "
            + ", ".join(sorted_purposes)
            + "]",
            "[CONSENT TIP: 工具访问 PHI 前必须声明用途；禁止访问未在上述目的列表中声明的数据用途。"
            "若 LLM 尝试声明未授权的用途，ConsentGate 将自动阻断并写入 WORM 审计。]",
        ]
        return "\n".join(lines)
