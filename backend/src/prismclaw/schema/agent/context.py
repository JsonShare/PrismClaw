from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.llm.request_mode import RequestMode
from prismclaw.schema.storage.storage_types import SessionStore

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.config.config import CompactionConfig
from prismclaw.schema.content.compaction import AssembleResult, CompactResult
from prismclaw.schema.agent.session import (
    MessageEntry,
    SessionContextState,
)
from prismclaw.schema.agent.task_types import TaskSpec


@runtime_checkable
class BootstrapContextProvider(Protocol):
    async def get_bootstrap(self, session_id: str) -> str | None: ...


@runtime_checkable
class MemoryContextProvider(Protocol):
    async def get_l1_snapshot(self, session_id: str) -> list[MessageEntry]: ...

    # SDD audit fix: the rotation boundary calls this as the canonical
    # session-end consolidation (chat-fact flush + archive + dream enqueue).
    # The retriever always implemented it; the contract just never declared
    # it — duck typing worked, typed callers would have failed.
    async def consolidate_session(self, session_id: str) -> bool: ...


@runtime_checkable
class HasBootstrapProvider(Protocol):
    @property
    def bootstrap_provider(self) -> BootstrapContextProvider: ...


@runtime_checkable
class HasMemoryProvider(Protocol):
    @property
    def memory_provider(self) -> MemoryContextProvider: ...


@runtime_checkable
class ContextAssembler(Protocol):
    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[MessageEntry],
        token_budget: int | None = None,
        prompt: str | None = None,
        request_mode: RequestMode | None = None,
        task_spec: TaskSpec | None = None,
    ) -> AssembleResult: ...


@runtime_checkable
class ContextCompactor(Protocol):
    async def compact(
        self,
        *,
        session_id: str,
        messages: list[MessageEntry],
        token_budget: int,
        force: bool = False,
        abort_event: asyncio.Event | None = None,
        model: str | None = None,
        threshold_override: float | None = None,
        keep_recent_tokens_override: int | None = None,
    ) -> CompactResult: ...


@runtime_checkable
class ContextIngestor(Protocol):
    async def ingest(self, session_id: str, message: dict[str, Any]) -> None: ...


@runtime_checkable
class ContextTurnFinalizer(Protocol):
    async def after_turn(self, session_id: str, messages: list[MessageEntry]) -> None: ...


@runtime_checkable
class MemoryAftermathBoundary(Protocol):
    async def after_turn(self, session_id: str) -> None: ...

    async def consolidate_session(self, session_id: str) -> bool: ...

    def set_session_store(self, session_store: SessionStore) -> None: ...


@runtime_checkable
class HasCompactionSettings(Protocol):
    @property
    def compaction_threshold(self) -> float: ...

    @property
    def keep_recent_tokens(self) -> int: ...


@runtime_checkable
class ContextEngine(
    HasBootstrapProvider,
    HasMemoryProvider,
    HasCompactionSettings,
    ContextAssembler,
    ContextCompactor,
    ContextIngestor,
    ContextTurnFinalizer,
    Protocol,
):
    """上下文引擎的统一契约。

    从 subsystems/context/context_engine.py L49-63 移入。
    agent 层只导入此 Protocol，不导入 DefaultContextEngine 具体实现。
    """
    def set_result_compaction_config(self, compaction_config: CompactionConfig | None) -> None: ...
    def set_session_store(self, session_store: SessionStore | None) -> None: ...
    def set_ports(self, ports: Any) -> None: ...
    def get_session_state(self, session_id: str) -> SessionContextState: ...
    async def consolidate_session(self, session_id: str) -> bool: ...
