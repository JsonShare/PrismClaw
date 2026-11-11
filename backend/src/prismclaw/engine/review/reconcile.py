"""reconcile — compare the live file against what the ReviewSet expects.

State machine:
  Match    — live file == ReviewSet.head  → safe to materialize a baseline.
  Drifted  — live file changed out-of-band → refuse blind write (warn + force).
  Missing  — live file gone               → treat per new-file / deletion semantics.
"""

from __future__ import annotations

from enum import Enum


class ReconcileState(str, Enum):
    MATCH = "match"
    DRIFTED = "drifted"
    MISSING = "missing"


def reconcile(current_sha: str | None, expected_sha: str | None) -> ReconcileState:
    """Classify the relationship between the live file and the expected head.

    ``current_sha`` is the SHA-256 of the live file (or None if the file does
    not exist). ``expected_sha`` is the head SHA recorded in the ReviewSet.
    """
    if current_sha is None:
        return ReconcileState.MISSING
    if expected_sha is None:
        # No expectation recorded (e.g. a new file never confirmed) — treat the
        # live content as authoritative, not drift.
        return ReconcileState.MATCH
    return ReconcileState.MATCH if current_sha == expected_sha else ReconcileState.DRIFTED
