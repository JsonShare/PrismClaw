"""E5: 记忆图谱边存储（PG 邻接表 + 递归 CTE 遍历）。

6 种通用谓词：causes, prohibits, is_a, precedes, replaces, conflicts。
condition TEXT 字段存储自然语言条件（空字符串=无条件），LLM 运行时求值。
部分唯一索引（WHERE status='active'）避免同逻辑边 active→inactive→active 冲突。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from prismclaw.schema.memory import (
    DIRECTION_SENSITIVE_PREDICATES,
    VALID_PREDICATES,
    MemoryEdge,
)

logger = logging.getLogger(__name__)


def _sql_text(sql: str):
    from sqlalchemy import text as sa_text
    return sa_text(sql)


class EdgeStore:
    """记忆图谱边存储（PG 邻接表 + 递归 CTE 遍历）。"""

    def __init__(self, engine: AsyncEngine, *, dlp_scanner: Any = None) -> None:
        self._engine = engine
        self._dlp = dlp_scanner

    async def store_edge(self, session_key: str, edge: MemoryEdge) -> None:
        """写入一条边。condition 使用空字符串替代 NULL（F1 修正）。
        对 condition 和 evidence 字段做 DLP 脱敏。
        """
        if edge.predicate not in VALID_PREDICATES:
            raise ValueError(
                f"Invalid predicate '{edge.predicate}', must be one of {sorted(VALID_PREDICATES)}"
            )

        # 方向敏感谓词双向校验（best-effort warning，不阻断写入）
        # 在写入事务内检查反向边，避免额外连接和 TOCTOU 竞态

        condition = edge.condition or ""
        evidence = edge.evidence

        # E1 联动：DLP 脱敏 condition 和 evidence
        if self._dlp is not None:
            condition = self._sanitize_field(condition, "condition")
            evidence = self._sanitize_field(evidence, "evidence")

        now = time.time()
        async with self._engine.begin() as conn:
            # 方向敏感谓词双向校验（同一事务内，避免 TOCTOU 竞态）
            if edge.predicate in DIRECTION_SENSITIVE_PREDICATES:
                try:
                    rev = await conn.execute(
                        _sql_text(
                            "SELECT id FROM pc_memory_edges "
                            "WHERE session_key = :sk AND status = 'active' "
                            "AND source_id = :tgt AND target_id = :src "
                            "AND predicate = :pred LIMIT 1"
                        ),
                        {
                            "sk": session_key,
                            "src": edge.source_id,
                            "tgt": edge.target_id,
                            "pred": edge.predicate,
                        },
                    )
                    if rev.fetchone():
                        logger.warning(
                            "[E8] %s: reverse edge exists for %s→%s (%s), writing anyway",
                            session_key, edge.target_id, edge.source_id, edge.predicate,
                        )
                except Exception:
                    pass  # best-effort

            try:
                await conn.execute(
                    _sql_text(
                        """
                        INSERT INTO pc_memory_edges
                            (id, session_key, source_id, target_id, predicate,
                             condition, confidence, evidence, status, created_at, updated_at)
                        VALUES (:id, :sk, :src, :tgt, :pred,
                                :cond, :conf, :evid, :status, :cat, :uat)
                        ON CONFLICT (id) DO UPDATE SET
                            source_id = EXCLUDED.source_id,
                            target_id = EXCLUDED.target_id,
                            predicate = EXCLUDED.predicate,
                            condition = EXCLUDED.condition,
                            confidence = EXCLUDED.confidence,
                            evidence = EXCLUDED.evidence,
                            status = EXCLUDED.status,
                            updated_at = EXCLUDED.updated_at
                        """
                    ),
                    {
                        "id": edge.id or f"edge_{session_key}_{time.time_ns():x}",
                        "sk": session_key,
                        "src": edge.source_id,
                        "tgt": edge.target_id,
                        "pred": edge.predicate,
                        "cond": condition,
                        "conf": edge.confidence,
                        "evid": evidence,
                        "status": edge.status,
                        "cat": edge.created_at or now,
                        "uat": now,
                    },
                )
            except Exception as exc:
                # 部分唯一索引冲突（同逻辑键已有 active 边）→ 静默跳过
                err_msg = str(exc).lower()
                if "idx_edges_unique" in err_msg or "unique" in err_msg:
                    logger.info(
                        "[E5] %s: duplicate active edge skipped (%s→%s %s)",
                        session_key, edge.source_id, edge.target_id, edge.predicate,
                    )
                else:
                    raise

    async def delete_edge(self, session_key: str, edge_id: str) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _sql_text("DELETE FROM pc_memory_edges WHERE id = :id AND session_key = :sk"),
                {"id": edge_id, "sk": session_key},
            )
            return (result.rowcount or 0) > 0

    async def deactivate_edges_for_entry(self, session_key: str, entry_id: str) -> int:
        """E3 降级条目时调用：将关联边标记为 inactive。

        避免死边累积和递归穿过失效节点。
        """
        now = time.time()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _sql_text(
                    """
                    UPDATE pc_memory_edges
                    SET status = 'inactive', updated_at = :now
                    WHERE session_key = :sk
                      AND status = 'active'
                      AND (source_id = :eid OR target_id = :eid)
                    """
                ),
                {"sk": session_key, "eid": entry_id, "now": now},
            )
            count = result.rowcount or 0
            if count:
                logger.info(
                    "[E5] %s: deactivated %d edges for entry %s",
                    session_key, count, entry_id,
                )
            return count

    async def cleanup_dead_edges(self, session_key: str) -> int:
        """Deep tier 清理：删除 source/target 条目已不存在或已非 active 的 inactive 边。

        清理条件（满足任一）：
        1. source 或 target 条目已从 pc_memory_entries 中物理删除
        2. source 或 target 条目状态为 decayed/archived（非 active）
        """
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _sql_text(
                    """
                    DELETE FROM pc_memory_edges e
                    WHERE e.session_key = :sk
                      AND e.status = 'inactive'
                      AND (
                          NOT EXISTS (
                              SELECT 1 FROM pc_memory_entries ent
                              WHERE ent.id = e.source_id AND ent.session_key = :sk
                          )
                          OR EXISTS (
                              SELECT 1 FROM pc_memory_entries ent
                              WHERE ent.id = e.source_id AND ent.session_key = :sk
                                AND ent.status != 'active'
                          )
                          OR NOT EXISTS (
                              SELECT 1 FROM pc_memory_entries ent
                              WHERE ent.id = e.target_id AND ent.session_key = :sk
                          )
                          OR EXISTS (
                              SELECT 1 FROM pc_memory_entries ent
                              WHERE ent.id = e.target_id AND ent.session_key = :sk
                                AND ent.status != 'active'
                          )
                      )
                    """
                ),
                {"sk": session_key},
            )
            count = result.rowcount or 0
            if count:
                logger.info("[E5] %s: cleaned up %d dead edges", session_key, count)
            return count

    async def edge_degree(self, session_key: str, entry_id: str) -> int:
        """E3 连接度查询：该条目关联的 active 边数。"""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _sql_text(
                    """
                    SELECT COUNT(*) FROM pc_memory_edges
                    WHERE session_key = :sk
                      AND status = 'active'
                      AND (source_id = :eid OR target_id = :eid)
                    """
                ),
                {"sk": session_key, "eid": entry_id},
            )
            row = result.fetchone()
            return int(row[0]) if row else 0

    async def traverse(
        self, session_key: str, source_ids: list[str], *,
        max_hops: int = 2, max_edges: int = 100,
    ) -> list[MemoryEdge]:
        """N 跳遍历：递归 CTE，每步 JOIN entries 过滤 active 条目。

        IE8：有效 confidence = min(edge.confidence, target_entry.confidence)。
        IG2：max_edges 限制返回边数，防止高扇出 hub 级联崩溃。
        返回从 source_ids 出发可达的所有边（不含起点自身的边）。
        """
        if not source_ids:
            return []

        async with self._engine.begin() as conn:
            result = await conn.execute(
                _sql_text(
                    """
                    WITH RECURSIVE traverse AS (
                        SELECT e.id, e.source_id, e.target_id, e.predicate,
                               e.condition,
                               LEAST(e.confidence, ent.confidence) AS effective_conf,
                               e.evidence, e.status,
                               e.created_at, e.updated_at, 1 AS hop
                        FROM pc_memory_edges e
                        JOIN pc_memory_entries ent ON ent.id = e.source_id
                            AND ent.session_key = :sk
                            AND ent.status = 'active'
                        WHERE e.source_id = ANY(:source_ids)
                          AND e.session_key = :sk
                          AND e.status = 'active'

                        UNION ALL

                        SELECT e.id, e.source_id, e.target_id, e.predicate,
                               e.condition,
                               LEAST(e.confidence, ent.confidence) AS effective_conf,
                               e.evidence, e.status,
                               e.created_at, e.updated_at, t.hop + 1
                        FROM pc_memory_edges e
                        JOIN traverse t ON e.source_id = t.target_id
                        JOIN pc_memory_entries ent ON ent.id = e.target_id
                            AND ent.session_key = :sk
                            AND ent.status = 'active'
                        WHERE t.hop < :max_hops
                          AND e.session_key = :sk
                          AND e.status = 'active'
                          AND e.predicate != 'replaces'
                    )
                    SELECT DISTINCT id, source_id, target_id, predicate,
                           condition, effective_conf, evidence, status,
                           created_at, updated_at, hop
                    FROM traverse
                    ORDER BY hop, effective_conf DESC
                    LIMIT :max_edges
                    """
                ),
                {
                    "sk": session_key,
                    "source_ids": source_ids,
                    "max_hops": max_hops,
                    "max_edges": max_edges,
                },
            )
            edges: list[MemoryEdge] = []
            for row in result.fetchall():
                edges.append(MemoryEdge(
                    id=row[0],
                    source_id=row[1],
                    target_id=row[2],
                    predicate=row[3],
                    condition=row[4] or "",
                    confidence=float(row[5] or 1.0),
                    evidence=row[6],
                    status=row[7] or "active",
                    created_at=float(row[8] or 0),
                    updated_at=float(row[9]) if row[9] is not None else None,
                ))
            return edges

    async def edges_from(self, session_key: str, source_id: str) -> list[MemoryEdge]:
        return await self._query_edges(session_key, "source_id = :id", source_id)

    async def edges_to(self, session_key: str, target_id: str) -> list[MemoryEdge]:
        return await self._query_edges(session_key, "target_id = :id", target_id)

    async def edges_by_predicate(self, session_key: str, predicate: str) -> list[MemoryEdge]:
        if predicate not in VALID_PREDICATES:
            raise ValueError(f"Invalid predicate '{predicate}'")
        return await self._query_edges(session_key, "predicate = :id", predicate)

    async def _query_edges(
        self, session_key: str, where_clause: str, param_value: str
    ) -> list[MemoryEdge]:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _sql_text(
                    f"""
                    SELECT id, source_id, target_id, predicate,
                           condition, confidence, evidence, status,
                           created_at, updated_at
                    FROM pc_memory_edges
                    WHERE session_key = :sk AND status = 'active' AND {where_clause}
                    ORDER BY confidence DESC
                    """
                ),
                {"sk": session_key, "id": param_value},
            )
            edges: list[MemoryEdge] = []
            for row in result.fetchall():
                edges.append(MemoryEdge(
                    id=row[0],
                    source_id=row[1],
                    target_id=row[2],
                    predicate=row[3],
                    condition=row[4] or "",
                    confidence=float(row[5] or 1.0),
                    evidence=row[6],
                    status=row[7] or "active",
                    created_at=float(row[8] or 0),
                    updated_at=float(row[9]) if row[9] is not None else None,
                ))
            return edges

    def _sanitize_field(self, text: str, field_name: str) -> str:
        """DLP 脱敏单个字段。fail-open：脱敏失败返回原文。"""
        if not text or not text.strip():
            return text
        try:
            decision = self._dlp.scan(text)
            return decision.sanitized_text or text
        except Exception:
            logger.warning("EdgeStore DLP scan failed for %s", field_name, exc_info=True)
            return text

    async def edges_applicable_in_context(
        self, edges: list[MemoryEdge], context: str, llm_client: Any,
    ) -> list[MemoryEdge]:
        """从候选边中筛选出在当前上下文中生效的边（IE6）。

        - 无条件边（condition 为空字符串）直接通过
        - 有条件边经 LLM 判断条件是否成立
        - 条件求值前做注入检测，检测到注入则跳过该边
        - 条件求值结果在会话内缓存（IG3：LRU，TTL 5min）
        - prohibits 边 LLM 失败时默认通过约束（IG4：conservative）
        """
        unconditional = [e for e in edges if not e.condition]
        conditional = [e for e in edges if e.condition]
        if not conditional:
            return unconditional

        # 注入检测：复用 notary 的注入关键词列表
        from prismclaw.persistence.memory.notary import MemoryNotary
        notary = MemoryNotary()  # 仅用于注入检测，无 DLP

        safe_conditional: list[MemoryEdge] = []
        for edge in conditional:
            injection_result = notary.inspect(edge.condition)
            if injection_result.injection_score >= 0.3:
                logger.warning(
                    "[E5] Edge %s condition has injection score %.2f, skipping",
                    edge.id, injection_result.injection_score,
                )
                continue
            safe_conditional.append(edge)

        if not safe_conditional:
            return unconditional

        # 缓存检查（IG3）
        cache = _cache_instance
        context_prefix = context[:200]  # 截取前缀减少缓存 key 长度
        uncached: list[tuple[int, MemoryEdge]] = []
        cached_results: dict[int, bool] = {}
        for i, edge in enumerate(safe_conditional):
            cache_key = _condition_cache_key(edge, context_prefix)
            hit = await cache.get(cache_key)
            if hit is not None:
                cached_results[i] = hit
            else:
                uncached.append((i, edge))

        # LLM 条件求值（使用 PrismClaw 统一 LLM 协议：PackedLLMRequest + complete()）
        if uncached:
            prompt = (
                "判断以下条件在当前上下文中是否成立。每条只回答 true 或 false。\n\n"
                f"上下文：{context}\n\n"
            )
            for idx, (i, edge) in enumerate(uncached):
                prompt += f"{idx + 1}. 条件「{edge.condition}」→ true/false\n"

            try:
                from prismclaw.schema.llm.request import PackedLLMRequest

                llm_response = await llm_client.complete(
                    request=PackedLLMRequest(
                        messages=[{"role": "user", "content": prompt}],
                        system="",
                        context_payload=None,
                        cache_strategy={},
                    ),
                    temperature=0.0,
                )
                response_text = (getattr(llm_response, "text", "") or "").strip()
            except Exception:
                logger.warning("[E5] LLM condition evaluation failed", exc_info=True)
                # IG4：prohibits 边默认通过（conservative），其余丢弃（permissive）
                for i, edge in uncached:
                    if edge.predicate == "prohibits":
                        cached_results[i] = True
                return _merge_applicable(unconditional, safe_conditional, cached_results)

            # 解析 LLM 响应 + 写入缓存
            lines = response_text.split("\n") if response_text else []
            for idx, (i, edge) in enumerate(uncached):
                applicable = False
                if idx < len(lines):
                    answer = lines[idx].strip().lower()
                    if "true" in answer:
                        applicable = True
                cache_key = _condition_cache_key(edge, context_prefix)
                await cache.set(cache_key, applicable)
                cached_results[i] = applicable

        return _merge_applicable(unconditional, safe_conditional, cached_results)


# ── 向后兼容模块级函数 ──────────────────────────────────────

async def edges_applicable_in_context(
    edges: list[MemoryEdge],
    context: str,
    llm_client: Any,
) -> list[MemoryEdge]:
    """向后兼容别名 — 新代码应使用 EdgeStore.edges_applicable_in_context()。"""
    # 无状态函数需要一个临时 EdgeStore 实例来委托
    # 但实际上此函数有状态（依赖缓存），所以保留独立实现
    unconditional = [e for e in edges if not e.condition]
    conditional = [e for e in edges if e.condition]
    if not conditional:
        return unconditional

    from prismclaw.persistence.memory.notary import MemoryNotary
    notary = MemoryNotary()

    safe_conditional: list[MemoryEdge] = []
    for edge in conditional:
        injection_result = notary.inspect(edge.condition)
        if injection_result.injection_score >= 0.3:
            logger.warning(
                "[E5] Edge %s condition has injection score %.2f, skipping",
                edge.id, injection_result.injection_score,
            )
            continue
        safe_conditional.append(edge)

    if not safe_conditional:
        return unconditional

    cache = _cache_instance
    context_prefix = context[:200]
    uncached: list[tuple[int, MemoryEdge]] = []
    cached_results: dict[int, bool] = {}
    for i, edge in enumerate(safe_conditional):
        cache_key = _condition_cache_key(edge, context_prefix)
        hit = await cache.get(cache_key)
        if hit is not None:
            cached_results[i] = hit
        else:
            uncached.append((i, edge))

    if uncached:
        prompt = (
            "判断以下条件在当前上下文中是否成立。每条只回答 true 或 false。\n\n"
            f"上下文：{context}\n\n"
        )
        for idx, (i, edge) in enumerate(uncached):
            prompt += f"{idx + 1}. 条件「{edge.condition}」→ true/false\n"

        try:
            from prismclaw.schema.llm.request import PackedLLMRequest

            llm_response = await llm_client.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system="",
                    context_payload=None,
                    cache_strategy={},
                ),
                temperature=0.0,
            )
            response_text = (getattr(llm_response, "text", "") or "").strip()
        except Exception:
            logger.warning("[E5] LLM condition evaluation failed", exc_info=True)
            for i, edge in uncached:
                if edge.predicate == "prohibits":
                    cached_results[i] = True
            return _merge_applicable(unconditional, safe_conditional, cached_results)

        lines = response_text.split("\n") if response_text else []
        for idx, (i, edge) in enumerate(uncached):
            applicable = False
            if idx < len(lines):
                answer = lines[idx].strip().lower()
                if "true" in answer:
                    applicable = True
            cache_key = _condition_cache_key(edge, context_prefix)
            await cache.set(cache_key, applicable)
            cached_results[i] = applicable

    return _merge_applicable(unconditional, safe_conditional, cached_results)


def _merge_applicable(
    unconditional: list[MemoryEdge],
    conditional: list[MemoryEdge],
    results: dict[int, bool],
) -> list[MemoryEdge]:
    """合并无条件边和条件求值通过的边。"""
    applicable = list(unconditional)
    for i, edge in enumerate(conditional):
        if results.get(i, False):
            applicable.append(edge)
    return applicable


# ---------------------------------------------------------------------------
# 条件边求值缓存（IG3：内存 TTL 缓存，5min）
# ---------------------------------------------------------------------------

import asyncio as _asyncio
import hashlib as _hashlib


class _ConditionCache:
    """轻量 TTL 缓存，300s 过期，最多 512 条。asyncio.Lock 保护并发安全。"""

    _TTL = 300.0
    _MAX = 512

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, bool]] = {}
        self._lock = _asyncio.Lock()

    async def get(self, key: str) -> bool | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, val = entry
            if time.time() - ts > self._TTL:
                del self._store[key]
                return None
            return val

    async def set(self, key: str, value: bool) -> None:
        async with self._lock:
            if len(self._store) >= self._MAX:
                sorted_keys = sorted(self._store, key=lambda k: self._store[k][0])
                for k in sorted_keys[: self._MAX // 4]:
                    del self._store[k]
            self._store[key] = (time.time(), value)


# 模块级常量——模块加载时单线程执行，无竞态
_cache_instance = _ConditionCache()


def _condition_cache_key(edge: MemoryEdge, context_prefix: str) -> str:
    """缓存 key：edge_id + condition hash + context prefix hash。"""
    cond_hash = _hashlib.sha256(edge.condition.encode()).hexdigest()[:16]
    ctx_hash = _hashlib.sha256(context_prefix.encode()).hexdigest()[:16]
    return f"{edge.id}:{cond_hash}:{ctx_hash}"
