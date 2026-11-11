from __future__ import annotations

import asyncio
from typing import Any

from prismclaw.schema.config import (
    DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    DEFAULT_KEEP_RECENT_TOKENS,
    DEFAULT_THRESHOLD,
)
from prismclaw.schema.agent.context import (
    BootstrapContextProvider,
    ContextEngine,
    MemoryAftermathBoundary,
    MemoryContextProvider,
)
from prismclaw.schema.llm.request_mode import RequestMode
from prismclaw.engine.compression.context_bundle import (
    ContextServiceBundle,
    build_context_service_bundle,
)
from prismclaw.engine.compression.memory_retriever import MemoryRetrievalSettings
from prismclaw.schema.agent.session import SessionContextState
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.config.config import CompactionConfig
from prismclaw.schema.content.compaction import AssembleResult, CompactResult
from prismclaw.schema.storage.storage_types import SessionStore
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.storage.storage_types import WorkspaceStore


__all__ = [
    "ContextEngine",
    "DefaultContextEngine",
]

# ContextEngine Protocol 已移入 schema/agent/context.py；本模块只保留
# DefaultContextEngine 具体实现。ContextEngineBuilder 与
# DIContainer 已删除（无外部调用者，生产装配直接构造 DefaultContextEngine）。


class DefaultContextEngine:
    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
        summarize: Any = None,
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
        retrieval_audit_log: object | None = None,
        aftermath_boundary: MemoryAftermathBoundary | None = None,
        attribution_recorder: object | None = None,
        edge_store: object | None = None,
        validation_ledger: object | None = None,
    ) -> None:
        self._services = build_context_service_bundle(
            threshold=threshold,
            keep_recent_tokens=keep_recent_tokens,
            summarize=summarize,
            compaction_timeout_s=compaction_timeout_s,
            chunk_token_budget=chunk_token_budget,
            workspace_store=workspace_store,
            bootstrap_files=bootstrap_files,
            memory_store=memory_store,
            memory_settings=memory_settings,
            compaction_config=compaction_config,
            session_store=session_store,
            llm_client=llm_client,
            evolution_settings=evolution_settings,
            retrieval_audit_log=retrieval_audit_log,
            aftermath_boundary=aftermath_boundary,
            attribution_recorder=attribution_recorder,
            edge_store=edge_store,
            validation_ledger=validation_ledger,
        )

    @property
    def service_bundle(self) -> ContextServiceBundle:
        return self._services

    @property
    def bootstrap_provider(self) -> BootstrapContextProvider:
        return self.service_bundle.bootstrap_provider

    @property
    def memory_provider(self) -> MemoryContextProvider:
        return self.service_bundle.memory_retriever

    @property
    def compaction_threshold(self) -> float:
        return self.service_bundle.history_compactor.threshold

    @property
    def keep_recent_tokens(self) -> int:
        return self.service_bundle.history_compactor.keep_recent_tokens

    def set_result_compaction_config(self, compaction_config: CompactionConfig | None) -> None:
        self.service_bundle.assemble_coordinator.set_compaction_config(compaction_config)

    def set_session_store(self, session_store: SessionStore | None) -> None:
        self.service_bundle.assemble_coordinator.set_session_store(session_store)
        self.service_bundle.memory_retriever.set_session_store(session_store)

    def set_ports(self, ports: Any) -> None:
        """注入依赖反转 ports，委托给 assemble_coordinator。"""
        self.service_bundle.assemble_coordinator.set_ports(ports)

    async def get_bootstrap(self, session_id: str) -> str | None:
        return await self.bootstrap_provider.get_bootstrap(session_id)

    async def get_l1_snapshot(self, session_id: str) -> list[Any]:
        return await self.memory_provider.get_l1_snapshot(session_id)

    def get_session_state(self, session_id: str) -> SessionContextState:
        return self.service_bundle.state_store.get_state(session_id)

    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[Any],
        token_budget: int | None = None,
        prompt: str | None = None,
        request_mode: RequestMode | None = None,
        task_spec: TaskSpec | None = None,
    ) -> AssembleResult:
        return await self.service_bundle.assemble_coordinator.assemble(
            session_id=session_id,
            messages=messages,
            token_budget=token_budget,
            prompt=prompt,
            request_mode=request_mode,
            task_spec=task_spec,
        )

    async def ingest(self, session_id: str, message: dict[str, Any]) -> None:
        await self.service_bundle.context_ingestor.ingest(session_id, message)

    async def compact(
        self,
        *,
        session_id: str,
        messages: list[Any],
        token_budget: int,
        force: bool = False,
        abort_event: asyncio.Event | None = None,
        model: str | None = None,
        threshold_override: float | None = None,
        keep_recent_tokens_override: int | None = None,
    ) -> CompactResult:
        return await self.service_bundle.history_compactor.compact(
            session_id=session_id,
            messages=messages,
            token_budget=token_budget,
            force=force,
            abort_event=abort_event,
            model=model,
            threshold_override=threshold_override,
            keep_recent_tokens_override=keep_recent_tokens_override,
        )

    async def after_turn(self, session_id: str, messages: list[Any]) -> None:
        normalized = [m for m in messages if isinstance(m, dict)]
        self.service_bundle.state_store.rebuild_from_messages(session_id, normalized)
        await self.service_bundle.memory_retriever.after_turn(session_id)

    async def consolidate_session(self, session_id: str) -> bool:
        return await self.service_bundle.memory_retriever.consolidate_session(session_id)
