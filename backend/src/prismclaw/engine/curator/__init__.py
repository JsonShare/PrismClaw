"""Curator background loop — archives stale memory entries via SETNX distributed lock.

SOPs (``type='auto_sop'``) live in the shared ``pc_memory_entries`` Postgres table.
The curator scans per ``session_key``, archives stale SOPs (``status='archived'``)
and graduates high-use ones (``status='graduated'``) into SKILL.md files.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from prismclaw.engine.curator.state import (
    CuratorStateStore,
)
from prismclaw.schema.agent.curator import CuratorL1Index, CuratorSopRepository
from prismclaw.schema.agent.curator_types import (
    CuratorReport as CuratorReport,
)
from prismclaw.schema.agent.curator_types import (
    CycleReport as CycleReport,
)
from prismclaw.schema.agent.curator_types import (
    ReviewOutcome as ReviewOutcome,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from prismclaw.infra.settings._memory import CuratorSettings
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.services.lock import LockManagerProtocol
    from prismclaw.schema.services.task_protocol import TaskManagerProtocol

logger = logging.getLogger(__name__)


CycleError = Literal["lock_lost", "review_skipped_interval"] | None

# SDD A2 (defect D3, docs/memory-dream-sdd.md §1.1): review inputs are never
# truncated per-entry — the LLM must see each SOP in full to judge promote/
# archive. Prompt budget is controlled at the BATCH level instead (implemented
# in persistence/curator/repository.py): entries are appended whole until the
# budget is exhausted; the remainder stays for the next cycle (curator rescans
# by use_count DESC).

CURATOR_CYCLE_LOCK_KEY = "curator:cycle"
SCAN_CONCURRENCY = 10


async def run_curator_cycle(
    *,
    sop_repo: CuratorSopRepository,
    settings: CuratorSettings,
    redis_client: aioredis.Redis,
    lock_manager: LockManagerProtocol,
    task_manager: TaskManagerProtocol,
    l1_index: CuratorL1Index,
    workspace_base_dir: Path | None = None,
    llm_client: LLMClient | None = None,
    mode: Literal["scan_and_review", "review_only"] = "scan_and_review",
    force_review: bool = False,
    owner_label: str = "timed",
    count_validation_signals: Callable[[str, list[str]], Awaitable[dict[str, dict[str, int]]]]
    | None = None,
) -> CycleReport:
    """Execute one curator cycle using the package-level curator API."""
    from prismclaw.engine.curator.cycle import CuratorCycle

    cycle = CuratorCycle(
        sop_repo=sop_repo,
        settings=settings,
        state_store=CuratorStateStore(redis_client),
        lock_manager=lock_manager,
        task_manager=task_manager,
        l1_index=l1_index,
        workspace_base_dir=workspace_base_dir,
        llm_client=llm_client,
        mode=mode,
        force_review=force_review,
        owner_label=owner_label,
        count_validation_signals=count_validation_signals,
    )
    return await cycle.execute()  # pyright: ignore[reportReturnType]


# NOTE: REVIEW_PROMPT_TEMPLATE lives in persistence/curator/repository.py
# (single source of truth — the repo's review_session() renders it for the LLM).
# This module used to carry a second copy that drifted; removed 2026-11-12.


async def create_curator_loop(
    settings: CuratorSettings,
    sop_repo: CuratorSopRepository,
    redis_client: aioredis.Redis,
    l1_index: CuratorL1Index,
    *,
    lock_manager: LockManagerProtocol,
    task_manager: TaskManagerProtocol,
    workspace_base_dir: Path | None = None,
    llm_client: LLMClient | None = None,
    count_validation_signals: Callable[[str, list[str]], Awaitable[dict[str, dict[str, int]]]]
    | None = None,
) -> None:
    logger.info(
        "curator cycle using RedisLockManager (key: prismclaw:curator:cycle)",
    )

    try:
        archive_days = int(getattr(settings, "archive_after_days", 90))
        promo_days = int(getattr(settings, "promotion_min_days", 7))
        if archive_days <= promo_days:
            logger.warning(
                "archiveAfterDays (%d) <= promotionMinDays (%d): "
                "SOPs may be archived before graduation eligibility",
                archive_days,
                promo_days,
            )
    except (TypeError, ValueError):
        pass

    state_store = CuratorStateStore(redis_client)
    await state_store.seed_if_missing()

    try:
        while True:
            await asyncio.sleep(settings.check_interval_seconds)

            last_run_at = await state_store.get_last_scan_at()
            if last_run_at is not None and time.time() - last_run_at < settings.interval_seconds:
                continue

            report = await run_curator_cycle(
                sop_repo=sop_repo,
                settings=settings,
                redis_client=redis_client,
                lock_manager=lock_manager,
                task_manager=task_manager,
                l1_index=l1_index,
                workspace_base_dir=workspace_base_dir,
                llm_client=llm_client,
                mode="scan_and_review",
                force_review=False,
                owner_label="timed",
                count_validation_signals=count_validation_signals,
            )

            if not report.acquired:
                continue
            if report.error == "lock_lost":
                logger.warning("curator cycle aborted: lock_lost (timed)")
    except asyncio.CancelledError:
        return


async def _list_active_sop_sessions(sop_repo: CuratorSopRepository) -> list[str]:
    """Distinct session_keys that currently own active SOPs."""
    return await sop_repo.list_active_sop_sessions()


async def run_curator_scan(
    sop_repo: CuratorSopRepository,
    archive_days: int,
    l1_index: CuratorL1Index,
    workspace_base_dir: Path | None = None,
    settings: CuratorSettings | None = None,
    *,
    check_alive: Callable[[], None] = lambda: None,
    count_validation_signals: Callable[[str, list[str]], Awaitable[dict[str, dict[str, int]]]]
    | None = None,
) -> CuratorReport:

    check_alive()
    session_keys = await _list_active_sop_sessions(sop_repo)

    report = CuratorReport(total_scanned=len(session_keys))
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _bounded_scan(session_key: str) -> tuple[int, int]:
        async with semaphore:
            check_alive()
            return await _scan_session(
                sop_repo,
                session_key,
                archive_days,
                l1_index,
                workspace_base_dir=workspace_base_dir,
                settings=settings,
                count_validation_signals=count_validation_signals,
            )

    results = await asyncio.gather(
        *[_bounded_scan(sk) for sk in session_keys],
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            report.errors.append(f"{session_keys[i]}: {result!r}")
        elif isinstance(result, tuple):
            report.total_archived += result[0]
            report.total_graduated += result[1]

    return report


async def _scan_session(
    sop_repo: CuratorSopRepository,
    session_key: str,
    archive_days: int,
    l1_index: CuratorL1Index,
    workspace_base_dir: Path | None = None,
    settings: CuratorSettings | None = None,
    count_validation_signals: Callable[[str, list[str]], Awaitable[dict[str, dict[str, int]]]]
    | None = None,
) -> tuple[int, int]:
    """Archive stale SOPs and graduate high-use ones for one session_key."""
    return await sop_repo.scan_session(
        session_key=session_key,
        archive_days=archive_days,
        l1_index=l1_index,
        workspace_base_dir=workspace_base_dir,
        settings=settings,
        count_validation_signals=count_validation_signals,
    )


# ---------------------------------------------------------------------------
# LLM Review
# ---------------------------------------------------------------------------


async def should_run_llm_review(settings: CuratorSettings, state_store: CuratorStateStore) -> bool:
    if not settings.llm_review_enabled:
        return False

    last_run = await state_store.get_last_review_at()
    return not (
        last_run is not None and time.time() - last_run < settings.llm_review_interval_seconds
    )


async def _review_session(
    sop_repo: CuratorSopRepository,
    session_key: str,
    settings: CuratorSettings,
    l1_index: CuratorL1Index,
    workspace_base_dir: Path,
    llm_client: LLMClient,
    *,
    check_alive: Callable[[], None] = lambda: None,
    abort_event: asyncio.Event | None = None,
) -> ReviewOutcome:
    """Per-session LLM review — delegates to CuratorSopRepository.

    ``check_alive`` is called at function entry, before the LLM request, and
    before each UPDATE statement. Its default is a no-op for standalone test
    use; the curator cycle wires it to ``DistributedMutex.check_alive`` so
    lock loss shrinks the UPDATE race window from seconds to
    sub-millisecond.

    Returns ``ReviewOutcome`` describing how many entries were promoted,
    archived, or failed. Callers sum ``total_actions`` across sessions.
    """
    return await sop_repo.review_session(
        session_key=session_key,
        settings=settings,
        l1_index=l1_index,
        workspace_base_dir=workspace_base_dir,
        llm_client=llm_client,
        check_alive=check_alive,
        abort_event=abort_event,
    )


async def run_llm_review(
    sop_repo: CuratorSopRepository,
    session_key: str,
    settings: CuratorSettings,
    llm_client: LLMClient,
    l1_index: CuratorL1Index,
    workspace_base_dir: Path,
    *,
    check_alive: Callable[[], None] = lambda: None,
) -> ReviewOutcome:
    """Top-level LLM review for a single session's SOPs.

    Thin async wrapper around :func:`_review_session` that preserves the
    public review API used by ``CuratorCycle`` (keyword args: ``sop_repo``,
    ``session_key``, ``settings``, ``llm_client``, ``l1_index``,
    ``workspace_base_dir``, ``check_alive``).
    """

    return await _review_session(
        sop_repo=sop_repo,
        session_key=session_key,
        settings=settings,
        l1_index=l1_index,
        workspace_base_dir=workspace_base_dir,
        llm_client=llm_client,
        check_alive=check_alive,
    )
