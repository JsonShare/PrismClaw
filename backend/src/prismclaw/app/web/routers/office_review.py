"""Pure-Python office review synthesis + subset apply.

Replaces the deleted ``.prismclaw/plugins/office`` MCP-server shell-outs. Produces
TipTap-compatible JSON documents (insertion/deletion marks, ``changeId`` === detail
index) for the frontend tiptap-track-changes review editor, and re-materializes
office files from the original backup blob given an accepted-operations subset.

Supports ``.xlsx`` (openpyxl) and ``.pptx`` (python-pptx).

The op-application engine lives in
``prismclaw.engine.review.changes.apply_office_ops`` and is shared with the
edit tool, so the review/apply path and the edit path can never drift apart.

The TipTap JSON shape consumed by the frontend
(``frontend/src/components/officeReview/tiptap/``) is::

    {"type": "doc", "content": [ {"type": "paragraph", "content": [
        {"type": "text", "text": "new", "marks": [
            {"type": "insertion", "attrs": {"changeId": 0}}]},
        {"type": "text", "text": "old", "marks": [
            {"type": "deletion", "attrs": {"changeId": 0}}]}
    ]} ]}
"""
from __future__ import annotations

import hashlib
import io
from typing import Any


_apply_office_ops_ref: Any = None

def _set_apply_office_ops(fn: Any) -> None:
    global _apply_office_ops_ref
    _apply_office_ops_ref = fn


# ── TipTap JSON node builders ────────────────────────────────────────────────
def _text(text: str, marks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def _insertion(text: str, change_id: int) -> dict[str, Any]:
    return _text(text, [{"type": "insertion", "attrs": {"changeId": change_id}}])


def _deletion(text: str, change_id: int) -> dict[str, Any]:
    return _text(text, [{"type": "deletion", "attrs": {"changeId": change_id}}])


def _paragraph(
    content: list[dict[str, Any]] | None = None,
    attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "paragraph"}
    if attrs:
        node["attrs"] = attrs
    if content:
        node["content"] = content
    return node


def _doc(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(blocks)}


def _empty_doc() -> dict[str, Any]:
    return _doc(_paragraph())

# ── .xlsx ─────────────────────────────────────────────────────────────────────
def _synthesize_xlsx(before_bytes: bytes, details: list[dict] | None) -> dict[str, Any]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(before_bytes))
    detail_by_ref: dict[str, list[tuple[int, dict]]] = {}
    for i, d in enumerate(details or []):
        if isinstance(d, dict) and d.get("ref"):
            detail_by_ref.setdefault(d["ref"], []).append((i, d))

    blocks: list[dict] = []
    for si, ws in enumerate(wb.worksheets, start=1):
        blocks.append(
            _paragraph([_text(f"Sheet: {ws.title}")], attrs={"data-sheet": ws.title})
        )
        max_row = ws.max_row or 0
        max_col = ws.max_column or 0
        for r in range(1, max_row + 1):
            parts: list[dict] = []
            for c in range(1, max_col + 1):
                ref = f"s#{si}!r#{r}c#{c}"
                val = ws.cell(row=r, column=c).value
                text = "" if val is None else str(val)
                matched = detail_by_ref.get(ref, [])
                for idx, d in matched:
                    before = d.get("before") or ""
                    after = d.get("after") or ""
                    if before:
                        parts.append(_deletion(before, idx))
                    if after:
                        parts.append(_insertion(after, idx))
                if not matched and text:
                    parts.append(_text(text))
                parts.append(_text(" | "))
            if parts:
                parts = parts[:-1]
                blocks.append(_paragraph(parts, attrs={"data-row": r}))

    return _doc(*blocks)


# ── .pptx ─────────────────────────────────────────────────────────────────────
def _synthesize_pptx(before_bytes: bytes, details: list[dict] | None) -> dict[str, Any]:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(before_bytes))
    detail_by_ref: dict[str, list[tuple[int, dict]]] = {}
    for i, d in enumerate(details or []):
        if isinstance(d, dict) and d.get("ref"):
            detail_by_ref.setdefault(d["ref"], []).append((i, d))

    blocks: list[dict] = []
    for si, slide in enumerate(prs.slides, start=1):
        blocks.append(
            _paragraph([_text(f"Slide {si}")], attrs={"data-slide": si})
        )
        tf_index = 0
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            tf_index += 1
            ref = f"slide#{si}/tf#{tf_index}"
            text = shape.text_frame.text
            children = []
            changed = False
            for idx, d in detail_by_ref.get(ref, []):
                before = d.get("before") or ""
                after = d.get("after") or ""
                if before:
                    children.append(_deletion(before, idx))
                if after:
                    children.append(_insertion(after, idx))
                changed = True
            if not changed and text:
                children.append(_text(text))
            blocks.append(
                _paragraph(children if children else None,
                           attrs={"data-tf-ref": ref})
            )

    return _doc(*blocks)


# ── Fallback plain render (no marks) ──────────────────────────────────────────
def _render_plain_fallback(raw: bytes | None, fmt: str | None) -> dict[str, Any]:
    if not raw:
        return _empty_doc()
    blocks: list[dict] = []
    try:
        if fmt == "xlsx":
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(raw))
            for ws in wb.worksheets:
                blocks.append(_paragraph([_text(f"Sheet: {ws.title}")]))
                for r in range(1, (ws.max_row or 0) + 1):
                    row_parts = []
                    for c in range(1, (ws.max_column or 0) + 1):
                        v = ws.cell(r, c).value
                        if v is not None:
                            row_parts.append(str(v))
                    if row_parts:
                        blocks.append(_paragraph([_text(" | ".join(row_parts))]))
        elif fmt == "pptx":
            from pptx import Presentation

            prs = Presentation(io.BytesIO(raw))
            for si, slide in enumerate(prs.slides, start=1):
                blocks.append(_paragraph([_text(f"Slide {si}")]))
                for shape in slide.shapes:
                    if shape.has_text_frame and shape.text_frame.text:
                        blocks.append(_paragraph([_text(shape.text_frame.text)]))
    except Exception:
        pass
    if not blocks:
        blocks = [_paragraph([_text("(无法渲染文件内容)")])]
    return _doc(*blocks)


# ── Public API ────────────────────────────────────────────────────────────────
def synthesize_review_doc(
    before_bytes: bytes | None,
    current_bytes: bytes | None,
    fmt: str | None,
    details: list[dict] | None,
) -> dict[str, Any]:
    """Build a TipTap JSON review document.

    Prefers the before-backup + structured ``details`` (so ``changeId`` aligns with
    the frontend's ``officeDetail`` index). Falls back to a plain (unmarked) render
    of the current/before file when ``details`` or ``before_ref`` are unavailable.
    """
    if fmt == "xlsx" and before_bytes is not None:
        try:
            return _synthesize_xlsx(before_bytes, details)
        except Exception:
            pass
    if fmt == "pptx" and before_bytes is not None:
        try:
            return _synthesize_pptx(before_bytes, details)
        except Exception:
            pass
    return _render_plain_fallback(current_bytes or before_bytes, fmt)


def apply_office_subset(
    before_bytes: bytes, fmt: str, operations: list[dict]
) -> bytes:
    """Re-materialize an office file from its original backup applying only the
    accepted ``operations`` subset. Returns the new file bytes.

    Delegates to the shared engine in
    ``prismclaw.engine.review.changes.apply_office_ops`` so the edit path
    and the apply path use identical op semantics.
    """
    if _apply_office_ops_ref is not None:
        return _apply_office_ops_ref(before_bytes, fmt, operations)
    raise RuntimeError("apply_office_ops not injected; call _set_apply_office_ops first")


def count_marks(doc: dict[str, Any]) -> tuple[int, int]:
    """Count insertion/deletion marks in a TipTap doc (for response metadata)."""
    inserted = 0
    deleted = 0

    def _walk(node: dict[str, Any]) -> None:
        nonlocal inserted, deleted
        for m in node.get("marks", []):
            if m.get("type") == "insertion":
                inserted += 1
            elif m.get("type") == "deletion":
                deleted += 1
        for child in node.get("content", []):
            _walk(child)

    _walk(doc)
    return inserted, deleted


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
