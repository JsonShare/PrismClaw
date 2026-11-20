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
from prismclaw.schema.memory.classify import classify_scope, normalize_type
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
# 纯聊天提取只会写 L2（store_chat_facts），因此只允许事实类：procedure 需
# 走 sop_extraction 的 L3 校验/去重管线，此处不暴露给模型，避免绕过校验
# 直接污染 L3。键同时充当 parse_facts 的白名单。
_CHAT_CAPTURE_TYPES = {
    "fact": "事实（含环境事实、项目背景）",
    "preference": "用户偏好",
    "constraint": "稳定约束条件",
    "observation": "观察",
    "decision": "明确决策（长期有效、不衰减）",
}
_TYPE_HINT = " / ".join(f"{name}（{label}）" for name, label in _CHAT_CAPTURE_TYPES.items())

# SDD A8: Never Infer 敏感属性名单。
# 这些属性「只有用户明确说自己/他人有 X」才能存 fact；
# 如果用户只是"询问 X"/"讨论 X"/"帮朋友问 X"，绝对禁止存成关于用户的 fact，
# 可存成 preference（用户关注 X 主题）。
_SENSITIVE_ATTRIBUTES = """健康状态（疾病、诊断、用药、症状）
法律状态（诉讼、合同纠纷、合规调查）
政治倾向、宗教信仰、种族、性别取向
财务状况（收入、债务、资产）
个人身份（身份证号、住址、手机号、亲属关系）"""

_EXTRACTION_PROMPT = """你是一个记忆提取引擎。以下是一段纯对话（无工具调用）的完整轮次记录。
请提取其中值得跨会话记住的持久事实：用户偏好、明确决策、环境/项目背景、稳定的约束条件。

要求：
1. 忽略闲聊、寒暄、只与当前会话相关的临时内容。
2. 每条事实一句话，自包含（脱离对话后仍可理解）。
3. 为每条事实标注 type 与 time_scope：
   - type: {type_hint}
   - time_scope: permanent（长期有效）/ short_term（临时状态/近期计划）/ recurring（周期性）
4. "我今天/这周末想X"是 short_term；"我一直/我们总是X"是 permanent。
5. 若整段对话没有值得记忆的内容，返回空数组。
6. 事实以用户原话及其明确确认为准；assistant 的转述、总结与建议不是事实来源——禁止从中提取，禁止将 assistant 回答中丢失或弱化的细节（如省略的技术专名、数值）再生成一条"残缺版本"事实。同一主题只保留信息最完整的表述，与用户原话冲突的转述一律丢弃。

─── Never Infer 敏感属性约束（最高优先级）───
以下属性{sensitive_attrs}，**只有用户明确说自己/他人"患有 X"/"正在使用 X"/"是 X"时才能存成 fact**。
如果用户只是：
  · 询问（"帮我查一下 X"）
  · 讨论（"X 的最新指南是什么"）
  · 转述（"帮我朋友问一下 X"）
  · 好奇（"X 到底是怎么回事"）
  绝对禁止存成 fact。这种场景如果值得存，只能存成 preference 类型
  （如"用户关注 X 主题"），而不是 fact 类型（"用户患有 X"）。

以 JSON 数组输出，格式：
[{"fact": "...", "type": "fact", "time_scope": "permanent"}]

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
    return _EXTRACTION_PROMPT.replace("{type_hint}", _TYPE_HINT).replace(
        "{sensitive_attrs}", _SENSITIVE_ATTRIBUTES
    ).replace(
        "{transcript}", "\n".join(lines)
    )


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
        try:
            fact_type = normalize_type(str(item.get("type", "fact")).strip())
        except ValueError:
            fact_type = "fact"  # normalize_type 已做受控词表校验，非法值归 fact
        if fact_type not in _CHAT_CAPTURE_TYPES:
            fact_type = "fact"
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


# ═══════════════════════════════════════════════════════════════════
# SDD A8: 独立 Validator —— 二次 LLM 审查 + confidence 赋值
#
# 为什么需要 validator？
#   extractor 和 validator 是两个独立的 LLM 调用。extractor 在"从对话里提取
#   事实"，validator 在"审查这些事实是不是站得住脚"。同一个 LLM 同时做这两件事
#   时容易"自己信了自己"——独立调用让它有了 fresh perspective，显著降低
#   hallucination 和主语归属错误的通过率。
#
# validator 输出的 validation_label 和 confidence 会：
#   1. 写入 pc_memory_validation_events（供运维查污染率）
#   2. 覆盖 store_chat_facts 里的 AUTO_EXTRACTION_CONFIDENCE
#   3. rejected 直接丢弃，不入库
# ═══════════════════════════════════════════════════════════════════

_VALIDATION_PROMPT = """你是一个记忆审核引擎。以下是一段对话的完整轮次记录，以及另一个引擎从这段对话中提取出的候选记忆条目。
请你逐条审查每个候选条目，判断它是否真的应该被记忆下来。

审查标准：
1. **主语归属（最高优先级）**："用户询问/讨论/帮朋友问/好奇 X" ≠ "用户患有/使用/是 X"。以下敏感属性只有用户明确说自己/他人有 X 才能判 pass：
   {sensitive_attrs}
   如果候选条目的 fact 内容把"询问/讨论"当成了"确认"，一律判 rejected。
2. **证据来源**：事实必须有对话中的明确证据（用户原话明确确认 / 用户原话提供了信息）。如果候选条目是从 assistant 的回答或外部知识里来的，判 flagged（降 confidence）。
3. **自包含**：脱离对话后是否还能理解。如果需要当前对话上下文才能读懂，判 flagged。
4. **冗余**：如果多个候选条目说的是同一件事，保留信息最完整的那条，其余判 rejected。

对每条候选，以 JSON 输出审查结果：
- validation_label: "pass"（可信）/ "flagged"（存但降 confidence）/ "rejected"（丢弃）
- confidence: 0.0~1.0 的 float，建议：
    pass + 用户原话 = 0.95~1.0
    pass + assistant 转述（对话中有证据）= 0.7~0.85
    flagged（主语归属可疑 / 证据弱）= 0.2~0.5
    rejected 不需要填 confidence（会被丢弃）
- reason: 一句话说明审查结论

以 JSON 数组输出，格式：
[{{"index": 0, "validation_label": "pass", "confidence": 0.95, "reason": "..."}}]

=== 候选记忆条目 ===
{candidates_json}
=== 对话记录 ===
{transcript}
=== 结束 ===
"""


def build_validation_prompt(
    turns: list[dict[str, Any]],
    candidates: list[dict[str, str]],
) -> str:
    """Build validator prompt. candidates 是 extractor 输出的原始 JSON 数组。"""
    lines: list[str] = []
    for i, turn in enumerate(turns, 1):
        user_text = str(turn.get("user", "")).strip()
        assistant_text = str(turn.get("assistant", "")).strip()
        lines.append(f"--- 轮次 {i} ---")
        if user_text:
            lines.append(f"user: {user_text}")
        if assistant_text:
            lines.append(f"assistant: {assistant_text}")

    candidates_lines = []
    for i, c in enumerate(candidates):
        candidates_lines.append(
            f"[{i}] type={c.get('type', '?')}, time_scope={c.get('time_scope', '?')}, "
            f"fact={c.get('fact', '')}"
        )

    return _VALIDATION_PROMPT.replace(
        "{sensitive_attrs}", _SENSITIVE_ATTRIBUTES
    ).replace(
        "{candidates_json}", "\n".join(candidates_lines)
    ).replace(
        "{transcript}", "\n".join(lines)
    )


def parse_validation(raw_text: str, num_candidates: int) -> list[dict[str, Any]]:
    """Parse validator LLM output into per-candidate results。

    返回长度 = num_candidates 的列表；validator 没输出的条目默认为
    validation_label='rejected'（fail-safe：宁可丢也不存）。
    """
    default = [
        {"validation_label": "rejected", "confidence": 0.0, "reason": "validator 未审查到"}
        for _ in range(num_candidates)
    ]
    if not raw_text or not raw_text.strip():
        return default

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
        logger.warning("chat fact validation: unparseable LLM output")
        return default

    if not isinstance(parsed, list):
        return default

    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= num_candidates:
            continue
        label = str(item.get("validation_label", "rejected")).strip().lower()
        if label not in ("pass", "flagged", "rejected"):
            label = "rejected"
        try:
            conf_raw = item.get("confidence")
            confidence = float(conf_raw) if conf_raw is not None else (
                1.0 if label == "pass" else 0.4 if label == "flagged" else 0.0
            )
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0
        reason = str(item.get("reason", "")).strip()
        default[idx] = {
            "validation_label": label,
            "confidence": confidence,
            "reason": reason,
        }
    return default


async def validate_facts(
    llm_client: Any,
    turns: list[dict[str, Any]],
    candidates: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """独立 LLM 二次审查。返回每个候选的 {validation_label, confidence, reason}。

    失败时 fail-safe：全部判 rejected（宁可丢也不脏）。
    """
    if not candidates:
        return []
    if llm_client is None:
        # 没 LLM 就跳过 validator——但 confidence 保守设低
        return [
            {"validation_label": "flagged", "confidence": 0.4, "reason": "validator 未运行（无 LLM）"}
            for _ in candidates
        ]
    try:
        from prismclaw.schema.llm.request import PackedLLMRequest

        response = await llm_client.complete(
            request=PackedLLMRequest(
                messages=[
                    {"role": "user", "content": build_validation_prompt(turns, candidates)}
                ],
                system="",
                context_payload=None,
                cache_strategy={},
            )
        )
        return parse_validation(
            getattr(response, "text", "") or "", len(candidates)
        )
    except Exception:
        logger.warning("chat fact validation: LLM call failed")
        return [
            {"validation_label": "rejected", "confidence": 0.0, "reason": "validator LLM 异常"}
            for _ in candidates
        ]


async def store_validated_chat_facts(
    memory_store: MemoryStore,
    *,
    session_key: str,
    session_id: str,
    facts: list[dict[str, str]],
    validation_results: list[dict[str, Any]],
    gateway: Any = None,
    validation_ledger: Any = None,
) -> int:
    """Store facts that passed validator。

    相比 store_chat_facts 的区别：
    1. rejected 的直接丢弃（不入库）
    2. confidence 用 validator 给的值（覆盖 AUTO_EXTRACTION_CONFIDENCE）
    3. validation_label=pass 的 origin_stage='auto_validated'，flagged 的是 'auto_flagged'
    4. 每条写入 validation_events 供审计
    """
    now = time.time()
    stored = 0

    # 如果 facts 和 validation_results 长度不匹配，做安全对齐
    if len(validation_results) != len(facts):
        logger.warning(
            "store_validated_chat_facts: facts(%d) vs validation(%d) length mismatch, "
            "fallback to rejected for misaligned entries",
            len(facts), len(validation_results),
        )
        aligned_results = []
        for i, f in enumerate(facts):
            if i < len(validation_results):
                aligned_results.append(validation_results[i])
            else:
                aligned_results.append({
                    "validation_label": "rejected",
                    "confidence": 0.0,
                    "reason": "validation_results 长度不足",
                })
        validation_results = aligned_results

    for fact, vr in zip(facts, validation_results):
        label = vr.get("validation_label", "rejected")
        if label == "rejected":
            # rejected 也写 validation_events（方便审计知道丢了多少）
            await _maybe_write_validation_event(
                validation_ledger, session_key, session_id,
                fact=fact, validation=vr, entry_id=None,
            )
            continue

        confidence = float(vr.get("confidence", 0.4))
        reason = vr.get("reason", "")
        origin = "auto_validated" if label == "pass" else "auto_flagged"

        entry = MemoryEntry(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            layer="L2",
            type=fact["type"],
            content=fact["fact"],
            scope=classify_scope(fact["type"]),
            source_session_id=session_id,
            created_at=now,
            updated_at=now,
            confidence=confidence,
            origin_stage=origin,
            expires_at=(now + SHORT_TERM_TTL_SECONDS)
            if fact.get("time_scope") == "short_term"
            else None,
        )

        persisted = False
        if gateway is not None:
            try:
                result = await gateway.submit(
                    session_key, entry, source="auto_chat_extraction"
                )
                if result.decision != "NOOP":
                    stored += 1
                    persisted = True
            except Exception:
                logger.warning(
                    "gateway submit failed for validated chat fact (skipped)",
                    exc_info=True,
                )
        else:
            await memory_store.store(session_key, entry)
            stored += 1
            persisted = True

        if persisted:
            await _maybe_write_validation_event(
                validation_ledger, session_key, session_id,
                fact=fact, validation=vr, entry_id=entry.id,
            )

    return stored


async def _maybe_write_validation_event(
    validation_ledger: Any,
    session_key: str,
    session_id: str,
    *,
    fact: dict[str, str],
    validation: dict[str, Any],
    entry_id: str | None,
) -> None:
    """Best-effort 写 validation_events。ledger 可能是 None，DB 可能挂。"""
    if validation_ledger is None:
        return
    try:
        await validation_ledger.record_validation(
            session_key=session_key,
            session_id=session_id,
            entry_id=entry_id,
            entry_type=fact.get("type", ""),
            fact_content=fact.get("fact", "")[:500],
            validation_label=validation.get("validation_label", "unknown"),
            confidence_before=AUTO_EXTRACTION_CONFIDENCE,
            confidence_after=float(validation.get("confidence", 0.4)),
            reason=validation.get("reason", "")[:500],
        )
    except Exception:
        logger.warning(
            "validation event write failed (non-fatal)", exc_info=True
        )
