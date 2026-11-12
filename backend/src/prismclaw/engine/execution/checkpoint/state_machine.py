from __future__ import annotations

import logging

from prismclaw.engine.execution.checkpoint.models import RunState
from prismclaw.engine.state.tool_loop_state import ToolLoopState

logger = logging.getLogger(__name__)


class RunStateMachine:
    def __init__(self, state: RunState):
        self.state = state

    def retry_counts_view(self) -> dict[str, int]:
        return self.state.retry_state.model_dump(mode="python")

    def record_retry(self, classification: str) -> dict[str, int]:
        if classification == "reasoning":
            self.state.retry_state.reasoning += 1
        elif classification == "empty":
            self.state.retry_state.empty += 1
        else:
            logger.warning("unknown no-tool retry classification: %s", classification)
        return self.retry_counts_view()

    def reset_retry_state(self) -> dict[str, int]:
        self.state.retry_state.reasoning = 0
        self.state.retry_state.empty = 0
        return self.retry_counts_view()

    def load_tool_loop_state(self) -> ToolLoopState:
        return ToolLoopState.from_dict(self.state.tool_loop_state)

    def update_tool_loop_state(self, tool_state: ToolLoopState) -> ToolLoopState:
        self.state.tool_loop_state = tool_state.to_dict()
        return tool_state
