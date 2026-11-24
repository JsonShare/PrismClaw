from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from prismclaw.engine.services.llm import DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS
from prismclaw.engine.services.sop_extraction import SOP_LLM_MAX_ATTEMPTS

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from prismclaw.engine.hooks.memory_nudge_hook import MemoryNudgeHook
    from prismclaw.engine.services.sop_extraction import ExtractionResult, SopExtractionSettings
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_HEADROOM_SECONDS = 30.0
EXTRACT_TIMEOUT_SECONDS = (
    DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS * SOP_LLM_MAX_ATTEMPTS + EXTRACT_TIMEOUT_HEADROOM_SECONDS
)


async def run_extract(
    *,
    redis_client: aioredis.Redis,
    memory_store: MemoryStore,
    session_store: SessionStore,
    llm_client: LLMClient,
    session_id: str,
    settings: SopExtractionSettings,
    nudge_hook: MemoryNudgeHook | None = None,
) -> ExtractionResult | None:
    """
    Run SOP extraction with timeout. Returns ExtractionResult, or None if timed out, or a
    synthetic disabled result if any dep is missing.
    """
    from prismclaw.engine.services.sop_extraction import (
        ExtractionResult,
        _check_user_ratelimit,
        extract_sops_sync,
    )
    from prismclaw.schema.session.ids import derive_memory_session_key

    if (
        redis_client is None
        or memory_store is None
        or settings is None
        or llm_client is None
        or session_store is None
    ):
        return ExtractionResult(spawned=False, skip_reason="disabled")

    session_key = derive_memory_session_key(session_id)
    if not await _check_user_ratelimit(redis_client, session_key):
        return ExtractionResult(spawned=False, skip_reason="rate_limited")

    try:
        async with asyncio.timeout(EXTRACT_TIMEOUT_SECONDS):
            return await extract_sops_sync(
                memory_store=memory_store,
                session_store=session_store,
                redis_client=redis_client,
                llm_client=llm_client,
                session_id=session_id,
                settings=settings,
                min_tool_calls=1,
                nudge_hook=nudge_hook,
            )
    except TimeoutError:
        return None
