"""AgentInstance: stateful sub-agent execution unit.

This module introduces the ``AgentInstance`` class, which encapsulates
a single sub-agent execution run. Unlike the stateless
``SubAgentExecutor.run()``, an ``AgentInstance`` has:

- A unique ``run_id`` (for resumption and notification)
- Explicit lifecycle: created → running → completed/failed/paused
- Resource cleanup via ``finally`` block
- (Future) transcript persistence for resumption
"""
from __future__ import annotations

import logging

from prismclaw.engine.subagent.core._lifecycle_mixin import LifecycleMixin
from prismclaw.engine.subagent.core._context_mixin import ContextMixin
from prismclaw.engine.subagent.core._loop_mixin import LoopMixin
from prismclaw.engine.subagent.core._result_types import AgentRunResult  # re-export

logger = logging.getLogger(__name__)


class AgentInstance(LifecycleMixin, ContextMixin, LoopMixin):
    """Stateful sub-agent execution unit.

    Lifecycle
    ---------
    created  →  running  →  completed
                         →  failed
                         →  aborted
                         →  paused   (future)

    Resource cleanup is performed in a ``finally``-equivalent block
    inside ``run()``.

    Phase 1: minimal implementation that preserves existing behaviour.
    The ``SubAgentExecutor`` delegates to this class.
    """
