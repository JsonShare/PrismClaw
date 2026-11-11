"""materializer — the single, atomic write path for file content.

Every physical change to a reviewed file goes through here: write to a temp
file, ``fsync``, then atomic ``os.replace``. No other code path writes reviewed
files. Combined with the registry appending the decision event, this is the
closest we get to a transaction: the file on disk and the recorded decision can
only disagree if the event append itself fails — and because a baseline revert
is idempotent, a re-click recovers cleanly.
"""

from __future__ import annotations

import logging
import os
import tempfile

from pathlib import Path

logger = logging.getLogger(__name__)


def materialize_file(workspace_path: Path, path: str, content: bytes | None) -> None:
    """Write ``content`` to ``path`` inside ``workspace_path`` atomically.

    ``content is None`` means the file should be removed (rejecting a file that
    did not exist before the agent touched it).
    """
    full = workspace_path / path

    if content is None:
        if full.exists():
            full.unlink()
        return

    full.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp in the same directory guarantees the rename is on the same FS.
    fd, tmp_name = tempfile.mkstemp(dir=str(full.parent), prefix=f".{full.name}.mat.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, full)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        logger.error("materializer.write_failed", exc_info=True)
        raise
