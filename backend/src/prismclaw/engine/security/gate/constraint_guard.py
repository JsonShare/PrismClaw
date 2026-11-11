"""R4 — Constraint 硬护栏 detection seam.

Constraints (``TaskSpec.constraints``) are **hard acceptance anchors**: the
completion intake (``control.completion``) records them as acceptance
criteria, so a completion claim is only accepted when the observable gap is empty.

This module is the **detection seam** the R2 telemetry point consumes. It reads
the review verdict (a real, existing signal — no NLP, no guessing at free-text
semantics) and reports constraints that were declared but not evidenced at a
non-completion review.

Design discipline:
- Do **not** invent a blind static NLP checker over free-text constraints. That
  would produce false positives and would be guessing at semantics we do not own.
- The detector consumes an **existing signal**: the review-gate verdict's
  ``acceptance_evaluation`` entries whose ``criterion`` matches a declared
  constraint and whose ``status != "met"``. This is the honest, conservative
  proxy — "constraint declared but not evidenced" — and it never fires for a
  task that cleanly completed (``verdict == "complete"``), so answer-type tasks
  that complete on heuristic evidence are never false-positive'd.
- Breach thresholds / recovery depth remain **data-derived** from R2 collection;
  this seam only reports *which* constraints were unmet, not *how many* are
  allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConstraintBreach:
    """A single observed constraint violation for the current turn."""

    code: str
    detail: str


def _safe_constraint_texts(task_spec: Any | None) -> set[str]:
    raw = getattr(task_spec, "constraints", None)
    if not raw:
        return set()
    return {item.strip() for item in raw if isinstance(item, str) and item.strip()}


def detect_constraint_breaches(
    *,
    task_spec: Any | None = None,
    review_decision: Any | None = None,
) -> list[ConstraintBreach]:
    """Return constraint breaches observed for the current turn.

    Consumes the **review verdict** (an existing signal, no NLP): a constraint is
    reported as a breach when it was declared (``task_spec.constraints``) but
    its acceptance criterion is not ``met`` at a review that did **not**
    complete the task.

    Args:
        task_spec: The live ``TaskSpec`` (carries ``constraints``). Typed as
            ``Any`` to avoid import coupling with the model layer.
        review_decision: The ``ReviewDecisionRecord`` produced by
            ``control.completion``. When ``None`` (no review ran) or
            when the task cleanly completed (``verdict == "complete"``), no
            breach is reported — conservative, zero false positives.

    Returns:
        ``ConstraintBreach`` entries for each declared constraint whose
        acceptance criterion was left unmet at a non-completion review.
        Empty list when there is nothing to report.
    """
    acceptance = getattr(review_decision, "acceptance_evaluation", None)
    if not acceptance:
        return []
    # A clean completion means the constraints were satisfied (answer-type
    # tasks complete on heuristic evidence by design). Never false-positive a
    # completed task.
    if getattr(review_decision, "verdict", None) == "complete":
        return []
    constraint_texts = _safe_constraint_texts(task_spec)
    if not constraint_texts:
        return []
    breaches: list[ConstraintBreach] = []
    for ev in acceptance:
        ev_criterion = getattr(ev, "criterion", None)
        ev_status = getattr(ev, "status", None)
        if ev_criterion in constraint_texts and ev_status != "met":
            breaches.append(ConstraintBreach(code="constraint_unmet", detail=str(ev_criterion)))
    return breaches
