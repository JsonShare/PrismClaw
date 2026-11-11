"""DOCX native review artifact builder backed by the official docx-editor bridge.

This module intentionally keeps the integration surface narrow:

* `.docx` edits are suggestion-only; no direct-write compatibility path remains.
* Inputs must already use the official docx-editor paragraph handles (`paraId`)
  returned by `read_document`.
* The backend delegates tracked-change semantics to the published agent toolkit
  instead of re-implementing them locally.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.schema.agent.review_errors import (
    DocxReviewEngineGap,
    UnsupportedDocxChange,
)
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import sha256hex


@dataclass(frozen=True)
class SuggestedDocxChange:
    op: str
    ref: str
    search: str
    replace_with: str


@dataclass(frozen=True)
class ReadDocxAgentTextResult:
    text: str


@dataclass(frozen=True)
class BuildDocxReviewArtifactResult:
    review_docx_bytes: bytes
    review_docx_sha: str
    pending_changes: int
    insertions: int
    deletions: int
    note: str


_SUPPORTED_DOCX_NATIVE_OPS = frozenset({"suggest_change"})


_sha256hex = sha256hex


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _bridge_path() -> Path:
    return _repo_root() / "frontend" / "scripts" / "docx-review-bridge.mjs"


def _bridge_cwd() -> Path:
    return _repo_root() / "frontend"


def _parse_read_document_lines(text: str) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("["):
            continue
        try:
            para_id, paragraph_text = line[1:].split("]", 1)
        except ValueError:
            continue
        lines.append((para_id.strip(), paragraph_text.lstrip()))
    return lines


def _build_docx_para_id_map(base_bytes: bytes) -> dict[str, str]:
    snapshot_text = read_docx_agent_text(base_bytes).text
    return {para_id: paragraph_text for para_id, paragraph_text in _parse_read_document_lines(snapshot_text)}


def normalize_docx_suggested_changes(
    base_bytes: bytes,
    operations: list[dict[str, Any]],
) -> list[SuggestedDocxChange]:
    """Validate `.docx` suggest_change operations against current reviewer handles."""
    if not isinstance(operations, list) or not operations:
        raise UnsupportedDocxChange("operations must be a non-empty list")

    para_ids = _build_docx_para_id_map(base_bytes)
    normalized: list[SuggestedDocxChange] = []
    seen_refs: set[str] = set()

    for operation in operations:
        if not isinstance(operation, dict):
            raise UnsupportedDocxChange("every operation must be an object")

        op = str(operation.get("op") or "")
        ref = str(operation.get("ref") or "")
        if op not in _SUPPORTED_DOCX_NATIVE_OPS:
            raise UnsupportedDocxChange(
                f"docx native review does not support op '{op}'. Supported op: suggest_change."
            )
        if not ref:
            raise UnsupportedDocxChange("docx operation ref is required")
        if ref in seen_refs:
            raise UnsupportedDocxChange(f"duplicate docx ref in one batch is not allowed: {ref}")
        seen_refs.add(ref)
        if ref not in para_ids:
            raise UnsupportedDocxChange(f"docx paraId not found: {ref}")
        search = operation.get("search")
        replace_with = operation.get("replaceWith")
        if not isinstance(search, str):
            raise UnsupportedDocxChange(f"docx suggest_change requires string search for {ref}")
        if not isinstance(replace_with, str):
            raise UnsupportedDocxChange(f"docx suggest_change requires string replaceWith for {ref}")
        normalized.append(
            SuggestedDocxChange(
                op=op,
                ref=ref,
                search=search,
                replace_with=replace_with,
            )
        )

    return normalized


def _parse_bridge_stdout(stdout: str, stderr: str) -> dict[str, Any]:
    raw = (stdout or "").strip()
    if not raw:
        raise DocxReviewEngineGap(f"docx review bridge produced no stdout. stderr: {stderr.strip()}")

    candidate = raw.splitlines()[-1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise DocxReviewEngineGap(
            f"docx review bridge returned invalid JSON: {candidate}"
        ) from exc

    if not isinstance(payload, dict):
        raise DocxReviewEngineGap("docx review bridge returned a non-object payload")
    return payload


def read_docx_agent_text(
    base_bytes: bytes,
    *,
    author: str = "PrismClaw Agent",
) -> ReadDocxAgentTextResult:
    """Read a DOCX through the official reviewer bridge and return agent text."""
    node = shutil.which("node")
    if not node:
        raise DocxReviewEngineGap("node executable not found")

    bridge_path = _bridge_path()
    bridge_cwd = _bridge_cwd()
    if not bridge_path.exists():
        raise DocxReviewEngineGap(f"docx review bridge not found: {bridge_path}")

    with tempfile.TemporaryDirectory(prefix="prismclaw-docx-read-") as tmpdir:
        tmp_root = Path(tmpdir)
        input_path = tmp_root / "input.docx"
        input_path.write_bytes(base_bytes)

        proc = subprocess.run(
            [
                node,
                str(bridge_path),
                "read",
                "--input",
                str(input_path),
                "--author",
                author,
            ],
            cwd=str(bridge_cwd),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        bridge_payload = _parse_bridge_stdout(proc.stdout, proc.stderr)
        if proc.returncode != 0 or not bridge_payload.get("ok"):
            error_text = str(bridge_payload.get("error") or proc.stderr.strip() or "unknown bridge error")
            raise DocxReviewEngineGap(error_text)

    return ReadDocxAgentTextResult(text=str(bridge_payload.get("text") or ""))


def build_docx_review_artifact(
    base_bytes: bytes,
    operations: list[dict[str, Any]],
    *,
    author: str = "PrismClaw Agent",
) -> BuildDocxReviewArtifactResult:
    """Build a tracked-changes `.docx` artifact from official suggest_change ops."""
    normalized_changes = normalize_docx_suggested_changes(base_bytes, operations)

    node = shutil.which("node")
    if not node:
        raise DocxReviewEngineGap("node executable not found")

    bridge_path = _bridge_path()
    bridge_cwd = _bridge_cwd()
    if not bridge_path.exists():
        raise DocxReviewEngineGap(f"docx review bridge not found: {bridge_path}")

    payload = [
        {
            "op": change.op,
            "ref": change.ref,
            "search": change.search,
            "replaceWith": change.replace_with,
        }
        for change in normalized_changes
    ]

    with tempfile.TemporaryDirectory(prefix="prismclaw-docx-review-") as tmpdir:
        tmp_root = Path(tmpdir)
        input_path = tmp_root / "input.docx"
        operations_path = tmp_root / "operations.json"
        output_path = tmp_root / "review.docx"

        input_path.write_bytes(base_bytes)
        operations_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                node,
                str(bridge_path),
                "build",
                "--input",
                str(input_path),
                "--operations",
                str(operations_path),
                "--output",
                str(output_path),
                "--author",
                author,
            ],
            cwd=str(bridge_cwd),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        bridge_payload = _parse_bridge_stdout(proc.stdout, proc.stderr)
        if proc.returncode != 0 or not bridge_payload.get("ok"):
            error_text = str(bridge_payload.get("error") or proc.stderr.strip() or "unknown bridge error")
            raise DocxReviewEngineGap(error_text)
        if not output_path.exists():
            raise DocxReviewEngineGap("docx review bridge did not produce an output file")

        review_docx_bytes = output_path.read_bytes()

    pending_changes = int(bridge_payload.get("pending_changes") or 0)
    if pending_changes <= 0:
        raise UnsupportedDocxChange("docx review artifact contains no pending tracked changes")

    return BuildDocxReviewArtifactResult(
        review_docx_bytes=review_docx_bytes,
        review_docx_sha=_sha256hex(review_docx_bytes),
        pending_changes=pending_changes,
        insertions=int(bridge_payload.get("insertions") or 0),
        deletions=int(bridge_payload.get("deletions") or 0),
        note=str(bridge_payload.get("note") or ""),
    )
