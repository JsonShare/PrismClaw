from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from prismclaw.engine.services.llm import DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS
from prismclaw.engine.services.sop_extraction import SOP_LLM_MAX_ATTEMPTS

if TYPE_CHECKING:
    from prismclaw.engine.services.sop_extraction import ExtractionResult

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_HEADROOM_SECONDS = 30.0
EXTRACT_TIMEOUT_SECONDS = (
    DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS * SOP_LLM_MAX_ATTEMPTS
    + EXTRACT_TIMEOUT_HEADROOM_SECONDS
)


async def run_extract(
    *,
    redis_client: Any,
    memory_store: Any,
    session_store: Any,
    llm_client: Any,
    session_id: str,
    settings: Any,
    nudge_hook: Any = None,
    timeout: float = EXTRACT_TIMEOUT_SECONDS,
) -> ExtractionResult | None:
    """Run SOP extraction with timeout. Returns ExtractionResult, or None if timed out, or a synthetic disabled result if any dep is missing."""
    from prismclaw.schema.session.ids import derive_memory_session_key
    from prismclaw.engine.services.sop_extraction import (
        ExtractionResult,
        _check_user_ratelimit,
        extract_sops_sync,
    )

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
        return await asyncio.wait_for(
            extract_sops_sync(
                memory_store=memory_store,
                session_store=session_store,
                redis_client=redis_client,
                llm_client=llm_client,
                session_id=session_id,
                settings=settings,
                min_tool_calls=1,
                nudge_hook=nudge_hook,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return None
