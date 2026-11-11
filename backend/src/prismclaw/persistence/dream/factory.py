from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.dream import (
    DreamCandidateStore,
    DreamStateStore,
    DreamAuditLogger,
    DreamGovernanceStore,
    DreamDecisionAuditStore,
)


@dataclass
class DreamStores:
    candidate_store: DreamCandidateStore
    state_store: DreamStateStore
    audit_logger: DreamAuditLogger
    governance_store: DreamGovernanceStore
    decision_audit_store: DreamDecisionAuditStore


def create_dream_stores(session_factory: async_sessionmaker[AsyncSession]) -> DreamStores:
    from prismclaw.persistence.dream.candidates import PostgresDreamCandidateStore
    from prismclaw.persistence.dream.state_store import PostgresDreamStateStore
    from prismclaw.persistence.dream.audit import PostgresDreamAuditLog
    from prismclaw.persistence.dream.governance import PostgresDreamGovernanceStore
    from prismclaw.persistence.dream.decision_audit import PostgresDreamDecisionAuditStore

    return DreamStores(
        candidate_store=PostgresDreamCandidateStore(session_factory),
        state_store=PostgresDreamStateStore(session_factory),
        audit_logger=PostgresDreamAuditLog(session_factory),
        governance_store=PostgresDreamGovernanceStore(session_factory),
        decision_audit_store=PostgresDreamDecisionAuditStore(session_factory),
    )
