"""E5: 关系抽取器 — 从文本中抽取记忆间的语义关系。

产出 ExtractedRelation（entity_key 级别），调用方通过 entity_key 查 entries 表
找到 entry_id 后构造 MemoryEdge。找不到对应 entry 的边不写入。

v2.0 修正 #25：不直接产出 MemoryEdge，避免 entity_key→entry_id 的耦合内聚。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from prismclaw.schema.memory.memory_graph import VALID_PREDICATES, DIRECTION_SENSITIVE_PREDICATES

_EXTRACTION_PROMPT = """从以下文本中抽取核心语义关系。仅抽取高置信度（>= 0.7）的关系。

6 种关系类型：
- causes: A 导致 B（因果）
- prohibits: A 禁止/阻止 B（约束）
- is_a: A 是 B 的一种（分类）
- precedes: A 先于 B（时序）
- replaces: A 替代 B（版本替代）
- conflicts: A 与 B 矛盾/冲突（互斥）

输出 JSON 数组，每条关系格式：
[{{"source": "实体A", "target": "实体B", "predicate": "causes", "condition": "条件（空字符串=无条件）", "confidence": 0.9}}]

如果文本中没有明确的关系，输出空数组 []。
只输出 JSON，不要任何解释。

文本：
{text}"""


@dataclass
class ExtractedRelation:
    """LLM 抽取的关系（entity_key 级别，非 entry id 级别）。"""
    source_entity_key: str
    target_entity_key: str
    predicate: str
    condition: str          # 无条件时为空字符串 ''（F1 修正：不用 None）
    confidence: float


class RelationExtractor:
    """从文本中抽取核心关系（仅高置信度关系，低频的留给向量）。"""

    _MIN_CONFIDENCE_DEFAULT = 0.7
    _MIN_CONFIDENCE_DIRECTION_SENSITIVE = 0.9

    def __init__(self, *, min_confidence: float = 0.7) -> None:
        self._min_confidence = min_confidence

    async def extract(self, text: str, *, llm_client: Any) -> list[ExtractedRelation]:
        """LLM 抽取关系，predicate 限制为 6 种，confidence >= min_confidence。"""
        if not text or not text.strip():
            return []

        try:
            from prismclaw.schema.llm.request import PackedLLMRequest

            response = await llm_client.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(text=text)}],
                    system="",
                    context_payload=None,
                    cache_strategy={},
                )
            )
            raw = (getattr(response, "text", "") or "").strip()
        except Exception:
            logger.warning("relation extraction LLM failed", exc_info=True)
            return []

        if not raw:
            return []

        # 提取 JSON
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw, re.DOTALL)
        if not json_match:
            return []

        try:
            items = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.warning("relation extraction JSON parse failed: %s", raw[:200])
            return []

        relations: list[ExtractedRelation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            predicate = str(item.get("predicate", "")).strip().lower()
            if predicate not in VALID_PREDICATES:
                continue
            confidence = float(item.get("confidence", 0) or 0)
            # 方向敏感谓词使用更高阈值（0.9 vs 0.7）
            threshold = (
                self._MIN_CONFIDENCE_DIRECTION_SENSITIVE
                if predicate in DIRECTION_SENSITIVE_PREDICATES
                else self._min_confidence
            )
            if confidence < threshold:
                continue
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            if not source or not target:
                continue
            condition = str(item.get("condition", "") or "").strip()

            relations.append(ExtractedRelation(
                source_entity_key=source,
                target_entity_key=target,
                predicate=predicate,
                condition=condition,
                confidence=confidence,
            ))

        return relations

    async def resolve_and_store(
        self,
        session_key: str,
        relations: list[ExtractedRelation],
        *,
        memory: Any,
        edge_store: Any,
    ) -> int:
        """将 ExtractedRelation 解析为 MemoryEdge 并写入。

        通过 memory.get_entry_id_by_entity_key() 精确查找（IG1），
        避免依赖 search() 排序不确定性。
        """
        if not relations:
            return 0

        from prismclaw.schema.memory import MemoryEdge

        now = time.time()
        stored = 0

        for rel in relations:
            # IG1：精确查询 entity_key → entry_id
            try:
                source_id = await memory.get_entry_id_by_entity_key(
                    session_key, rel.source_entity_key,
                )
                target_id = await memory.get_entry_id_by_entity_key(
                    session_key, rel.target_entity_key,
                )
            except Exception:
                logger.warning(
                    "relation resolve failed for %s", session_key, exc_info=True
                )
                continue

            if source_id is None or target_id is None:
                logger.debug(
                    "[E6] entity_key not resolved: source=%s→%s, target=%s→%s",
                    rel.source_entity_key, source_id, rel.target_entity_key, target_id,
                )
                continue

            edge = MemoryEdge(
                id=f"edge_{now:.0f}_{stored}",
                session_key=session_key,
                source_id=source_id,
                target_id=target_id,
                predicate=rel.predicate,
                condition=rel.condition,
                confidence=rel.confidence,
                status="active",
                created_at=now,
                updated_at=now,
            )
            try:
                await edge_store.store_edge(session_key, edge)
                stored += 1
            except Exception:
                logger.warning(
                    "relation store_edge failed for %s", session_key, exc_info=True
                )

        return stored
