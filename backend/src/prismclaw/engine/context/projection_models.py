from __future__ import annotations

from dataclasses import dataclass

from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.content.context import ContextPackLayout
from prismclaw.schema.agent.session import AgentMessageDict, ProjectionInputMessage


@dataclass(slots=True)
class ProjectionMessageSet:
    messages: list[AgentMessageDict]
    meta_messages: list[ProjectionInputMessage]
    appended_messages: list[ProjectionInputMessage]

    def replace_messages(
        self, new_messages: list[AgentMessageDict]
    ) -> "ProjectionMessageSet":
        """Return a new instance with ``messages`` swapped in.

        WP-1.1 (Phase 1 immutability): the hot path used to mutate
        ``message_set.messages = new`` in place, which silently
        rewrote the caller's view and could leak changes to a shared
        list. ``replace_messages`` makes the swap explicit, returns a
        fresh dataclass, and decouples the caller's reference from
        the new list (so subsequent mutations on the new list don't
        affect the original ``self.messages``).

        The new instance shares the ``meta_messages`` and
        ``appended_messages`` references — those are treated as
        read-only by the call sites we audited (no in-place mutation
        in ``pre_request_pipeline`` or ``runner``).
        """
        return ProjectionMessageSet(
            messages=list(new_messages),
            meta_messages=self.meta_messages,
            appended_messages=self.appended_messages,
        )


@dataclass(slots=True)
class PromptProjection:
    system_blocks: list[ContextBlock]
    dynamic_projection: list[ContextBlock]
    meta_messages: list[AgentMessageDict]
    messages: list[AgentMessageDict]
    appended_messages: list[AgentMessageDict]
    history_projection: list[ContextBlock]
    tool_projection: list[ContextBlock]

    @property
    def projected_messages(self) -> list[AgentMessageDict]:
        if not self.meta_messages and not self.appended_messages:
            return self.messages
        return [*self.meta_messages, *self.messages, *self.appended_messages]

    def to_context_layout(self) -> ContextPackLayout:
        return ContextPackLayout(
            static_blocks=list(self.system_blocks),
            dynamic_blocks=list(self.dynamic_projection),
            history_blocks=list(self.history_projection),
            result_blocks=list(self.tool_projection),
        )
