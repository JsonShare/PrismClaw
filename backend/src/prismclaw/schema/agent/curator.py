"""Curator Protocol definitions — behavioural contracts only.

Lives in contracts (L0) so that engine (L2) can depend on these interfaces
without circular imports.  See SDD §5.1.5.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prismclaw.schema.agent.curator_types import ArchivedSopRow, ReviewOutcome, SopRow

if TYPE_CHECKING:
    from prismclaw.infra.settings._memory import CuratorSettings
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.memory import MemoryEntry


class CuratorL1Index(Protocol):
    """curator 只需从 L1 索引淘汰条目，不关心底层是 Redis 还是内存实现。"""

    async def index_get(self, session_key: str) -> list[MemoryEntry]: ...

    async def index_remove(self, session_key: str, entry_id: str) -> None: ...


@runtime_checkable
class CuratorSopRepository(Protocol):
    async def list_active_sop_sessions(self) -> list[str]: ...

    async def scan_session(
        self,
        session_key: str,
        archive_days: int,
        l1_index: CuratorL1Index,
        workspace_base_dir: Path | None = None,
        settings: CuratorSettings | None = None,
        count_validation_signals: Callable[[str, list[str]], Awaitable[dict[str, dict[str, int]]]]
        | None = None,
    ) -> tuple[int, int]: ...

    async def review_session(
        self,
        session_key: str,
        settings: CuratorSettings,
        l1_index: CuratorL1Index,
        workspace_base_dir: Path,
        llm_client: LLMClient,
        *,
        check_alive: Callable[[], None] = lambda: None,
        abort_event: asyncio.Event | None = None,
    ) -> ReviewOutcome: ...

    async def list_auto_sops(
        self,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]: ...

    async def list_stale_sops(
        self,
        settings: CuratorSettings,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]: ...

    async def list_archived_sops(
        self,
        *,
        session_key: str | None = None,
    ) -> list[ArchivedSopRow]: ...

    async def preview_graduation(
        self,
        settings: CuratorSettings,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]: ...

    async def restore_sop(
        self,
        entry_id: str,
        *,
        session_key: str | None = None,
    ) -> int: ...
