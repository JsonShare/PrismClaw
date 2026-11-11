"""Retrieval audit Protocol definitions — behavioural contracts only.

Lives in contracts (L0) so that engine (L2) can depend on these interfaces
without circular imports.  See SDD §5.1.3.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from prismclaw.schema.audit.audit_types import RetrievalAuditEntry


@runtime_checkable
class RetrievalAuditLogger(Protocol):
    async def record(self, entry: RetrievalAuditEntry) -> None: ...
