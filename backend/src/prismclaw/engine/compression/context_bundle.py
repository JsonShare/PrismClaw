from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.schema.config import (
    DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    DEFAULT_KEEP_RECENT_TOKENS,
    DEFAULT_THRESHOLD,
)
from prismclaw.schema.agent.context import (
    ContextIngestor,
    MemoryAftermathBoundary,
)
from prismclaw.schema.audit.audit import RetrievalAuditLogger
from prismclaw.engine.compression.assemble_coordinator import AssembleCoordinator
from prismclaw.engine.compression.bootstrap import BootstrapProvider
from prismclaw.engine.compression.history_compactor import HistoryCompactor
from prismclaw.engine.compression.memory_formatter import MemoryFormatter
from prismclaw.engine.compression.memory_retriever import (
    MemoryRetrievalSettings,
    MemoryRetriever,
)
from prismclaw.engine.compression.state_store import SessionStateStore
from prismclaw.engine.compression.summary_engine import SummarizerCallable, SummaryEngine
from prismclaw.engine.compression.task_profile_inference import TaskProfileInference
from prismclaw.schema.config.config import CompactionConfig
from prismclaw.schema.storage.storage_types import SessionStore
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.storage.storage_types import WorkspaceStore


@dataclass(slots=True)
class ContextServiceBundle:
    bootstrap_provider: BootstrapProvider
    memory_retriever: MemoryRetriever
    retrieval_audit_log: RetrievalAuditLogger | None
    state_store: SessionStateStore
    assemble_coordinator: AssembleCoordinator
    summary_engine: SummaryEngine
    history_compactor: HistoryCompactor
    context_ingestor: ContextIngestor
    edge_store: Any = None


def build_context_service_bundle(
    *,
    threshold: float = DEFAULT_THRESHOLD,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
    summarize: SummarizerCallable | None = None,
    compaction_timeout_s: float = DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    chunk_token_budget: int = 8_000,
    workspace_store: WorkspaceStore | None = None,
    bootstrap_files: list[str] | None = None,
    memory_store: MemoryStore | None = None,
    memory_settings: MemoryRetrievalSettings | None = None,
    compaction_config: CompactionConfig | None = None,
    session_store: SessionStore | None = None,
    llm_client: object | None = None,
    evolution_settings: object | None = None,
    retrieval_audit_log: RetrievalAuditLogger | None = None,
    aftermath_boundary: MemoryAftermathBoundary | None = None,
    attribution_recorder: object | None = None,
    edge_store: Any = None,
    validation_ledger: object | None = None,
) -> ContextServiceBundle:
    if memory_store is not None and memory_settings is None:
        raise ValueError("memory_settings must be provided when memory_store is set")

    bootstrap_provider = BootstrapProvider(
        workspace_store=workspace_store,
        bootstrap_files=bootstrap_files,
    )
    memory_retriever = MemoryRetriever(
        memory_store=memory_store,
        memory_settings=memory_settings,
        session_store=session_store,
        llm_client=llm_client,
        evolution_settings=evolution_settings,
        retrieval_audit_log=retrieval_audit_log,
        aftermath_boundary=aftermath_boundary,
        attribution_recorder=attribution_recorder,
        edge_store=edge_store,
        validation_ledger=validation_ledger,
    )
    state_store = SessionStateStore()
    assemble_coordinator = AssembleCoordinator(
        memory_retriever=memory_retriever,
        state_store=state_store,
        memory_formatter=MemoryFormatter(),
        task_profile_inference=TaskProfileInference(),
        compaction_config=compaction_config,
        session_store=session_store,
    )
    summary_engine = SummaryEngine(
        summarize=summarize,
        compaction_timeout_s=compaction_timeout_s,
        chunk_token_budget=chunk_token_budget,
        # SDD context-governance W11: forward the compaction config so the
        # configured 10-section template (incl. the W8 "user-stated facts"
        # carry-forward section) is used instead of the hard-coded fallback
        # prompt when the LLM summarizer is wired.
        compaction_config=compaction_config,
    )
    history_compactor = HistoryCompactor(
        threshold=threshold,
        keep_recent_tokens=keep_recent_tokens,
        summary_engine=summary_engine,
    )
    context_ingestor = StateContextIngestor(state_store)
    return ContextServiceBundle(
        bootstrap_provider=bootstrap_provider,
        memory_retriever=memory_retriever,
        retrieval_audit_log=retrieval_audit_log,
        state_store=state_store,
        assemble_coordinator=assemble_coordinator,
        summary_engine=summary_engine,
        history_compactor=history_compactor,
        context_ingestor=context_ingestor,
        edge_store=edge_store,
    )


class NoopContextIngestor:
    async def ingest(self, session_id: str, message: dict[str, Any]) -> None:
        del session_id, message
        return None


class StateContextIngestor:
    def __init__(self, state_store: SessionStateStore) -> None:
        self._state_store = state_store

    async def ingest(self, session_id: str, message: dict[str, Any]) -> None:
        self._state_store.ingest_message(session_id, message)
