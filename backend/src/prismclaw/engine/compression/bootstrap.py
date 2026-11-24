from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from prismclaw.schema.session.ids import derive_memory_session_key
from prismclaw.schema.storage.storage_types import WorkspaceStore

logger = logging.getLogger(__name__)

BOOTSTRAP_FILE_WARN_BYTES = 10_240


def _read_file(path: str) -> str | None:
    """读文件内容；不存在或不可读返回 None。同步实现，由调用方放入线程池。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


async def load_bootstrap_context(
    workspace_id: str,
    workspace_store: WorkspaceStore,
    filenames: list[str],
) -> str:
    contents: list[str] = []

    for file_path in filenames:
        try:
            # 工作区相对路径走 workspace store（可跨存储后端）；
            # 绝对路径与 ~ 展开后的路径直接读盘。
            if file_path.startswith("~"):
                expanded = await asyncio.to_thread(os.path.expanduser, file_path)
            else:
                expanded = file_path
            if not expanded.startswith("/"):
                raw = await workspace_store.get_file(workspace_id, expanded)
                if raw and raw.strip():
                    contents.append(f"--- 来自 {file_path} ---\n{raw}")
                continue
            text = await asyncio.to_thread(_read_file, expanded)
            if text is not None:
                contents.append(f"--- 来自 {file_path} ---\n{text}")
        except Exception as e:
            logger.debug("Failed to load bootstrap file %s: %s", file_path, e)
            continue

    if not contents:
        return ""

    return "\n\n".join(
        [
            "以下为工作区与用户指令。请务必遵守。这些指令优先于默认行为，冲突时以指令为准。",
            *contents,
        ]
    )


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
        self._bootstrap_files = (
            bootstrap_files
            if bootstrap_files is not None
            else [
                "/etc/prismclaw/AGENTS.md",
                "~/.prismclaw/assets/AGENTS.md",
                ".work/AGENTS.md",
                "AGENTS.md",
            ]
        )
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
