"""SDD R2′.3 regression: SOP-extraction tool-result previews.

Legacy failure being locked out: ``_build_segments`` clipped every tool
result to 300 chars — facts living past char 300 (paths, config values,
error codes) could NEVER enter durable memory, contradicting the A2
"decision-layer inputs are never truncated" declaration in the same
function.

New contract: 2000-char previews; ``<persisted-output>`` references follow
their on-disk file (fuller than the 500-char inline preview inside the
reference).
"""

from __future__ import annotations


from prismclaw.engine.services._sop_parse import (
    _TOOL_RESULT_PREVIEW_CHARS,
    _tool_result_preview,
)


class TestToolResultPreview:
    def test_short_result_untouched(self):
        assert _tool_result_preview("ok") == "ok"

    def test_long_result_clipped_at_2000_with_marker(self):
        text = "x" * 5000
        out = _tool_result_preview(text)
        assert len(out) < len(text)
        assert out.startswith("x" * 2000)
        assert "5000 chars total" in out
        assert "preview truncated" in out

    def test_boundary_2000_not_clipped(self):
        text = "y" * _TOOL_RESULT_PREVIEW_CHARS
        assert _tool_result_preview(text) == text

    def test_persisted_reference_follows_file(self, tmp_path):
        full_file = tmp_path / "tr_abc.txt"
        full_text = "FACT-AT-CHAR-800-" + "z" * 1600  # 1617 chars, < 2000
        full_file.write_text(full_text, encoding="utf-8")
        ref = (
            "<persisted-output>\n"
            "tool_use_id: abc\n"
            "size: 1617 chars\n"
            "preview: FACT-AT-CHAR-800-...\n"
            f"[Full output persisted to: {full_file}] Use the Read tool to view the full contents.\n"
        )
        out = _tool_result_preview(ref)
        # The full file content (not just the 24-char inline preview) is read:
        assert out.count("z") == 1600
        assert "preview truncated" not in out

    def test_persisted_file_larger_than_preview_clipped(self, tmp_path):
        full_file = tmp_path / "tr_big.txt"
        full_file.write_text("H" * 9000, encoding="utf-8")
        ref = (
            "<persisted-output>\n"
            f"[Full output persisted to: {full_file}]\n"
        )
        out = _tool_result_preview(ref)
        assert out.startswith("H" * 2000)
        assert "9000 chars total" in out

    def test_persisted_reference_missing_file_falls_back(self, tmp_path):
        missing = str(tmp_path / "missing.txt")
        ref = (
            "<persisted-output>\n"
            "tool_use_id: abc\n"
            f"[Full output persisted to: {missing}] Use the Read tool.\n"
        )
        out = _tool_result_preview(ref)
        # Falls back to the reference text itself (short → unclipped).
        assert "tool_use_id: abc" in out
        assert "preview truncated" not in out
