"""SDD A4: full-turn chat capture (docs/memory-dream-sdd.md §5.4).

Pure-chat turns (no tool calls) never reach the SOP extraction pipeline
(it gates on min_tool_calls>=2) and rely on the model voluntarily calling
`memorize` — which is why consulting-style sessions lose cross-session
memory entirely (defect S3).

This module buffers no-tool turn transcripts per session and, at session
consolidation time, runs ONE batched LLM extraction over the whole buffer.
Extracted facts enter via the same store() path as the memorize tool,
flagged with origin_stage='auto_chat_extraction' and a low initial
confidence so retrieval ranking naturally de-prioritizes them until
validation events accumulate (gateway/B1 territory).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.memory.classify import classify_scope
from prismclaw.schema.memory.memory_types import MemoryEntry

logger = logging.getLogger(__name__)

# Fewer buffered turns than this → not worth one LLM call (budget gate).
MIN_CHAT_TURNS_FOR_EXTRACTION = 3
# short_term facts expire after this many seconds (MindVault-style write-time
# temporal scoping; "我今天想X" ≠ "我一直喜欢X").
SHORT_TERM_TTL_SECONDS = 7 * 24 * 3600
# Initial confidence for auto-extracted facts — low on purpose.
AUTO_EXTRACTION_CONFIDENCE = 0.5
_ALLOWED_TIME_SCOPES = {"permanent", "short_term", "recurring"}
_ALLOWED_FACT_TYPES = {
    "user_preference",
    "decision",
    "env_fact",
    "project_context",
    "general",
}

_EXTRACTION_PROMPT = """你是一个记忆提取引擎。以下是一段纯对话（无工具调用）的完整轮次记录。
请提取其中值得跨会话记住的持久事实：用户偏好、明确决策、环境/项目背景、稳定的约束条件。

要求：
1. 忽略闲聊、寒暄、只与当前会话相关的临时内容。
2. 每条事实一句话，自包含（脱离对话后仍可理解）。
3. 为每条事实标注 type 与 time_scope：
   - type: user_preference（用户偏好）/ decision（明确决策）/ env_fact（环境事实）/ project_context（项目背景）/ general
   - time_scope: permanent（长期有效）/ short_term（临时状态/近期计划）/ recurring（周期性）
4. "我今天/这周末想X"是 short_term；"我一直/我们总是X"是 permanent。
5. 若整段对话没有值得记忆的内容，返回空数组。
6. 事实以用户原话及其明确确认为准；assistant 的转述、总结与建议不是事实来源——禁止从中提取，禁止将 assistant 回答中丢失或弱化的细节（如省略的技术专名、数值）再生成一条"残缺版本"事实。同一主题只保留信息最完整的表述，与用户原话冲突的转述一律丢弃。

以 JSON 数组输出，格式：
[{"fact": "...", "type": "user_preference", "time_scope": "permanent"}]

=== 对话记录 ===
{transcript}
=== 结束 ==="""


def build_extraction_prompt(turns: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, turn in enumerate(turns, 1):
        user_text = str(turn.get("user", "")).strip()
        assistant_text = str(turn.get("assistant", "")).strip()
        lines.append(f"--- 轮次 {i} ---")
        if user_text:
            lines.append(f"user: {user_text}")
        if assistant_text:
            lines.append(f"assistant: {assistant_text}")
    # NOTE: .replace (not str.format) — the prompt template embeds a JSON
    # example whose braces would be misread as format placeholders.
    return _EXTRACTION_PROMPT.replace("{transcript}", "\n".join(lines))


def parse_facts(raw_text: str) -> list[dict[str, str]]:
    """Parse LLM JSON output into validated fact dicts; [] on any failure."""
    if not raw_text or not raw_text.strip():
        return []
    cleaned = raw_text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        bracket = re.search(r"\[\s*\{.*\}\s*\]", cleaned, re.DOTALL)
        if bracket:
            cleaned = bracket.group(0)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        logger.warning("chat fact extraction: unparseable LLM output")
        return []
    if not isinstance(parsed, list):
        return []

    facts: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        if not fact:
            continue
        fact_type = str(item.get("type", "general")).strip()
        if fact_type not in _ALLOWED_FACT_TYPES:
            fact_type = "general"
        time_scope = str(item.get("time_scope", "permanent")).strip()
        if time_scope not in _ALLOWED_TIME_SCOPES:
            time_scope = "permanent"
        facts.append({"fact": fact, "type": fact_type, "time_scope": time_scope})
    return facts


async def extract_chat_facts(llm_client: Any, turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One batched LLM call over the buffered turns. Best-effort: [] on failure."""
    if not turns:
        return []
    from prismclaw.schema.llm.request import PackedLLMRequest

    response = await llm_client.complete(
        request=PackedLLMRequest(
            messages=[{"role": "user", "content": build_extraction_prompt(turns)}],
            system="",
            context_payload=None,
            cache_strategy={},
        )
    )
    return parse_facts(getattr(response, "text", "") or "")


async def store_chat_facts(
    memory_store: MemoryStore,
    *,
    session_key: str,
    session_id: str,
    facts: list[dict[str, str]],
    gateway: Any = None,
) -> int:
    """Persist extracted facts via the memorize store() path.

    short_term facts get an expires_at so the decay field (currently dormant)
    has data the moment B3 activates it. SDD B2: when a gateway is provided,
    each fact is arbitrated (dedup/conflict) instead of blind-stored.
    """
    now = time.time()
    stored = 0
    for fact in facts:
        entry = MemoryEntry(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            layer="L2",
            type=fact["type"],
            content=fact["fact"],
            scope=classify_scope(fact["type"]),  # SDD B5 write-time stamping
            source_session_id=session_id,
            created_at=now,
            updated_at=now,
            confidence=AUTO_EXTRACTION_CONFIDENCE,
            origin_stage="auto_chat_extraction",
            expires_at=(now + SHORT_TERM_TTL_SECONDS)
            if fact["time_scope"] == "short_term"
            else None,
        )
        if gateway is not None:
            # Arbitration failures on auto-extracted facts are non-fatal —
            # fail-open inside the gateway covers LLM errors; lock errors
            # just skip this fact (extraction runs again next session).
            try:
                result = await gateway.submit(session_key, entry, source="auto_chat_extraction")
                if result.decision != "NOOP":
                    stored += 1
            except Exception:
                logger.warning(
                    "gateway submit failed for chat fact (skipped)", exc_info=True
                )
            continue
        await memory_store.store(session_key, entry)
        stored += 1
    return stored
