"""Structured-office edit/revert engines (xlsx / pptx).

Pure format engines with no workspace IO of their own. The strategy layer
(``SpreadsheetChange``) decides when to read/write the live file and how to
record the result. Every failure is fail-loud: a bad ``ref`` or unsupported
``op`` raises ``OfficeEditError`` — never a silent skip.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, cast

from prismclaw.schema.agent.review_errors import OfficeEditError, OfficeRevertConflict
from prismclaw.engine.toolkit.file.workspace import clean_rel_path

_OFFICE_FMT_BY_SUFFIX = {
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

_STRUCTURED_OFFICE_FMT_BY_SUFFIX = {
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}


def fmt_from_path(path: str) -> str | None:
    """Map a file path to a supported office format key, or ``None``.

    Runs ``clean_rel_path`` first so LLM template-leak wrappers (``{}``,
    quotes, brackets, trailing punctuation, etc.) never make it to
    ``Path.suffix``. Without this, ``"uploads/a.pptx{}"`` ends up with
    suffix ``".pptx{}"`` and the user sees "only .docx/.xlsx/.pptx are
    supported" for a file that is genuinely a .pptx.
    """
    cleaned = clean_rel_path(path)
    if cleaned is None:
        return None
    return _OFFICE_FMT_BY_SUFFIX.get(Path(cleaned).suffix.lower())


def structured_office_fmt_from_path(path: str) -> str | None:
    """Map a file path to a structured-office review format key, or ``None``.

    Mirrors ``fmt_from_path``'s ``clean_rel_path`` pre-clean so suffix
    extraction is robust to the same LLM template artifacts.
    """
    cleaned = clean_rel_path(path)
    if cleaned is None:
        return None
    return _STRUCTURED_OFFICE_FMT_BY_SUFFIX.get(Path(cleaned).suffix.lower())


def _ensure_structured_office_fmt(fmt: str) -> None:
    if fmt not in ("xlsx", "pptx"):
        raise OfficeEditError(
            "结构化办公引擎仅支持 .xlsx/.pptx；"
            ".docx 请使用原生 docx 审查产物流程"
        )


def _set_tf_text_preserve(tf: Any, text: str) -> None:
    """Set a PowerPoint text frame's text, keeping the first run alive."""
    if tf.paragraphs:
        para = tf.paragraphs[0]
        runs = list(para.runs)
        if runs:
            runs[0].text = text
            for r in runs[1:]:
                r._r.getparent().remove(r._r)
        else:
            para.add_run().text = text
    else:
        tf.add_paragraph().add_run().text = text


def _apply_xlsx(base_bytes: bytes, operations: list[dict]) -> bytes:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(base_bytes))
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        ref = op.get("ref") or ""
        o = op.get("op")
        m = re.match(r"s#(\d+)!r#(\d+)c#(\d+)", ref)
        if not m or o != "set_cell":
            raise OfficeEditError(f"xlsx unsupported ref/op: {ref}/{o}")
        si, r, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= si <= len(wb.worksheets)):
            raise OfficeEditError(f"xlsx sheet #{si} out of range")
        value = op.get("text") if op.get("text") is not None else op.get("value")
        wb.worksheets[si - 1].cell(row=r, column=c).value = value

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pptx_tf_map(prs):
    tf_map: dict[str, Any] = {}
    for si, slide in enumerate(prs.slides, start=1):
        tf_index = 0
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            tf_index += 1
            tf_map[f"slide#{si}/tf#{tf_index}"] = shape.text_frame
    return tf_map


def _apply_pptx(base_bytes: bytes, operations: list[dict]) -> bytes:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(base_bytes))
    tf_map = _pptx_tf_map(prs)

    for op in operations or []:
        if not isinstance(op, dict):
            continue
        ref = op.get("ref")
        o = op.get("op")
        tf = tf_map.get(ref)
        if tf is None:
            raise OfficeEditError(f"pptx ref not found: {ref}")
        if o in ("set_text_frame", "replace_run", "replace_paragraph"):
            _set_tf_text_preserve(tf, op.get("text") or "")
        elif o == "delete_text_frame":
            _set_tf_text_preserve(tf, "")
        else:
            raise OfficeEditError(f"pptx unsupported op: {o}")

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def apply_office_ops(base_bytes: bytes, fmt: str, operations: list[dict]) -> bytes:
    """Apply a list of structured operations to ``base_bytes`` (the current file).

    Fail-loud: a bad ``ref``/``op`` raises ``OfficeEditError`` (the caller turns it
    into a tool error). Returns the edited file bytes.
    """
    _ensure_structured_office_fmt(fmt)
    if fmt == "xlsx":
        return _apply_xlsx(base_bytes, operations)
    if fmt == "pptx":
        return _apply_pptx(base_bytes, operations)
    raise OfficeEditError(f"unsupported office format: {fmt}")


def _overlap_decision(cur: str, before: str, after: str, ref: str, fmt: str) -> bool:
    """Decide whether a modify-style revert is safe.

    Returns ``True``  → apply the revert (restore ``before``); the location still
                         holds this change's result (``after``).
    Returns ``False`` → skip (idempotent); the location already equals ``before``.
    Raises ``OfficeRevertConflict`` → the location holds neither ``before`` nor
                         ``after``, i.e. a later edit changed it; reverting would
                         destroy that newer edit.
    """
    if cur == after:
        return True
    if cur == before:
        return False  # already reverted / never diverged
    raise OfficeRevertConflict(
        f"{fmt} 引用 {ref} 已被后续编辑改动"
        f"（当前内容既不匹配本次改动的 before，也不匹配 after）；"
        f"若拒绝本次审查会覆盖那次更新的编辑——请重新审查最新改动。"
    )


def _revert_xlsx(current_bytes: bytes, rejected: list[dict]) -> bytes:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(current_bytes))
    for d in reversed(rejected):
        o = d.get("op")
        ref = d.get("ref") or ""
        m = re.match(r"s#(\d+)!r#(\d+)c#(\d+)", ref)
        if not m or o != "set_cell":
            raise OfficeEditError(f"xlsx unsupported revert ref/op: {ref}/{o}")
        si, r, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= si <= len(wb.worksheets)):
            raise OfficeEditError(f"xlsx sheet #{si} out of range")
        cell = wb.worksheets[si - 1].cell(row=r, column=c)
        cur = "" if cell.value is None else str(cell.value)
        before = "" if d.get("before") is None else str(d.get("before"))
        after = "" if d.get("after") is None else str(d.get("after"))
        if _overlap_decision(cur, before, after, ref, "xlsx"):
            cell.value = d.get("before")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _revert_pptx(current_bytes: bytes, rejected: list[dict]) -> bytes:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(current_bytes))
    tf_map = _pptx_tf_map(prs)
    for d in reversed(rejected):
        o = d.get("op")
        ref = d.get("ref")
        tf = tf_map.get(ref)
        if tf is None:
            raise OfficeEditError(f"pptx ref not found on revert: {ref}")
        if o in ("set_text_frame", "replace_run", "replace_paragraph", "delete_text_frame"):
            cur = tf.text
            before = d.get("before") or ""
            after = d.get("after") or ""
            # delete_text_frame sets after="" ; treat empty current as the applied state.
            if _overlap_decision(cur, before, after, ref, "pptx"):
                _set_tf_text_preserve(tf, before)
        else:
            raise OfficeEditError(f"pptx unsupported revert op: {o}")
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def revert_office_ops(current_bytes: bytes, fmt: str, rejected: list[dict]) -> bytes:
    """Reverse only the **rejected** changes on the CURRENT file.

    ``rejected`` is the subset of ``officeDetail`` entries whose decision was
    'reject'. Each entry carries ``op``/``ref``/``before``/``after`` so the
    removed element can be reinserted exactly where it was. Accepted changes are
    untouched, so unrelated content — including edits made in later rounds — is
    preserved.

    Overlap safety: if a rejected location was subsequently changed by a *later*
    edit, reversing it would silently destroy that newer edit. We detect this
    (the location holds neither ``before`` nor ``after``) and raise
    ``OfficeRevertConflict`` (fail-loud) instead of corrupting data.
    """
    if not rejected:
        return current_bytes
    _ensure_structured_office_fmt(fmt)
    if fmt == "xlsx":
        return _revert_xlsx(current_bytes, rejected)
    if fmt == "pptx":
        return _revert_pptx(current_bytes, rejected)
    raise OfficeEditError(f"unsupported office format: {fmt}")


def _read_ref_value(base_bytes: bytes, fmt: str, ref: str) -> str | None:
    """Return the text currently at ``ref`` in ``base_bytes``, or ``None`` if the
    ref is unknown. Used to populate ``officeDetail[*].before``."""
    _ensure_structured_office_fmt(fmt)
    if fmt == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(base_bytes))
        m = re.match(r"s#(\d+)!r#(\d+)c#(\d+)", ref or "")
        if not m:
            return None
        si, r, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= si <= len(wb.worksheets)):
            return None
        val = wb.worksheets[si - 1].cell(row=r, column=c).value
        return "" if val is None else str(val)
    if fmt == "pptx":
        from pptx import Presentation

        prs = Presentation(io.BytesIO(base_bytes))
        tf = _pptx_tf_map(prs).get(ref)
        return tf.text if tf is not None else None
    return None


def build_office_detail(base_bytes: bytes, fmt: str, operations: list[dict]) -> list[dict]:
    """Build the ``officeDetail`` list (1:1 with ``operations``) for the review UI.

    Each entry mirrors the frontend ``OfficeChangeDetail`` shape and adds a
    ``locator`` (used by ``revert_office_ops`` to reverse deletions/insertions
    precisely):
      {ref, op, before, after, inserted_chars, deleted_chars, locator}
    """
    _ensure_structured_office_fmt(fmt)
    details: list[dict] = []
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        ref = op.get("ref") or ""
        o = op.get("op") or ""
        after = op.get("text") or ""
        before_val = _read_ref_value(base_bytes, fmt, ref) or ""

        if o in ("replace_paragraph", "replace_run", "set_cell", "set_text_frame"):
            before, after_text = before_val, after
            locator = {"kind": "modify", "ref": ref}
        elif o == "delete_paragraph":
            before, after_text = before_val, ""
            # prev_ref lets revert reinsert the paragraph right after its neighbour.
            mm = re.match(r"p#(\d+)", ref)
            prev_ref = f"p#{int(mm.group(1)) - 1}" if (mm and int(mm.group(1)) > 1) else None
            locator = {"kind": "delete_para", "prev_ref": prev_ref}
        elif o == "delete_text_frame":
            before, after_text = before_val, ""
            locator = {"kind": "modify", "ref": ref}
        elif o in ("insert_after",):
            before, after_text = "", after
            locator = {"kind": "insert_after", "ref": ref}
        elif o in ("insert_row", "insert_column"):
            before, after_text = "", after
            locator = {"kind": o, "ref": ref}
        else:
            before, after_text = before_val, after
            locator = {"kind": "modify", "ref": ref}

        details.append(
            {
                "ref": ref,
                "op": o,
                "before": before,
                "after": after_text,
                "inserted_chars": len(after_text),
                "deleted_chars": len(before),
                "locator": locator,
            }
        )
    return details


def read_office_text(raw_bytes: bytes, fmt: str) -> str:
    """Render an office file as plain text with ``ref`` anchors so the agent can
    discover the geometry it needs to address with ``read`` + programmatic edits."""
    _ensure_structured_office_fmt(fmt)
    if fmt == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes))
        lines = []
        for si, ws in enumerate(wb.worksheets, start=1):
            lines.append(f"# Sheet {si}: {ws.title}")
            for r in range(1, (ws.max_row or 0) + 1):
                for c in range(1, (ws.max_column or 0) + 1):
                    val = ws.cell(row=r, column=c).value
                    if val is not None:
                        lines.append(f"s#{si}!r#{r}c#{c}: {val}")
        return "\n".join(lines)
    if fmt == "pptx":
        from pptx import Presentation

        prs = Presentation(io.BytesIO(raw_bytes))
        lines = []
        for si, slide in enumerate(prs.slides, start=1):
            lines.append(f"# Slide {si}")
            tf_index = 0
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                tf_index += 1
                text_frame = cast(Any, shape).text_frame
                lines.append(f"slide#{si}/tf#{tf_index}: {text_frame.text}")
        return "\n".join(lines)
    return ""
