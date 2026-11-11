"""Evidence ledger helpers — pure functions on EvidenceRecord."""

from __future__ import annotations

from prismclaw.schema.agent.task_runtime import EvidenceRecord


def is_recovery_probe_evidence(record: EvidenceRecord) -> bool:
    return record.tool_name == "recovery_probe" and record.origin == "system"
