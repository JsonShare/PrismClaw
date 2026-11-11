"""consent 子域 — 声明驱动同意管理。"""
from prismclaw.engine.security.consent.consent_gate import (
    ConsentRecord,
    ConsentCheckResult,
    InMemoryConsentStore,
    ConsentGate,
)
from prismclaw.engine.security.consent.redis_consent_store import RedisConsentStore

__all__ = [
    "ConsentRecord",
    "ConsentCheckResult",
    "InMemoryConsentStore",
    "ConsentGate",
    "RedisConsentStore",
]
