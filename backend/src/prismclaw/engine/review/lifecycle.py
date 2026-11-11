"""Blob lifecycle (garbage collection) for the file-review subsystem.

Both review ledgers write content-addressed blobs under
``<workspace>/.work/edits/blobs``:

  * text / code revisions  -> ``ReviewRegistry``          (``review_events.jsonl``, ``content_ref``)
  * office edits            -> ``ChangeTrackingStore``     (``log.jsonl``, ``before_ref`` / ``after_ref``)
  * docx review artifacts   -> ``ChangeTrackingStore``     (``docx_review_artifacts.jsonl``,
                                                            ``before_ref`` / ``review_docx_ref``)

A blob is safe to delete only when NO ledger still references it AND it is older
than the retention window. Referenced blobs are never touched — that keeps
rollback / re-review intact for every recorded revision. This module is pure
(no FastAPI / asyncio imports) so it is trivially unit-testable and can be
driven from an admin endpoint, a CLI, or a test without side-effect machinery.

Why an explicit GC rather than lazy delete-on-revert:
  Reverts re-materialize a *baseline* blob but never know about other revisions
  of the same file, nor about office blobs. A separate, ledger-driven sweep is
  the only place that can see the whole reference graph at once.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_BLOB_REF_PREFIX = "blobs/"


def _sha_from_ref(ref: str | None) -> str | None:
    """Extract the bare sha256 hex from a ``blobs/<sha>`` ref.

    Returns ``None`` for empty / malformed refs so a corrupt or legacy ref can
    never be mistaken for a live blob name.
    """
    if not ref or not ref.startswith(_BLOB_REF_PREFIX):
        return None
    sha = ref[len(_BLOB_REF_PREFIX):]
    return sha or None


def _iter_jsonl(path: Path):
    """Yield parsed JSON objects from an append-only log, skipping blank and
    corrupt lines. A bad line must never abort bookkeeping."""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("lifecycle.corrupt_log_line", extra={"path": str(path)})
                continue
    except OSError:
        logger.warning("lifecycle.read_log_failed", extra={"path": str(path)}, exc_info=True)


def collect_referenced_blobs(edits_dir: Path) -> set[str]:
    """Return the set of blob sha256 hex strings still referenced by any ledger.

    Reads three append-only logs from ``<edits_dir>`` and unions their blob
    references. Every blob writer in the codebase records its ref in exactly
    one of these logs, so the union is the complete liveness set.
    """
    referenced: set[str] = set()

    # 1. ReviewRegistry events (text / code revisions + baselines).
    for ev in _iter_jsonl(edits_dir / "review_events.jsonl"):
        payload = ev.get("payload") if isinstance(ev, dict) else None
        if not isinstance(payload, dict):
            continue
        for key in ("content_ref", "before_ref", "after_ref"):
            sha = _sha_from_ref(payload.get(key))
            if sha:
                referenced.add(sha)

    # 2. ChangeTrackingStore edit log (office before / after backups).
    for rec in _iter_jsonl(edits_dir / "log.jsonl"):
        if not isinstance(rec, dict):
            continue
        for key in ("before_ref", "after_ref"):
            sha = _sha_from_ref(rec.get(key))
            if sha:
                referenced.add(sha)

    # 3. ChangeTrackingStore docx review artifacts (source backup + generated docx).
    for rec in _iter_jsonl(edits_dir / "docx_review_artifacts.jsonl"):
        if not isinstance(rec, dict):
            continue
        for key in ("before_ref", "review_docx_ref"):
            sha = _sha_from_ref(rec.get(key))
            if sha:
                referenced.add(sha)

    return referenced


@dataclass
class GcReport:
    """Result of a blob GC pass over one workspace's ``<edits>`` directory."""

    workspace: str
    referenced_count: int = 0
    total_blobs: int = 0
    kept_referenced: int = 0
    kept_young: int = 0
    planned_removals: int = 0
    removed: int = 0
    freed_bytes: int = 0
    dry_run: bool = True

    def as_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "referenced_count": self.referenced_count,
            "total_blobs": self.total_blobs,
            "kept_referenced": self.kept_referenced,
            "kept_young": self.kept_young,
            "planned_removals": self.planned_removals,
            "removed": self.removed,
            "freed_bytes": self.freed_bytes,
            "dry_run": self.dry_run,
        }


def gc_blobs(
    edits_dir: Path,
    *,
    dry_run: bool = True,
    older_than_days: int = 30,
) -> GcReport:
    """Delete unreferenced blobs older than ``older_than_days``.

    Safety rules (ordered, fail-loud-free):
      * A blob referenced by any ledger is kept unconditionally.
      * An unreferenced blob newer than the cutoff is kept (still inside the
        retention window — a later edit may reference it).
      * Only old, unreferenced blobs are removed. With ``dry_run=True`` nothing
        is deleted; the report lists what *would* be removed.

    Transient write artifacts (``<sha>.tmp.<8hex>`` from ``FileSystemContentStore``)
    are never referenced by a ledger, so they age out under the same retention
    rule once their write has settled (the cutoff protects an in-flight write).
    """
    report = GcReport(workspace=str(edits_dir), dry_run=dry_run)
    blobs_dir = edits_dir / "blobs"
    if not blobs_dir.exists():
        return report

    referenced = collect_referenced_blobs(edits_dir)
    report.referenced_count = len(referenced)

    cutoff = time.time() - max(0, int(older_than_days)) * 86400.0

    for blob_path in blobs_dir.iterdir():
        if not blob_path.is_file():
            continue
        report.total_blobs += 1
        sha = blob_path.name

        if sha in referenced:
            report.kept_referenced += 1
            continue
        try:
            mtime = blob_path.stat().st_mtime
        except OSError:
            # Cannot stat — treat as young and leave it alone rather than risk
            # deleting something we cannot reason about.
            report.kept_young += 1
            continue
        if mtime > cutoff:
            report.kept_young += 1
            continue

        if dry_run:
            report.planned_removals += 1
            continue
        try:
            report.freed_bytes += blob_path.stat().st_size
            blob_path.unlink()
            report.removed += 1
        except OSError:
            logger.warning("lifecycle.delete_failed", extra={"path": str(blob_path)}, exc_info=True)

    return report
