from __future__ import annotations

import logging
import os

from prismclaw.schema.storage.storage_types import WorkspaceStore
from prismclaw.schema.session.ids import derive_memory_session_key

logger = logging.getLogger(__name__)

BOOTSTRAP_FILE_WARN_BYTES = 10_240


async def load_bootstrap_context(
    workspace_id: str,
    workspace_store: WorkspaceStore,
    filenames: list[str],
) -> str:
    contents = []

    # Prepend global system instructions explicitly mimicking the reference implementation
    contents.append(
        "以下展示代码库与用户指令。请务必遵守这些指令。"
        "重要：这些指令会覆盖任何默认行为，你必须严格按规定执行。"
    )

    for file_path in filenames:
        expanded_path = os.path.expanduser(file_path)
        try:
            # We try reading through workspace store for project-local files
            if not expanded_path.startswith("/"):
                raw = await workspace_store.get_file(workspace_id, expanded_path)
                if raw and raw.strip():
                    contents.append(f"--- 来自 {file_path} ---\n{raw}")
            else:
                # Global files like /etc or ~/.prismclaw
                if os.path.exists(expanded_path):
                    with open(expanded_path, "r", encoding="utf-8") as f:
                        contents.append(f"--- 来自 {file_path} ---\n{f.read()}")
        except Exception as e:
            logger.debug("Failed to load bootstrap file %s: %s", file_path, e)
            continue

    return "\n\n".join(contents)


def derive_workspace_id(session_id: str) -> str:
    """User/workspace-scoped id (``:s:`` suffix and web conversation id
    stripped via the single derivation in session_keys — same W10 rule
    as memory keys, so a user's conversations share one workspace)."""
    return derive_memory_session_key(session_id).replace(":", "_")


class BootstrapProvider:
    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore | None = None,
        bootstrap_files: list[str] | None = None,
    ) -> None:
        self._workspace_store = workspace_store
        self._bootstrap_files = bootstrap_files if bootstrap_files is not None else [
            # PrismClaw native paths
            "/etc/prismclaw/AGENTS.md",
            "~/.prismclaw/assets/AGENTS.md",
            ".work/AGENTS.md",
            "AGENTS.md",
            "/etc/prismclaw/prismclaw.md",
            "~/.prismclaw/assets/prismclaw.md",
            ".work/prismclaw.md",
            "prismclaw.md",
            "prismclaw.local.md",
        ]
        self._cache: dict[str, str] = {}

    async def get_bootstrap(self, session_id: str) -> str | None:
        if self._workspace_store is None:
            return None
        workspace_id = derive_workspace_id(session_id)
        if workspace_id in self._cache:
            cached = self._cache[workspace_id]
            return cached if cached else None
        bootstrap_str = await load_bootstrap_context(
            workspace_id,
            self._workspace_store,
            self._bootstrap_files,
        )
        self._cache[workspace_id] = bootstrap_str
        return bootstrap_str if bootstrap_str else None
