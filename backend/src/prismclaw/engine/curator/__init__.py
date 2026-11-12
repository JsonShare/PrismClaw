"""Curator background loop — archives stale memory entries via SETNX distributed lock.

SOPs (``type='auto_sop'``) live in the shared ``pc_memory_entries`` Postgres table.
The curator scans per ``session_key``, archives stale SOPs (``status='archived'``)
and graduates high-use ones (``status='graduated'``) into SKILL.md files.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from prismclaw.schema.agent.curator import CuratorSopRepository
from prismclaw.schema.agent.curator_types import CuratorReport, CycleReport, ReviewOutcome

from prismclaw.engine.curator.state import (
    CuratorStateStore,
)

logger = logging.getLogger(__name__)


CycleError = Literal["lock_lost", "review_skipped_interval"] | None

# SOP rows live in pc_memory_entries and are identified by type='auto_sop'.
_SOP_TYPE = "auto_sop"

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
    settings: Any,
    redis_client: Any,
    lock_manager: Any,
    task_manager: Any,
    l1_index: Any,
    workspace_base_dir: Path | None = None,
    llm_client: Any = None,
    mode: Literal["scan_and_review", "review_only"] = "scan_and_review",
    force_review: bool = False,
    owner_label: str = "timed",
    count_validation_signals: Callable | None = None,
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
    settings: Any,
    sop_repo: CuratorSopRepository,
    redis_client: Any,
    l1_index: Any,
    *,
    lock_manager: Any,
    task_manager: Any,
    workspace_base_dir: Path | None = None,
    llm_client: Any = None,
    count_validation_signals: Callable | None = None,
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
            if last_run_at is not None:
                if time.time() - last_run_at < settings.interval_seconds:
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
    l1_index: Any,
    workspace_base_dir: Path | None = None,
    settings: Any = None,
    *,
    check_alive: Callable[[], None] = lambda: None,
    count_validation_signals: Callable | None = None,
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
    l1_index: Any,
    workspace_base_dir: Path | None = None,
    settings: Any = None,
    count_validation_signals: Callable | None = None,
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


async def should_run_llm_review(settings: Any, state_store: CuratorStateStore) -> bool:
    if not settings.llm_review_enabled:
        return False

    last_run = await state_store.get_last_review_at()
    if last_run is not None:
        if time.time() - last_run < settings.llm_review_interval_seconds:
            return False

    return True


async def _review_session(
    sop_repo: CuratorSopRepository,
    session_key: str,
    settings: Any,
    l1_index: Any,
    workspace_base_dir: Path,
    llm_client: Any,
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
    settings: Any,
    llm_client: Any,
    l1_index: Any,
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
