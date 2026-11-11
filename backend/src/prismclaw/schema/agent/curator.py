"""Curator Protocol definitions — behavioural contracts only.

Lives in contracts (L0) so that engine (L2) can depend on these interfaces
without circular imports.  See SDD §5.1.5.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.agent.curator_types import ArchivedSopRow, ReviewOutcome, SopRow


@runtime_checkable
class CuratorSopRepository(Protocol):
    async def list_active_sop_sessions(self) -> list[str]: ...

    async def scan_session(
        self,
        session_key: str,
        archive_days: int,
        l1_index: Any,
        workspace_base_dir: Path | None = None,
        settings: Any = None,
    ) -> tuple[int, int]: ...

    async def review_session(
        self,
        session_key: str,
        settings: Any,
        l1_index: Any,
        workspace_base_dir: Path,
        llm_client: Any,
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
        settings: Any,
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
        settings: Any,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]: ...

    async def restore_sop(
        self,
        entry_id: str,
        *,
        session_key: str | None = None,
    ) -> int: ...
