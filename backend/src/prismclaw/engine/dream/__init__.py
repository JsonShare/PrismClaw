from __future__ import annotations

from .candidates import (
    DreamCandidateCollector,
)
from .candidates import (
    DreamCandidateStore as DreamCandidateStore,
)
from .candidates import (
    DreamMemoryCandidate as DreamMemoryCandidate,
)
from .dispatch import (
    DistributedDreamLease,
    DistributedDreamLockProvider,
    DreamBoundary,
    DreamDispatchConsumer,
    DreamDispatchJob,
    DreamEnqueueBoundary,
    DreamEnqueueResult,
    DreamExecutionLease,
    DreamLockProvider,
    DreamScheduler,
    RedisDreamDispatchQueue,
    build_light_dispatch_job,
    build_nightly_dispatch_job,
)
from .engine import DreamEngine
from .governance import (
    DreamAdminOps,
    DreamDecisionAuditRecord,
    DreamGovernanceRecord,
    DreamGovernanceRuntime,
    DreamGovernanceStore,
    DreamStatusOps,
    InProcessDreamAdminOps,
)

# DreamReviewService 与 engine.py 循环依赖，不在此层导出；
# 需直接 from prismclaw.engine.dream.governance.review_service import DreamReviewService
from .state import (
    DreamAuditEntry,
    DreamAuditLogger,
    DreamSessionState,
    DreamStateStore,
    DreamStatusService,
)

__all__ = [
    "DistributedDreamLease",
    "DistributedDreamLockProvider",
    "DreamAdminOps",
    "DreamAuditEntry",
    "DreamAuditLogger",
    "DreamBoundary",
    "DreamCandidateCollector",
    "DreamCandidateStore",
    "DreamDecisionAuditRecord",
    "DreamDispatchConsumer",
    "DreamDispatchJob",
    "DreamEnqueueBoundary",
    "DreamEnqueueResult",
    "DreamExecutionLease",
    "DreamGovernanceRecord",
    "DreamGovernanceRuntime",
    "DreamGovernanceStore",
    "DreamEngine",
    "DreamLockProvider",
    "DreamMemoryCandidate",
    "DreamScheduler",
    "DreamSessionState",
    "DreamStateStore",
    "DreamStatusOps",
    "DreamStatusService",
    "InProcessDreamAdminOps",
    "RedisDreamDispatchQueue",
    "build_light_dispatch_job",
    "build_nightly_dispatch_job",
]
