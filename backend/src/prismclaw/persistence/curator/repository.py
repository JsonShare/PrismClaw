"""PostgreSQL-backed CuratorSopRepository implementation.

SQL queries are extracted verbatim from the former engine/curator/ module-level
functions.  The only change is session acquisition: the old pattern created a
temporary ``async_sessionmaker(db_engine, ...)`` per call; this class uses
``self._sf`` injected at construction time.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.schema.agent import (
    ArchivedSopRow,
    ReviewDecision,
    ReviewOutcome,
    SopRow,
)
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.schema.shared.async_utils import await_or_abort

# SDD D9：验证信号毕业路径阈值——positive 累计两次且无反证即可毕业
# （count_validation_signals 只分 positive/negative 两类极性，无 neutral）。
_VALIDATION_POSITIVE_MIN = 2


def _graduation_candidates(
    aged: list[tuple[str, str]],
    use_eligible: set[str],
    validation_counts: dict[str, dict[str, int]],
) -> list[tuple[str, str]]:
    """SDD D9：毕业双路径候选裁决（纯函数）。

    use_count 路径（归因开启时生效）OR 验证信号路径（positive≥阈 且反证为
    零，不依赖归因）。两者都要求条目已过最小创建年龄（由调用方 SQL 过滤）。
    """
    eligible: set[str] = set(use_eligible)
    for entry_id, _content in aged:
        signals = validation_counts.get(entry_id)
        if signals is None:
            continue
        if (
            int(signals.get("positive", 0)) >= _VALIDATION_POSITIVE_MIN
            and int(signals.get("negative", 0)) == 0
        ):
            eligible.add(entry_id)
    return [(entry_id, content) for entry_id, content in aged if entry_id in eligible]

logger = logging.getLogger(__name__)

# SOP rows live in pc_memory_entries and are identified by type='auto_sop'.
_SOP_TYPE = "auto_sop"

# SDD A2 (defect D3, docs/memory-dream-sdd.md §1.1): review inputs are never
# truncated per-entry — the LLM must see each SOP in full to judge promote/
# archive. Prompt budget is controlled at the BATCH level instead.
_REVIEW_PROMPT_CHAR_BUDGET = 60_000

_REVIEW_PROMPT_TEMPLATE = """\
你是一个 SOP 质量审查员。审查以下自动生成的 SOPs，对每条给出决策：

可用决策: {actions}
- keep: 质量好，继续保留
- promote: 高频使用 + 高质量，推荐升级为正式 Skill（忽略 use_count 阈值）
- archive: 质量差或内容有害，归档

审查标准：
1. 是否是 CLASS-LEVEL 通用过程（非 instance-specific）
2. 步骤是否清晰可执行
3. 是否有安全风险
4. 内容是否过时或错误

以下是待审查的 SOPs:

{entries}

输出 JSON 数组: [{{"id": "...", "decision": "keep|promote|archive", "reason": "..."}}]
仅输出 JSON，无其他文字。
"""

# ---------------------------------------------------------------------------
# Row mappers (from admin.py)
# ---------------------------------------------------------------------------


def _session_clause(session_key: str | None) -> tuple[str, dict[str, Any]]:
    """Build an optional ``AND session_key = :sk`` fragment + bound params."""
    if session_key is None:
        return "", {}
    return "AND session_key = :sk", {"sk": session_key}


def _row_to_sop(row: Any) -> SopRow:
    return SopRow(
        entry_id=str(row[0]),
        session_key=str(row[1]),
        content=str(row[2]),
        use_count=int(row[3] or 0),
        last_used_at=float(row[4]) if row[4] is not None else None,
    )


def _parse_review_decisions(llm_output: str, allowed_actions: list[str]) -> list[ReviewDecision]:
    text_output = llm_output.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", text_output)
    if fence_match:
        text_output = fence_match.group(1)

    try:
        items = _json.loads(text_output)
    except _json.JSONDecodeError:
        arr_match = re.search(r"\[[\s\S]*\]", text_output)
        if arr_match:
            try:
                items = _json.loads(arr_match.group(0))
            except _json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(items, list):
        return []

    decisions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        eid = item.get("id", "")
        decision_val = item.get("decision", "")
        reason = item.get("reason", "")
        if decision_val in allowed_actions or decision_val == "keep":
            decisions.append(ReviewDecision(id=eid, decision=decision_val, reason=reason))

    return decisions


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PostgresCuratorSopRepository:
    """Concrete CuratorSopRepository backed by Postgres via SQLAlchemy."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        graduator: Callable | None = None,
    ) -> None:
        self._sf = session_factory
        # ``graduate_single_sop`` is engine business logic;
        # persistence must not import it directly.  The wiring layer injects the
        # callable at construction time so that scan_session / review_session can
        # invoke graduation without violating the layer boundary.
        self._graduator = graduator

    # ------------------------------------------------------------------
    # 1. list_active_sop_sessions  (from engine/curator/__init__::_list_active_sop_sessions)
    # ------------------------------------------------------------------

    async def list_active_sop_sessions(self) -> list[str]:
        """Distinct session_keys that currently own active SOPs."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT DISTINCT session_key FROM pc_memory_entries "
                    f"WHERE type = '{_SOP_TYPE}' AND status = 'active'"
                )
            )
            return [str(row[0]) for row in result]

    # ------------------------------------------------------------------
    # 2. scan_session  (from engine/curator/__init__::_scan_session)
    # ------------------------------------------------------------------

    async def scan_session(
        self,
        session_key: str,
        archive_days: int,
        l1_index: Any,
        workspace_base_dir: Path | None = None,
        settings: Any = None,
        count_validation_signals: Callable | None = None,
    ) -> tuple[int, int]:
        """Archive stale SOPs and graduate high-use ones for one session_key."""

        threshold = time.time() - archive_days * 86400

        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id FROM pc_memory_entries "
                    f"WHERE session_key = :sk AND type = '{_SOP_TYPE}' AND status = 'active' "
                    "AND COALESCE(last_used_at, created_at) < :threshold"
                ),
                {"sk": session_key, "threshold": threshold},
            )
            stale_ids = [str(row[0]) for row in result]

        archived_count = 0
        if stale_ids:
            now = time.time()
            reason = f"curator:{archive_days}d_unused"
            async with self._sf() as session:
                await session.execute(
                    text(
                        "UPDATE pc_memory_entries SET status = 'archived', updated_at = :now "
                        "WHERE id IN :ids AND status = 'active'"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"now": now, "ids": stale_ids},
                )
                await session.commit()
            archived_count = len(stale_ids)
            logger.info(
                "curator archived %d stale SOPs in %s (%s)",
                archived_count,
                session_key,
                reason,
            )
            for entry_id in stale_ids:
                if l1_index is not None:
                    try:
                        await l1_index.index_remove(session_key, entry_id)
                    except Exception:
                        logger.debug(
                            "L1 evict failed for %s:%s", session_key, entry_id, exc_info=True
                        )

        graduated_count = 0
        if settings and getattr(settings, "graduation_enabled", False) and workspace_base_dir:
            min_use = int(getattr(settings, "promotion_min_use_count", 5))
            min_days_ts = time.time() - int(getattr(settings, "promotion_min_days", 7)) * 86400

            async with self._sf() as session:
                result = await session.execute(
                    text(
                        "SELECT id, content FROM pc_memory_entries "
                        f"WHERE session_key = :sk AND type = '{_SOP_TYPE}' AND status = 'active' "
                        "AND created_at <= :min_days"
                    ),
                    {"sk": session_key, "min_days": min_days_ts},
                )
                aged = [(str(r[0]), str(r[1])) for r in result]
                result_use = await session.execute(
                    text(
                        "SELECT id FROM pc_memory_entries "
                        f"WHERE session_key = :sk AND type = '{_SOP_TYPE}' AND status = 'active' "
                        "AND use_count >= :min_use AND created_at <= :min_days"
                    ),
                    {"sk": session_key, "min_use": min_use, "min_days": min_days_ts},
                )
                use_eligible = {str(row[0]) for row in result_use}

            # SDD D9：毕业双路径——use_count（归因开启时生效）OR 验证信号
            # （positive>=2 且 negative==0，不依赖归因）。use_count 在归因
            # 关闭时自然为 0，不会误毕业；验证信号路径补上闭环缺口。
            counts: dict[str, dict[str, int]] = {}
            if count_validation_signals is not None and aged:
                need = [entry_id for entry_id, _content in aged if entry_id not in use_eligible]
                counts = await count_validation_signals(session_key, need)

            candidates = _graduation_candidates(aged, use_eligible, counts)

            if candidates and self._graduator is not None:
                grad_mode = getattr(settings, "graduation_mode", "template")
                for entry_id, content in candidates:
                    success, skill_path = self._graduator(
                        entry_id=entry_id,
                        content=content,
                        session_key=session_key,
                        workspace_base_dir=workspace_base_dir,
                        mode=grad_mode,
                    )
                    if success:
                        async with self._sf() as session:
                            await session.execute(
                                text(
                                    "UPDATE pc_memory_entries "
                                    "SET status = 'graduated', updated_at = :now "
                                    "WHERE id = :eid"
                                ),
                                {"now": time.time(), "eid": entry_id},
                            )
                            await session.commit()
                        if l1_index is not None:
                            try:
                                await l1_index.index_remove(session_key, entry_id)
                            except Exception:
                                logger.debug(
                                    "L1 evict failed for %s:%s",
                                    session_key,
                                    entry_id,
                                    exc_info=True,
                                )
                        graduated_count += 1
                        logger.info("Graduated SOP %s → %s", entry_id[:8], skill_path)

        return archived_count, graduated_count

    # ------------------------------------------------------------------
    # 3. review_session  (from engine/curator/__init__::_review_session)
    # ------------------------------------------------------------------

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
    ) -> ReviewOutcome:
        """Per-session LLM review — pure function, no Redis side effects."""
        check_alive()

        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id, content, use_count FROM pc_memory_entries "
                    f"WHERE session_key = :sk AND type = '{_SOP_TYPE}' AND status = 'active' "
                    "ORDER BY use_count DESC LIMIT :limit"
                ),
                {"sk": session_key, "limit": int(settings.llm_review_max_batch)},
            )
            entries = [(str(r[0]), str(r[1]), int(r[2] or 0)) for r in result]

        if not entries:
            return ReviewOutcome(
                session_key=session_key,
                entries_reviewed=0,
                promoted_count=0,
                archived_count=0,
                failed_count=0,
            )

        # SDD A2: whole-entry contents only; batch shrinks under budget instead
        # of any entry being truncated.
        review_batch: list[tuple[str, str, int]] = []
        used_chars = 0
        for eid, content, uc in entries:
            entry_chars = len(content) + 32  # id/use_count wrapper overhead
            if review_batch and used_chars + entry_chars > _REVIEW_PROMPT_CHAR_BUDGET:
                break
            review_batch.append((eid, content, uc))
            used_chars += entry_chars

        if not review_batch:
            return ReviewOutcome(
                session_key=session_key,
                entries_reviewed=0,
                promoted_count=0,
                archived_count=0,
                failed_count=0,
            )

        entries_text = "\n".join(
            f"[id={eid}, use_count={uc}]\n{content}\n---" for eid, content, uc in review_batch
        )
        actions_text = "/".join(settings.llm_review_actions)

        prompt = _REVIEW_PROMPT_TEMPLATE.format(actions=actions_text, entries=entries_text)

        model = settings.llm_review_model
        check_alive()
        try:
            response = await await_or_abort(
                llm_client.complete(
                    request=PackedLLMRequest(
                        messages=[{"role": "user", "content": prompt}],
                        system="",
                        context_payload=None,
                        cache_strategy={},
                    ),
                    model=model,
                ),
                abort_event,
                timeout=30.0,
            )
            llm_output = response.text
        except Exception as exc:
            logger.warning("LLM review call failed: %s", exc)
            return ReviewOutcome(
                session_key=session_key,
                entries_reviewed=len(review_batch),
                promoted_count=0,
                archived_count=0,
                failed_count=1,
            )

        decisions = _parse_review_decisions(llm_output, settings.llm_review_actions)
        promoted_count = 0
        archived_count = 0
        failed_count = 0
        entry_map = {eid: content for eid, content, _uc in review_batch}

        for decision in decisions:
            if decision.id not in entry_map:
                continue
            content = entry_map[decision.id]

            if decision.decision == "promote" and "promote" in settings.llm_review_actions:
                if not getattr(settings, "graduation_enabled", True):
                    logger.info(
                        "LLM review suggests promote for %s but graduation disabled",
                        decision.id[:8],
                    )
                    continue

                if self._graduator is None:
                    logger.warning(
                        "LLM review suggests promote for %s but no graduator configured",
                        decision.id[:8],
                    )
                    continue

                grad_mode = getattr(settings, "graduation_mode", "template")
                success, _ = self._graduator(
                    entry_id=decision.id,
                    content=content,
                    session_key=session_key,
                    workspace_base_dir=workspace_base_dir,
                    mode=grad_mode,
                )
                if success:
                    check_alive()
                    async with self._sf() as session:
                        await session.execute(
                            text(
                                "UPDATE pc_memory_entries "
                                "SET status = 'graduated', updated_at = :now "
                                "WHERE id = :eid"
                            ),
                            {"now": time.time(), "eid": decision.id},
                        )
                        await session.commit()
                    if l1_index:
                        try:
                            await l1_index.index_remove(session_key, decision.id)
                        except Exception:
                            pass
                    promoted_count += 1
                    logger.info("LLM review promoted %s", decision.id[:8])
                else:
                    failed_count += 1

            elif decision.decision == "archive" and "archive" in settings.llm_review_actions:
                reason_text = f"llm_review: {decision.reason[:100]}"
                now = time.time()
                check_alive()
                async with self._sf() as session:
                    await session.execute(
                        text(
                            "UPDATE pc_memory_entries "
                            "SET status = 'archived', updated_at = :now "
                            "WHERE id = :eid"
                        ),
                        {"now": now, "eid": decision.id},
                    )
                    await session.commit()
                if l1_index:
                    try:
                        await l1_index.index_remove(session_key, decision.id)
                    except Exception:
                        pass
                archived_count += 1
                logger.info(
                    "LLM review archived %s: %s", decision.id[:8], reason_text
                )

        return ReviewOutcome(
            session_key=session_key,
            entries_reviewed=len(review_batch),
            promoted_count=promoted_count,
            archived_count=archived_count,
            failed_count=failed_count,
        )

    # ------------------------------------------------------------------
    # 4. list_auto_sops
    # ------------------------------------------------------------------

    async def list_auto_sops(
        self,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]:
        clause, params = _session_clause(session_key)
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id, session_key, content, use_count, last_used_at "
                    "FROM pc_memory_entries "
                    f"WHERE type = '{_SOP_TYPE}' AND status = 'active' {clause}"
                ),
                params,
            )
            return [_row_to_sop(r) for r in result]

    # ------------------------------------------------------------------
    # 5. list_stale_sops
    # ------------------------------------------------------------------

    async def list_stale_sops(
        self,
        settings: Any,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]:
        stale_days = settings.evolution.curator.stale_after_days
        threshold = time.time() - stale_days * 86400
        clause, params = _session_clause(session_key)
        params["threshold"] = threshold
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id, session_key, content, use_count, last_used_at "
                    "FROM pc_memory_entries "
                    f"WHERE type = '{_SOP_TYPE}' AND status = 'active' "
                    "AND COALESCE(last_used_at, created_at) < :threshold "
                    f"{clause}"
                ),
                params,
            )
            return [_row_to_sop(r) for r in result]

    # ------------------------------------------------------------------
    # 6. list_archived_sops
    # ------------------------------------------------------------------

    async def list_archived_sops(
        self,
        *,
        session_key: str | None = None,
    ) -> list[ArchivedSopRow]:
        clause, params = _session_clause(session_key)
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id, session_key, content, updated_at "
                    "FROM pc_memory_entries "
                    f"WHERE type = '{_SOP_TYPE}' AND status = 'archived' {clause}"
                ),
                params,
            )
            return [
                ArchivedSopRow(
                    entry_id=str(r[0]),
                    session_key=str(r[1]),
                    content=str(r[2]),
                    archived_at=float(r[3]) if r[3] is not None else None,
                )
                for r in result
            ]

    # ------------------------------------------------------------------
    # 7. preview_graduation
    # ------------------------------------------------------------------

    async def preview_graduation(
        self,
        settings: Any,
        *,
        session_key: str | None = None,
    ) -> list[SopRow]:
        min_use = getattr(settings.evolution.curator, "promotion_min_use_count", 5)
        min_days = getattr(settings.evolution.curator, "promotion_min_days", 7)
        threshold = time.time() - min_days * 86400
        clause, params = _session_clause(session_key)
        params["min_use"] = min_use
        params["min_days"] = threshold
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "SELECT id, session_key, content, use_count, last_used_at "
                    "FROM pc_memory_entries "
                    f"WHERE type = '{_SOP_TYPE}' AND status = 'active' "
                    f"AND use_count >= :min_use AND created_at <= :min_days {clause}"
                ),
                params,
            )
            return [_row_to_sop(r) for r in result]

    # ------------------------------------------------------------------
    # 8. restore_sop
    # ------------------------------------------------------------------

    async def restore_sop(
        self,
        entry_id: str,
        *,
        session_key: str | None = None,
    ) -> int:
        """Re-activate a single archived auto_sop entry by id.

        Returns the number of rows updated.
        """
        clause, params = _session_clause(session_key)
        params["eid"] = entry_id
        params["now"] = time.time()
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "UPDATE pc_memory_entries "
                    "SET status = 'active', updated_at = :now "
                    f"WHERE id = :eid AND type = '{_SOP_TYPE}' AND status = 'archived' {clause}"
                ),
                params,
            )
            await session.commit()
            count = int(result.rowcount or 0)
        return count
