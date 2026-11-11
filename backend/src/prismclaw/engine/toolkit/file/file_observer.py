"""File version observation service.

Tracks file read epochs (SHA-256 content hashes) so that subsequent
edit operations can verify the file hasn't been modified since the last
read. FileObserver is an independent service, NOT stuffed into ToolContext
(which is a value object and should not hold behavior).

The read_file_state dict in ToolContext remains the raw data store
(dict[str, FileVersion]). FileObserver wraps it with behavioral methods
like check_version() and clear().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VersionCheck(Enum):
    OK = "ok"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass
class FileVersion:
    read_epoch: str   # SHA-256 content hash
    line_count: int


@dataclass
class FileObserver:
    """Independent service for tracking file read epochs.

    Created and managed by agent_instance.py, NOT by ToolContext.
    ToolContext.read_file_state remains the raw dict; FileObserver
    provides the behavioral interface.
    """
    _observations: dict[str, FileVersion] = field(default_factory=dict)

    def record_read(self, file_path: str, read_epoch: str, line_count: int) -> None:
        """Record a read observation for the given file."""
        self._observations[file_path] = FileVersion(
            read_epoch=read_epoch,
            line_count=line_count,
        )

    def check_version(self, file_path: str, claimed_epoch: str) -> VersionCheck:
        """Check if a claimed epoch matches the observed one."""
        observed = self._observations.get(file_path)
        if observed is None:
            return VersionCheck.UNKNOWN
        if observed.read_epoch == claimed_epoch:
            return VersionCheck.OK
        return VersionCheck.STALE

    def get_observation(self, file_path: str) -> FileVersion | None:
        """Get the current observation for a file, if any."""
        return self._observations.get(file_path)

    def remove(self, file_path: str) -> None:
        """Remove observation for a specific file."""
        self._observations.pop(file_path, None)

    def clear(self) -> None:
        """Clear all observations. Called at session end by agent_instance.py cleanup."""
        self._observations.clear()

    def sync_to_dict(self) -> dict[str, dict[str, Any]]:
        """Export observations to a plain dict for ToolContext.read_file_state compatibility."""
        return {
            path: {"read_epoch": fv.read_epoch, "line_count": fv.line_count}
            for path, fv in self._observations.items()
        }

    def sync_from_dict(self, data: dict[str, Any]) -> None:
        """Import observations from a plain dict (e.g. ToolContext.read_file_state)."""
        for path, value in data.items():
            if isinstance(value, dict) and "read_epoch" in value:
                self._observations[path] = FileVersion(
                    read_epoch=value["read_epoch"],
                    line_count=value.get("line_count", 0),
                )
