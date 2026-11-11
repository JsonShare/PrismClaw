from __future__ import annotations

from prismclaw.engine.execution.checkpoint.persistence import (
    RUN_SNAPSHOT_CUSTOM_TYPE,
    RunSnapshot,
    load_run_snapshot_from_branch,
)
from prismclaw.schema.persistence.persistence import RunSnapshotStore
from prismclaw.schema.session.types import CustomEntry, SessionTree, generate_entry_id, now_iso
from prismclaw.schema.storage.storage_types import SessionStore
from prismclaw.schema.session.event_logger import SessionEventLogger

_event_logger = SessionEventLogger()


def load_run_snapshot_for_tree(tree: SessionTree) -> RunSnapshot:
    return load_run_snapshot_from_branch(
        session_id=tree.header.id,
        branch=tree.get_branch(),
    )


async def persist_run_snapshot(
    store: SessionStore,
    tree: SessionTree,
    snapshot: RunSnapshot,
    snapshot_store: RunSnapshotStore | None = None,
) -> CustomEntry:
    snapshot.snapshot_cursor = tree.leaf_id or ""
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=RUN_SNAPSHOT_CUSTOM_TYPE,
        data=snapshot.model_dump(mode="json"),
    )
    snapshot.snapshot_cursor = entry.id
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await store.commit_events([event])
    if snapshot_store is not None:
        await snapshot_store.save(snapshot)
    return entry
