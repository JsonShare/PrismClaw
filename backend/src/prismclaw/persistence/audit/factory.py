from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.audit.audit import RetrievalAuditLogger


def create_audit_logger(
    session_factory: async_sessionmaker[AsyncSession],
) -> RetrievalAuditLogger:
    from prismclaw.persistence.audit.retrieval import PostgresRetrievalAuditLog

    return PostgresRetrievalAuditLog(session_factory)
