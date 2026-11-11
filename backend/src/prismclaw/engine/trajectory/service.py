"""TrajectoryAuditService — the one thing that connects bus→projection→persistence.

Phase 0' 解耦 critical deliverable: **core code never imports
`prismclaw.engine.trajectory`**. Instead:

1. Core runner creates a `SessionBus` and publishes event types
   declared in `prismclaw.engine.bus_events`.
2. TrajectoryAuditService is a trajectory-owned class that calls
   `bus.subscribe(...)` — **the dependency arrow runs from
   trajectory → bus (core-owned), not from core → trajectory**.
3. On `SessionTurnFinished`, service's subscriber pushes a dirty signal
   to `TrajectoryFlushWorker` (non-blocking, O(1)). The worker flushes
   to PostgreSQL asynchronously in the background.
4. Unloading the service calls `worker.stop()` (drain + final flush)
   then `bus.unsubscribe_all()` — zero leftover trajectory references.

All I/O goes through the injected TrajectoryPersistence Protocol —
the engine layer never imports persistence directly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from prismclaw.schema.trajectory.trajectory_types import TrajectoryCell
from prismclaw.schema.trajectory.trajectory import TrajectoryPersistence
from prismclaw.engine.trajectory.bus import SessionBus
from prismclaw.engine.trajectory import bus_events as evmod

from .flush_worker import TrajectoryFlushWorker
from .projection import (
    TrajectoryProjectionState,
    apply,
    init_state,
    view,
)

logger = logging.getLogger(__name__)


class TrajectoryAuditService:
    """Instance = one session's trajectory audit subscription.

    Create, register to bus, then forget. Cleanup: close() stops the
    flush worker (drains residual queue + final flush) then unsubscribes.
    """

    __slots__ = (
        "bus",
        "session_id",
        "state",
        "_persistence",
        "_subscribed",
        "_handlers_registered",
        "_flush_worker",
        "_tool_registry",
        "_schema_cache",
        "_pending_system_cell",
    )

    def __init__(
        self,
        session_id: str,
        bus: SessionBus,
        *,
        persistence: TrajectoryPersistence | None = None,
        tool_registry: Any = None,
    ) -> None:
        self.session_id = session_id
        self.bus = bus
        self._persistence = persistence
        self.state: TrajectoryProjectionState = init_state(session_id)
        self._subscribed = False
        self._handlers_registered = False
        self._flush_worker = TrajectoryFlushWorker(
            session_id, persistence,
        )
        self._tool_registry = tool_registry
        # name → schema JSON (session-lifetime cache; the service lives
        # for one turn, so tool-schema reload staleness is bounded)
        self._schema_cache: dict[str, str | None] = {}
        # Buffer the CURRENT turn's system-prompt snapshot; flushed ONCE
        # at turn end (final state wins — intra-turn changes carry no
        # audit value and fire-and-forget task interleaving would make
        # last-writer ordering unreliable anyway).
        self._pending_system_cell: dict[str, Any] | None = None

    # ------------------------------------------------------------------ life

    async def start(self, *, restore_from_db: bool = True) -> "TrajectoryAuditService":
        """Load prior state (if any) + register event handlers on self.bus.

        Async because it does one query for session state reconstruction
        on hot-session restarts. Idempotent: calling start() twice is a NOP
        (return self; no double subscription).
        """
        if self._subscribed:
            return self
        if restore_from_db and self._persistence is not None:
            try:
                prior = await self._persistence.load_state(self.session_id)
                if prior.session_id and (
                    prior.session_id == self.session_id
                    or self.session_id in (None, "")
                ):
                    self.state = prior
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "trajectory audit service start: restore failed for "
                    "sid=%s; starting clean. %s", self.session_id, exc,
                )
        # Start the async flush worker
        await self._flush_worker.start()
        if not self._handlers_registered:
            self._register_handlers()
        self._subscribed = True
        return self

    def preregister_handlers(self) -> None:
        """Register bus subscriptions synchronously, WITHOUT marking started.

        Used by callers that need subscribers in place BEFORE the async
        start() (which does restore_from_db + flush_worker.start()) completes.
        start() remains idempotent — it will skip _register_handlers() if
        already subscribed, but still run restore + flush_worker.
        """
        if self._handlers_registered:
            return
        self._register_handlers()

    async def close(self, *, force_flush: bool = True) -> None:
        """Stop flush worker (drain + final flush) + unsubscribe.

        Order: stop worker FIRST, then unregister handlers. This ensures
        that any residual queue items are persisted before the bus
        subscription is removed.

        Called from runner lifespan shutdown / AsyncExitStack callbacks.
        Multiple close() calls safe (NOP on subsequent).
        """
        if not self._subscribed:
            return

        # Step 1: Stop flush worker (drains queue + final flush)
        try:
            await self._flush_worker.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory audit close: worker stop failed sid=%s: %s",
                self.session_id, exc,
            )

        # Step 2: Unregister bus handlers
        self._unregister_handlers()
        self._subscribed = False

        # Final force flush (in case worker stop didn't cover it)
        if force_flush and self._persistence is not None:
            try:
                await self._persistence.flush_state(
                    self.state, only_if_dirty=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "trajectory audit close: final flush failed sid=%s: %s",
                    self.session_id, exc,
                )

    # ---------------------------------------------------------------- bus api

    def handle_event(self, ev: object) -> None:
        """Generic subscriber registered for EVERY event type (sync).

        This is the DSH firehose equivalent. apply() dispatches on type.
        Purely synchronous — no I/O, no await, O(μs).
        """
        # Validate event belongs to this session (bus = per-session today,
        # but defend against shared bus misconfig silently cross-leaking).
        sid_attr = getattr(ev, "session_id", None)
        ev_name = type(ev).__name__
        if ev_name == "ConversationNodeAdded":
            logger.info(
                "trajectory handle_event: ConversationNodeAdded "
                "self.sid=%s ev.sid=%s match=%s",
                self.session_id, sid_attr, sid_attr == self.session_id,
            )
        if sid_attr is not None and sid_attr != self.session_id:
            logger.warning(
                "TrajectoryAuditService sid=%s received event for sid=%s "
                "(bus cross-leak?); dropping.",
                self.session_id, sid_attr,
            )
            return
        if ev_name in ("SessionTurnStarted", "SessionTurnFinished", "SessionIterationStarted", "SessionIterationFinished"):
            logger.info(
                "trajectory event: sid=%s type=%s", self.session_id, ev_name,
            )

        # DSH parity: ConversationNodeAdded → per-conversation-event cell.
        # The event carries the COMPLETE cell payload (detail texts, metrics,
        # time range, iteration) because the runner stashes an
        # LLMMetricsSnapshot into the trajectory runtime ctx before the
        # assistant entry is appended — a single INSERT writes the whole
        # row. There is deliberately NO metrics-UPDATE path.
        if ev_name == "ConversationNodeAdded" and isinstance(ev, evmod.ConversationNodeAdded):
            self._handle_cell_event(ev)
            return

        # Failure marking: mark the failed iteration's assistant cell
        # (partial output usually already INSERTed) or synthesize an error
        # cell so failures are visible on the timeline. NOTE: no return —
        # LLMRequestFailed must ALSO flow into apply() below for the
        # per-iteration RequestAuditRecord (finish_reason="error" etc.);
        # short-circuiting here would starve the record path.
        if ev_name == "LLMRequestFailed" and isinstance(ev, evmod.LLMRequestFailed):
            self._handle_llm_failure_cell(ev)

        # System-prompt cell: publish a kind='system' cell when the prompt
        # sha changes across turns. No return — PackerFinalDone must ALSO
        # flow into apply() for the per-iteration RequestAuditRecord.
        if ev_name == "PackerFinalDone" and isinstance(ev, evmod.PackerFinalDone):
            self._handle_system_cell(ev)

        try:
            new_state = apply(self.state, ev)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory apply() failed sid=%s ev=%s: %s",
                self.session_id, type(ev).__name__, exc,
            )
            return
        self.state = new_state

    def _handle_cell_event(self, ev: "evmod.ConversationNodeAdded") -> None:
        """DSH-aligned cell handler: build a TrajectoryCell from the event
        and schedule a non-blocking async upsert.

        Sync (bus subscriber contract). Uses asyncio.ensure_future to fire
        insert_cell without blocking the bus drain. Failures are
        swallowed (best-effort — trajectory must never block the agent).

        Field-conservation contract: every field shared between
        ConversationNodeAdded and TrajectoryCell MUST be passed here (see
        test_event_cell_field_conservation). Adding a field to the event
        without passing it through silently writes NULLs.
        """
        import uuid as _uuid
        try:
            if self._persistence is None:
                return

            schema_detail = ev.schema_detail
            if (
                schema_detail is None
                and ev.kind == "tool"
                and self._tool_registry is not None
                and ev.tool_name is not None
            ):
                schema_detail = self._resolve_tool_schema(ev.tool_name)

            cell = TrajectoryCell(
                cell_id=f"cell_{_uuid.uuid4().hex}",
                session_id=ev.session_id,
                entry_id=ev.entry_id,
                seq=ev.seq,
                turn=ev.turn,
                kind=ev.kind,
                role=ev.role,
                content_preview=ev.content_preview,
                tool_calls_summary=ev.tool_calls_summary,
                tool_call_id=ev.tool_call_id,
                tool_name=ev.tool_name,
                input_detail=ev.input_detail,
                output_detail=ev.output_detail,
                thinking_detail=ev.thinking_detail,
                is_error=ev.is_error,
                # resolved local (registry lookup when the event carries None)
                schema_detail=schema_detail,
                model=ev.model,
                finish_reason=ev.finish_reason,
                usage_input_tokens=ev.usage_input_tokens,
                usage_output_tokens=ev.usage_output_tokens,
                usage_cache_read_tokens=ev.usage_cache_read_tokens,
                usage_cache_write_tokens=ev.usage_cache_write_tokens,
                usage_reasoning_tokens=ev.usage_reasoning_tokens,
                ttft_ms=ev.ttft_ms,
                decoding_ms=ev.decoding_ms,
                started_at_ms=ev.started_at_ms,
                completed_at_ms=ev.completed_at_ms,
                iteration=ev.iteration,
                error=ev.error,
            )
            # Non-blocking schedule — never await on the bus drain path
            _ins_task = asyncio.ensure_future(self._persistence.insert_cell(cell))
            def _log_insert_err(t: "asyncio.Task[None]") -> None:
                if t.cancelled():
                    return
                exc = t.exception()
                if exc is not None:
                    logger.warning("trajectory insert_cell async failed sid=%s kind=%s: %s", self.session_id, cell.kind, exc)
            _ins_task.add_done_callback(_log_insert_err)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory _handle_cell_event failed sid=%s: %s",
                self.session_id, exc,
            )

    def _resolve_tool_schema(self, name: str) -> str | None:
        """Resolve a tool's LLM-visible schema JSON from the registry.

        Freezes the schema as seen at call time (audit value: if the tool
        definition changes later, historical cells keep showing what the
        model actually saw). Cached per tool name for the service
        lifetime.
        """
        if name in self._schema_cache:
            cached = self._schema_cache[name]
            return cached
        schema_json: str | None = None
        try:
            import json as _json
            tool = self._tool_registry.get(name)
            if tool is not None:
                schema_json = _json.dumps(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters
                            or {"type": "object", "properties": {}},
                        },
                    },
                    ensure_ascii=False,
                    default=str,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "trajectory schema resolve failed for tool=%s: %s", name, exc,
            )
            schema_json = None
        self._schema_cache[name] = schema_json
        return schema_json

    def _handle_system_cell(self, ev: "evmod.PackerFinalDone") -> None:
        """Buffer the current turn's system-prompt snapshot in memory.

        Dedup/buffering state lives HERE (trajectory service), not in
        turn_preparation — core modules must not know about trajectory
        storage concerns. The snapshot is flushed ONCE at turn end with
        the final state; the sha changes essentially every turn because
        the system prompt carries the injected authoritative time, and
        each turn's snapshot IS audit value ("what did the model actually
        see this turn").
        """
        if ev.system_prompt_full_text is None or ev.system_prompt_sha1_16 is None:
            return
        current = self._pending_system_cell
        if current is not None:
            if current["turn"] != ev.turn_counter:
                # A new turn started — flush the previous turn's snapshot
                # first so turn boundaries stay self-consistent (do NOT
                # rely on TurnFinished arrival ordering).
                self._flush_system_cell()
            elif current["sha1_16"] == ev.system_prompt_sha1_16:
                return
        self._pending_system_cell = {
            "turn": ev.turn_counter,
            "iteration": ev.iteration,
            "sha1_16": ev.system_prompt_sha1_16,
            "full_text": ev.system_prompt_full_text,
        }

    def _flush_system_cell(self) -> None:
        """Persist the buffered system cell (final state of the turn)."""
        if self._pending_system_cell is None or self._persistence is None:
            return
        try:
            payload = self._pending_system_cell
            self._pending_system_cell = None
            asyncio.ensure_future(self._persistence.insert_system_cell(
                session_id=self.session_id,
                turn=payload["turn"],
                iteration=payload["iteration"],
                sha1_16=payload["sha1_16"],
                full_text=payload["full_text"],
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory _flush_system_cell failed sid=%s: %s",
                self.session_id, exc,
            )

    def _handle_llm_failure_cell(self, ev: "evmod.LLMRequestFailed") -> None:
        """Mark-or-synthesize the error cell for a failed LLM request.

        Sync (bus subscriber contract). Schedules the async
        mark_or_synthesize_error without blocking the bus drain.
        Failures are swallowed (best-effort — trajectory must never block the agent).
        """
        try:
            if self._persistence is None:
                return
            asyncio.ensure_future(self._persistence.mark_or_synthesize_error(
                session_id=ev.session_id,
                turn=ev.turn_counter,
                iteration=ev.iteration,
                error_code=ev.error_code,
                error_message=ev.error_message,
                model=ev.model or None,
                ttft_ms=ev.ttft_ms,
                total_ms=ev.total_ms,
                failed_at_ms=ev.failed_at_ms,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory _handle_llm_failure_cell failed sid=%s: %s",
                self.session_id, exc,
            )

    async def on_turn_finished(self, ev: evmod.SessionTurnFinished) -> None:
        """Async subscriber for TurnFinished: NON-BLOCKING flush signal.

        Instead of directly calling flush_state (which would block
        the event bus drain), this pushes a dirty signal to the
        TrajectoryFlushWorker's asyncio.Queue and returns immediately.

        The worker consumer task handles actual database I/O in the
        background, with batching and adaptive flush intervals.
        """
        logger.info(
            "trajectory on_turn_finished: sid=%s records=%d is_dirty=%s "
            "queue_depth=%d",
            self.session_id,
            len(self.state.records),
            self.state.is_dirty(),
            self._flush_worker.pending_count,
        )

        # Flush the turn's system-prompt cell (final state) before the
        # record-path flush signal — cell rows then land before the turn's
        # audit record in most orderings.
        self._flush_system_cell()

        # O(1) non-blocking submit — never awaits database I/O here
        try:
            self._flush_worker.submit(self.state)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trajectory audit on_turn_finished submit failed sid=%s: %s",
                self.session_id, exc,
            )
            # Fallback: direct flush (rare, only if worker is broken)
            if self._persistence is not None:
                try:
                    await self._persistence.flush_state(
                        self.state, only_if_dirty=True,
                    )
                    logger.warning(
                        "trajectory audit fallback direct flush succeeded sid=%s",
                        self.session_id,
                    )
                except Exception as fallback_exc:  # noqa: BLE001
                    logger.warning(
                        "trajectory audit fallback flush also failed sid=%s: %s",
                        self.session_id, fallback_exc,
                    )

    # ------------------------------------------------------------------ internals

    def _register_handlers(self) -> None:
        bus = self.bus
        # Blanket-subscribe one sync handler for ALL event types. This way
        # new event types added to bus_events.ALL_EVENTS automatically get
        # routed through apply() without any additional subscribe lines.
        for event_type in evmod.ALL_EVENTS:
            bus.subscribe(event_type, self.handle_event)
        # Additional dedicated TurnFinished subscriber = flush kick-off
        bus.subscribe(evmod.SessionTurnFinished, self.on_turn_finished)
        self._handlers_registered = True

    def _unregister_handlers(self) -> None:
        bus = self.bus
        for event_type in evmod.ALL_EVENTS:
            bus.unsubscribe(event_type, self.handle_event)
        bus.unsubscribe(evmod.SessionTurnFinished, self.on_turn_finished)

    # ---------------------------------------------------------------- helpers

    def view(self, **kwargs):
        return view(self.state, **kwargs)

    @property
    def flush_worker_stats(self) -> dict[str, Any]:
        """Expose worker stats for monitoring/debugging."""
        return self._flush_worker.stats
