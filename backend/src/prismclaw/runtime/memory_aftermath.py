from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from prismclaw.schema.dream.dream import DreamEnqueueBoundary
from prismclaw.schema.dream.dream_types import DreamEnqueueResult
from prismclaw.engine.dream.candidates import DreamCandidateCollector, DreamCandidateStore
from prismclaw.schema.agent.context import MemoryAftermathBoundary
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session.ids import derive_memory_session_key
from prismclaw.engine.memory.archive import archive_session_background

logger = logging.getLogger(__name__)

# SDD A4: per-session chat-turn buffer bound — the boundary lives for the
# app lifetime, sessions that never consolidate must not leak their buffers.
_CHAT_TURN_BUFFER_CAP = 100


class NullMemoryAftermathBoundary:
    async def after_turn(self, session_id: str) -> None:
        _ = session_id

    async def consolidate_session(self, session_id: str) -> bool:
        _ = session_id
        return False

    def set_session_store(self, session_store: Any) -> None:
        _ = session_store


class DefaultMemoryAftermathBoundary:
    def __init__(
        self,
        *,
        memory_store: Any = None,
        session_store: Any = None,
        task_manager: Any = None,
        redis_client: Any = None,
        llm_client: Any = None,
        evolution_settings: Any = None,
        nudge_hook: Any = None,
        dream_boundary: DreamEnqueueBoundary | None = None,
        dream_candidate_store: DreamCandidateStore | None = None,
        attribution_recorder: Any = None,
        memory_gateway: Any = None,
        edge_store: Any = None,
        validation_ledger: Any = None,
    ) -> None:
        self._memory_store = memory_store
        self._session_store = session_store
        self._task_manager = task_manager
        self._redis_client = redis_client
        self._llm_client = llm_client
        self._evolution_settings = evolution_settings
        self._nudge_hook = nudge_hook
        self._dream_boundary = dream_boundary
        self._dream_candidate_store = dream_candidate_store
        # SDD B1: attribution recorder shared with MemoryRetriever.
        self._attribution_recorder = attribution_recorder
        # SDD D9/T1-5: 无归因环境的最小验证信号簿记（独立于 attribution 开关）。
        self._validation_ledger = validation_ledger
        # SDD B2: write arbitration gateway for auto-extracted chat facts.
        self._memory_gateway = memory_gateway
        # 记忆图谱边存储，会话结束时抽取关系
        self._edge_store = edge_store
        self._relation_extractor: Any = None  # 懒初始化
        # SDD A4: per-session buffer of no-tool turn transcripts, flushed as
        # one batched extraction at consolidate_session time.
        self._chat_turn_buffer: dict[str, list[dict[str, str]]] = {}
        self._dream_candidate_collector: DreamCandidateCollector | None = None
        self._refresh_dream_candidate_collector()

    def set_session_store(self, session_store: Any) -> None:
        self._session_store = session_store
        self._refresh_dream_candidate_collector()

    async def after_turn(self, session_id: str) -> None:
        session_key = derive_memory_session_key(session_id)
        await self._mark_dream_activity(session_key)
        await self._capture_turn_candidate(session_id, session_key)
        await self._buffer_chat_turn(session_id)
        await self._record_attribution_signals(session_id)
        await self._maybe_extract_memories(session_id)

    async def consolidate_session(self, session_id: str) -> bool:
        if self._memory_store is None or self._session_store is None:
            return False
        await self._flush_chat_facts(session_id)
        await archive_session_background(
            self._memory_store,
            self._session_store,
            session_id,
            llm_client=self._llm_client,
        )
        session_key = derive_memory_session_key(session_id)
        await self._enqueue_nightly(session_key)
        await self._capture_session_candidate(session_id, session_key)
        return True

    async def _mark_dream_activity(self, session_key: str) -> DreamEnqueueResult | None:
        if self._dream_boundary is None:
            return None
        return await self._dream_boundary.mark_activity(session_key)

    async def _enqueue_nightly(self, session_key: str) -> DreamEnqueueResult | None:
        if self._dream_boundary is None:
            return None
        return await self._dream_boundary.enqueue_nightly(session_key)

    def _refresh_dream_candidate_collector(self) -> None:
        if self._session_store is None or self._dream_candidate_store is None:
            self._dream_candidate_collector = None
            return
        self._dream_candidate_collector = DreamCandidateCollector(
            session_store=self._session_store,
            candidate_store=self._dream_candidate_store,
        )

    async def _capture_turn_candidate(self, session_id: str, session_key: str) -> None:
        if self._dream_candidate_collector is None:
            return
        try:
            await self._dream_candidate_collector.capture_turn(session_id, session_key)
        except Exception:
            logger.warning("dream turn candidate capture failed for %s", session_id, exc_info=True)

    async def _capture_session_candidate(self, session_id: str, session_key: str) -> None:
        if self._dream_candidate_collector is None:
            return
        try:
            await self._dream_candidate_collector.capture_session(session_id, session_key)
        except Exception:
            logger.warning("dream session candidate capture failed for %s", session_id, exc_info=True)

    def _capture_fullturn_enabled(self) -> bool:
        return bool(
            getattr(self._evolution_settings, "capture_fullturn_enabled", False)
        )

    async def _buffer_chat_turn(self, session_id: str) -> None:
        """SDD A4: buffer the latest turn's transcript when it had no tool calls.

        Tool-using turns stay with the SOP pipeline (its min_tool_calls gate);
        pure-chat turns would otherwise never produce durable memory.

        Tool-turn detection must scan EVERYTHING after the last user message:
        a tool turn's tree is user → assistant(tool_calls) → tool → assistant
        (final reply, no tool_calls) — checking only the final assistant entry
        would misclassify tool turns as chat turns.
        """
        if not self._capture_fullturn_enabled():
            return
        if self._memory_store is None or self._session_store is None:
            return
        try:
            from prismclaw.schema.agent.session import MessageEntry

            tree = await self._session_store.load(session_id)
            if tree is None:
                return
            branch = tree.get_branch()
            last_user_idx: int | None = None
            for idx in range(len(branch) - 1, -1, -1):
                entry = branch[idx]
                if isinstance(entry, MessageEntry) and entry.role == "user":
                    last_user_idx = idx
                    break
            if last_user_idx is None:
                return

            has_tool_use = False
            last_assistant: MessageEntry | None = None
            for entry in branch[last_user_idx + 1:]:
                if not isinstance(entry, MessageEntry):
                    continue
                if entry.role == "tool":
                    has_tool_use = True
                elif entry.role == "assistant":
                    last_assistant = entry
                    if entry.tool_calls:
                        has_tool_use = True
            if last_assistant is None or has_tool_use:
                return  # no response yet, or tool-using turn → SOP pipeline owns it

            from prismclaw.schema.shared import extract_text_from_content

            user_text = extract_text_from_content(branch[last_user_idx].content)
            assistant_text = extract_text_from_content(last_assistant.content)
            if not user_text.strip() and not assistant_text.strip():
                return
            buffer = self._chat_turn_buffer.setdefault(session_id, [])
            buffer.append({"user": user_text, "assistant": assistant_text})
            # Bound: the boundary lives for the app lifetime; sessions that
            # never consolidate must not leak their buffers.
            if len(buffer) > _CHAT_TURN_BUFFER_CAP:
                del buffer[: len(buffer) - _CHAT_TURN_BUFFER_CAP]
        except Exception:
            logger.warning(
                "chat turn buffering failed for %s", session_id, exc_info=True
            )

    async def _flush_chat_facts(self, session_id: str) -> None:
        """Session-end batched extraction over the buffered chat turns.

        One LLM call per session (budget-gated by MIN_CHAT_TURNS_FOR_EXTRACTION);
        facts persist via the memorize store() path with a low initial
        confidence. Buffer is cleared regardless of outcome.
        """
        turns = self._chat_turn_buffer.pop(session_id, None)
        if not turns:
            return
        if self._llm_client is None or self._memory_store is None:
            return
        try:
            from prismclaw.engine.memory.capture import (
                MIN_CHAT_TURNS_FOR_EXTRACTION,
                extract_chat_facts,
                store_chat_facts,
            )

            if len(turns) < MIN_CHAT_TURNS_FOR_EXTRACTION:
                return
            session_key = derive_memory_session_key(session_id)
            facts = await extract_chat_facts(self._llm_client, turns)
            if facts:
                stored = await store_chat_facts(
                    self._memory_store,
                    session_key=session_key,
                    session_id=session_id,
                    facts=facts,
                    gateway=self._memory_gateway,
                )
                logger.info(
                    "[SDD-A4] chat fact extraction: session=%s turns=%d facts=%d",
                    session_id,
                    len(turns),
                    stored,
                )

            # 关系抽取（在 chat_facts 之后触发，数据源是 turns 而非 facts）
            if self._edge_store is not None:
                try:
                    if self._relation_extractor is None:
                        from prismclaw.engine.memory.relation_extractor import RelationExtractor
                        self._relation_extractor = RelationExtractor()
                    combined_text = "\n".join(
                        f"用户: {t.get('user', '')}\n助手: {t.get('assistant', '')}"
                        for t in turns
                    )
                    relations = await self._relation_extractor.extract(
                        combined_text, llm_client=self._llm_client,
                    )
                    if relations:
                        stored_edges = await self._relation_extractor.resolve_and_store(
                            session_key, relations,
                            memory=self._memory_store, edge_store=self._edge_store,
                        )
                        if stored_edges:
                            logger.info(
                                "[E6] relation extraction: session=%s edges=%d",
                                session_id, stored_edges,
                            )
                except Exception:
                    logger.warning(
                        "[E6] relation extraction failed for %s", session_id, exc_info=True
                    )
        except Exception:
            logger.warning(
                "chat fact extraction failed for %s", session_id, exc_info=True
            )

    async def _record_attribution_signals(self, session_id: str) -> None:
        """SDD B1: seal the turn's attribution + collect validation signals.

        Order matters:
        1. user-correction detection runs against LAST turn's injections
           (must happen before mark_turn_complete overwrites them);
        2. task-outcome signals apply to THIS turn's pending injections;
        3. mark_turn_complete flushes the reference log + use_count bump
           and stores this turn's injections for the next turn's check.

        SDD D9/T1-5 闭环：recorder 缺失（attribution_enabled=False）时走
        validation ledger 影子路径——task-outcome 信号 + use_count 回写不
        依赖归因开关，curator 毕业路径 B 因此可持续供给（路径 A 由本次
        use_count 回写补齐）。
        """
        recorder = self._attribution_recorder
        ledger = self._validation_ledger
        if recorder is None and ledger is None:
            return
        if self._session_store is None:
            return
        try:
            from prismclaw.schema.shared import extract_text_from_content

            tree = await self._session_store.load(session_id)
            if tree is None:
                return
            branch = tree.get_branch()

            # Locate the last user entry → turn boundary + turn_seq.
            last_user_idx: int | None = None
            turn_seq = 0
            for idx in range(len(branch) - 1, -1, -1):
                entry = branch[idx]
                if isinstance(entry, MessageEntry) and entry.role == "user":
                    last_user_idx = idx
                    break
            if last_user_idx is None:
                if recorder is not None:
                    recorder.mark_turn_complete(session_id, turn_seq)
                return
            turn_seq = sum(
                1
                for e in branch[: last_user_idx + 1]
                if isinstance(e, MessageEntry) and e.role == "user"
            )
            user_text = extract_text_from_content(branch[last_user_idx].content)
            session_key = derive_memory_session_key(session_id)

            # ── 影子 ledger 路径（无归因环境的最小闭环） ──────────────
            if recorder is None:
                assert ledger is not None
                pending_ids = ledger.take_pending(session_id)
                outcome = self._scan_task_outcome(branch, last_user_idx, pending_ids)
                if outcome is not None and pending_ids:
                    occurred = time.time()
                    events = [
                        {
                            "id": f"vs_{uuid.uuid4().hex[:12]}",
                            "entry_id": entry_id,
                            "signal_type": outcome[0],
                            "polarity": outcome[1],
                            "attribution": "weak",
                            "source_ref": f"{session_id}:turn{turn_seq}",
                            "occurred_at": occurred,
                            "payload": {},
                        }
                        for entry_id in pending_ids
                    ]
                    await ledger.record_events(session_key, events)
                    await ledger.bump_use_counts(session_key, pending_ids)
                return

            # ── 完整归因路径（attribution_enabled=True） ───────────────
            # 1. Correction detection vs LAST turn's injections (strong).
            events: list[dict[str, Any]] = []
            last_injections = recorder.last_turn_injections(session_id)
            if last_injections and user_text:
                from prismclaw.engine.memory.attribution import detect_user_correction

                corrected_ids = detect_user_correction(user_text, last_injections)
                for entry_id in corrected_ids:
                    events.append(
                        recorder.build_validation_event(
                            entry_id=entry_id,
                            signal_type="user_correction",
                            polarity="negative",
                            attribution="strong",
                            source_ref=f"{session_id}:turn{turn_seq}",
                            payload={"user_message": user_text[:500]},
                        )
                    )

            # 2. Task-outcome signals for THIS turn's pending injections (weak).
            pending_ids = recorder.pending_entry_ids(session_id)
            outcome = self._scan_task_outcome(branch, last_user_idx, pending_ids)
            if outcome is not None:
                for entry_id in pending_ids:
                    events.append(
                        recorder.build_validation_event(
                            entry_id=entry_id,
                            signal_type=outcome[0],
                            polarity=outcome[1],
                            attribution="weak",
                            source_ref=f"{session_id}:turn{turn_seq}",
                        )
                    )

            if events:
                await recorder.record_validation_events(session_id, events)

            # 3. Seal the turn (flush reference log + use_count; keep for next).
            recorder.mark_turn_complete(session_id, turn_seq)
        except Exception:
            logger.warning(
                "attribution signal collection failed for %s", session_id, exc_info=True
            )

    @staticmethod
    def _scan_task_outcome(
        branch: list[Any],
        last_user_idx: int,
        candidate_ids: list[str],
    ) -> tuple[str, str] | None:
        """本回合 tool/assistant 轨迹 → (signal_type, polarity) 或 None。

        有工具报错 → task_failure/negative；有成功工具且有回复 → task_success/
        positive；纯聊天回合答案正确性不可知 → 无信号。
        """
        if not candidate_ids:
            return None
        turn_slice = branch[last_user_idx + 1:]
        has_tool_error = False
        has_tool = False
        has_reply = False
        for entry in turn_slice:
            if not isinstance(entry, MessageEntry):
                continue
            if entry.role == "tool":
                has_tool = True
                if getattr(entry, "is_error", False):
                    has_tool_error = True
            elif entry.role == "assistant":
                has_reply = True
        if has_tool_error:
            return ("task_failure", "negative")
        if has_tool and has_reply:
            return ("task_success", "positive")
        # Pure-chat turns: answer correctness is unknowable — no signal.
        return None

    async def _maybe_extract_memories(self, session_id: str) -> bool:
        # Only memory_store and session_store are genuine runtime-optionals.
        # redis_client, task_manager, llm_client, evolution_settings are guaranteed
        # by the assembly layer (see runner_assembly._validate_memory_runtime_prerequisites).
        if self._memory_store is None or self._session_store is None:
            return False
        try:
            from prismclaw.engine.services.sop_extraction import maybe_spawn_extraction

            result = await maybe_spawn_extraction(
                task_manager=self._task_manager,
                memory_store=self._memory_store,
                session_store=self._session_store,
                redis_client=self._redis_client,
                llm_client=self._llm_client,
                session_id=session_id,
                settings=self._evolution_settings,
                nudge_hook=self._nudge_hook,
            )
            logger.info(
                "[PROBE R1] memory extraction completed: session=%s result=%r",
                session_id, result,
            )
            return result
        except Exception:
            logger.warning(
                "[PROBE R1] durable memory extraction trigger failed for session %s",
                session_id,
                exc_info=True,
            )
            return False


def build_memory_aftermath_boundary(
    *,
    memory_store: Any = None,
    session_store: Any = None,
    task_manager: Any = None,
    redis_client: Any = None,
    llm_client: Any = None,
    evolution_settings: Any = None,
    nudge_hook: Any = None,
    dream_boundary: DreamEnqueueBoundary | None = None,
    dream_candidate_store: DreamCandidateStore | None = None,
    attribution_recorder: Any = None,
    memory_gateway: Any = None,
    edge_store: Any = None,
    validation_ledger: Any = None,
) -> MemoryAftermathBoundary:
    if (
        memory_store is None
        and session_store is None
        and task_manager is None
        and redis_client is None
        and llm_client is None
        and evolution_settings is None
        and nudge_hook is None
        and dream_boundary is None
        and dream_candidate_store is None
        and attribution_recorder is None
        and memory_gateway is None
        and validation_ledger is None
    ):
        return NullMemoryAftermathBoundary()
    return DefaultMemoryAftermathBoundary(
        memory_store=memory_store,
        session_store=session_store,
        task_manager=task_manager,
        redis_client=redis_client,
        llm_client=llm_client,
        evolution_settings=evolution_settings,
        nudge_hook=nudge_hook,
        dream_boundary=dream_boundary,
        dream_candidate_store=dream_candidate_store,
        attribution_recorder=attribution_recorder,
        memory_gateway=memory_gateway,
        edge_store=edge_store,
        validation_ledger=validation_ledger,
    )


__all__ = [
    "DefaultMemoryAftermathBoundary",
    "NullMemoryAftermathBoundary",
    "build_memory_aftermath_boundary",
]
