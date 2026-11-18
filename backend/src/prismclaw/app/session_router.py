from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.session.types import now_iso
from prismclaw.schema.storage.storage_types import SessionStore


@dataclass
class SessionRouter:
    store: SessionStore
    on_session_rotated: Callable[[str], Awaitable[None]] | None = field(default=None)

    async def resolve_or_create(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str = "default",
    ) -> tuple[str, SessionTree]:
        session_id = await self.store.get_current_session_id(session_key)
        if session_id is not None:
            tree = await self.store.load(session_id)
            if tree is not None:
                return session_id, tree

        old_tree = await self.store.load(session_key)
        if old_tree is not None:
            await self.store.set_current_session_id(session_key, session_key)
            return session_key, old_tree

        tree = await self.store.create_new_session(session_key, workspace_id, agent_id)
        return tree.header.id, tree

    async def rotate(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str = "default",
        title: str | None = None,
    ) -> tuple[str, SessionTree]:
        old_id = await self.store.get_current_session_id(session_key)
        tree = await self.store.create_new_session(
            session_key, workspace_id, agent_id, parent_session_id=old_id, title=title
        )
        if old_id is not None and self.on_session_rotated:
            await self.on_session_rotated(old_id)
        return tree.header.id, tree

    async def update_last_interaction(self, session_id: str) -> None:
        tree = await self.store.load(session_id)
        if tree is None:
            return
        updated_header = tree.header.model_copy(update={"last_interaction_at": now_iso()})
        updated_tree = tree.model_copy(update={"header": updated_header})
        await self.store.save_header(updated_tree)
