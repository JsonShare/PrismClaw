"""Run control types — out-of-band control surface for in-flight agent runs.

Zero prismclaw-internal dependencies — only stdlib. Hoisted from
``engine.agent.run.control.run_control`` so that delivery and app layers
can reference RunControl / SteerMessage without depending on engine internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SteerMessage:
    kind: Literal["steer", "sidebar"]
    text: str


@dataclass
class RunControl:
    """Out-of-band control surface for an in-flight agent run.

    Fields
    ------
    abort_event:
        Signals the runner to terminate ASAP. Channels and ProtocolOp
        handlers SHALL call :meth:`stop` to set it. The runner itself uses
        ``abort_event.is_set()`` (via ``runtime_util.is_abort_set``) at every
        await boundary.
    active:
        Lifecycle flag maintained EXCLUSIVELY by the channel adapter via
        try/finally around ``run_agent_stream(...)``. Hooks SHALL NOT mutate
        this field — see ``on_run_start`` / ``on_run_end`` design note.
    pending_steers:
        Buffer of ``SteerMessage`` entries waiting to be injected into the
        next iteration's system prompt. Drained by ``SteerHook`` via a
        swap-idiom on every call to ``before_prompt_build``. Cap enforcement
        (5 messages / 2000 chars) is performed by the ``/steer`` and
        ``/btw`` command handlers, not by this dataclass.
    """

    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = False
    chat_done_handled_externally: bool = False
    pending_steers: list[SteerMessage] = field(default_factory=list)

    def stop(self) -> None:
        self.abort_event.set()
        self.pending_steers.clear()

    def is_active(self) -> bool:
        return self.active and not self.abort_event.is_set()
