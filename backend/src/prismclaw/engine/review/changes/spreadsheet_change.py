"""xlsx / pptx review strategy.

Reject All reverses only the rejected changes on the live workspace file
(never a frozen backup blob), so edits made in later rounds are preserved.
Drift (a later edit touched a rejected location) fails loud via
``OfficeRevertConflict`` — inherited from the shared engine.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from prismclaw.engine.review.changes.intent import ReviewIntent
from prismclaw.engine.review.changes.office_engines import revert_office_ops
from prismclaw.engine.toolkit.file.tracking import FileEditRecord, get_tracking_store


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SpreadsheetChange:
    kind: str  # set in __init__

    def __init__(self, fmt: str) -> None:
        self.kind = fmt  # 'xlsx' or 'pptx'

    def reject(self, intent: ReviewIntent) -> dict[str, Any]:
        full = intent.workspace_path / intent.path
        current = (
            intent.current_bytes
            if intent.current_bytes is not None
            else full.read_bytes()
        )
        # OfficeRevertConflict propagates (drift detected) — caller maps to 409.
        new = revert_office_ops(current, self.kind, intent.rejected)

        store = get_tracking_store(intent.workspace_path)
        if new == current:
            # Nothing rejected (or a no-op) — file unchanged. The old routes
            # path referenced an undefined `after_ref` here; we persist the
            # current file so the frontend still gets a valid before_ref.
            after_ref = store.save_blob_bytes(current)
            return {
                "status": "ok",
                "path": intent.path,
                "file_change": {
                    "path": intent.path,
                    "name": Path(intent.path).name,
                    "kind": self.kind,
                    "insertions": 0,
                    "deletions": 0,
                    "hunks": [],
                    "before_ref": after_ref,
                    "before_sha": _sha(current),
                    "officeDetail": [],
                },
                "office_result": {
                    "status": "ok",
                    "applied": 0,
                    "rejected": len(intent.rejected),
                },
            }

        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(new)

        before_ref = store.save_blob_bytes(current)
        after_ref = store.save_blob_bytes(new)
        before_sha = _sha(current)
        after_sha = _sha(new)
        rec = FileEditRecord(
            record_id=uuid.uuid4().hex[:12],
            session_id=intent.conversation_id or "",
            tool_call_id="office:apply",
            path=intent.path,
            before_sha=before_sha,
            after_sha=after_sha,
            before_ref=before_ref,
            after_ref=after_ref,
            ts=time.time(),
            binary=True,
        )
        store.record(rec)

        file_change = {
            "path": intent.path,
            "name": Path(intent.path).name,
            "kind": self.kind,
            "insertions": 0,
            "deletions": len(intent.rejected),
            "hunks": [],
            "before_ref": after_ref,
            "before_sha": after_sha,
            "officeDetail": [],
        }
        return {
            "status": "ok",
            "path": intent.path,
            "file_change": file_change,
            "office_result": {
                "status": "ok",
                "applied": 0,
                "rejected": len(intent.rejected),
            },
        }

    def accept(self, intent: ReviewIntent) -> dict[str, Any]:
        # The xlsx/pptx file is already in its applied state after the edit;
        # accepting the review keeps it as-is.
        return {"status": "ok", "path": intent.path}
