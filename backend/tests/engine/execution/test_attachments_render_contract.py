"""附件渲染契约测试。

锁定 attachments.py 的架构不变量（不是实现细节）：

1. kind → (真实 path, 正确工具) 的引用映射，引用文本不得出现完整性断言
2. 存在降级 / 未提取附件时，任何通道都不得出现完整性断言
3. 通道隔离：display 只放附件块与中性占位；policy 只在有附件或消息提到上传文件时出现
4. 混合输入（2 图 + 1 文档 + 1 不支持格式 + 超预算）→ 只给引用清单
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prismclaw.engine.execution.request import attachments as attachments_mod
from prismclaw.schema.agent.data_types import FileBlock, ImageBlock, TextBlock

# 完整性断言词：任何通道都不得对附件内容使用这些表述。
_COMPLETENESS_MARKERS = ("完整", "全部", "所有", "complete", "all of", "everything")


def _flat(content: object) -> str:
    """把 display/llm content（字符串或块列表）摊平成文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(getattr(block, "text", "") or "" for block in content)  # type: ignore[union-attr]


def _assert_no_completeness_claim(text: str) -> None:
    lowered = text.lower()
    for marker in _COMPLETENESS_MARKERS:
        assert marker not in lowered, f"completeness claim {marker!r} in: {text}"


def _image(path: str, ocr: str | None = None) -> ImageBlock:
    block = ImageBlock(type="image", data="", mime_type="image/png", source_path=path)
    if ocr is not None:
        block.text = ocr
    return block


class _TinyBudget:
    """history 预算为 0 → 附件字符预算落到下限 10_000。"""

    def compute_history_budget(self, model_max_output: object = None) -> int:
        return 0


def _stub_ocr(monkeypatch: pytest.MonkeyPatch, results: dict[str, str]) -> None:
    monkeypatch.setattr(attachments_mod, "_batch_ocr_attachments", lambda **_: results)


def _route(
    workspace: Path,
    message: str,
    raw: list[object],
    budget: object = None,
) -> attachments_mod.AttachmentRoutingDecision:
    return attachments_mod.route_attachments_for_turn(
        user_message=message,
        raw_attachments=raw,
        workspace_path=workspace,
        context_budget=budget,
    )


# ---------------------------------------------------------------------------
# 1. kind → path + 工具
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("unit", "must_contain", "forbidden"),
    [
        (attachments_mod.AttachmentUnit(path="u/a.png", kind="image", image_index=2), ("u/a.png", "read 工具"), ()),
        (
            attachments_mod.AttachmentUnit(path="u/a.png", kind="image", image_index=2, chars=120),
            ("u/a.png", "read 工具", "120"),
            (),
        ),
        (
            attachments_mod.AttachmentUnit(
                path="u/spec.docx",
                kind="document",
                chars=900,
                content_blocks=[TextBlock(type="text", text="x" * 900)],
            ),
            ("u/spec.docx", "read"),
            ("read_image",),
        ),
        (attachments_mod.AttachmentUnit(path="u/blob.bin", kind="file"), ("u/blob.bin", "read"), ("read_image",)),
    ],
)
def test_reference_carries_path_tool_and_no_completeness_claim(
    unit: attachments_mod.AttachmentUnit, must_contain: tuple[str, ...], forbidden: tuple[str, ...]
) -> None:
    notes = attachments_mod._reference_lines(
        unit, image_total=2, include_self=True, ocr_inlined=False
    )
    assert len(notes) == 1
    note = notes[0]
    for fragment in must_contain:
        assert fragment in note
    for fragment in forbidden:
        assert fragment not in note
    _assert_no_completeness_claim(note)


# ---------------------------------------------------------------------------
# 2. 降级 / 未提取附件 → 任何通道都无完整性断言
# ---------------------------------------------------------------------------

def test_no_completeness_claim_when_image_ocr_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_ocr(monkeypatch, {})
    decision = _route(tmp_path, "", [{"type": "image", "source_path": "uploads/a.png", "mime_type": "image/png"}])

    llm = _flat(decision.llm_content)
    assert "uploads/a.png" in llm and "read 工具" in llm
    for channel in (decision.display_content, decision.llm_content, decision.attachment_guidance):
        _assert_no_completeness_claim(_flat(channel))


def test_no_completeness_claim_when_file_not_extracted(tmp_path: Path) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "blob.xyz").write_bytes(b"binary")
    decision = _route(tmp_path, "看下这个文件", [{"type": "file", "path": "uploads/blob.xyz", "name": "blob.xyz"}])

    llm = _flat(decision.llm_content)
    assert "uploads/blob.xyz" in llm
    for channel in (decision.display_content, decision.llm_content, decision.attachment_guidance):
        _assert_no_completeness_claim(_flat(channel))


# ---------------------------------------------------------------------------
# 3. 通道隔离
# ---------------------------------------------------------------------------

def test_policy_only_when_attachments_or_uploaded_paths(tmp_path: Path) -> None:
    plain = _route(tmp_path, "今天天气如何", [])
    assert plain.attachment_guidance is None
    # 无附件时消息原样透传，不附加任何附件叙述
    assert plain.display_content == "今天天气如何" and plain.llm_content == "今天天气如何"

    mentioned = _route(tmp_path, "看看 uploads/report.md 的内容", [])
    assert mentioned.attachment_guidance is not None


def test_display_channel_has_no_policy_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_ocr(monkeypatch, {"uploads/a.png": "红色警示框"})
    decision = _route(tmp_path, "", [{"type": "image", "source_path": "uploads/a.png", "mime_type": "image/png"}])

    display = _flat(decision.display_content)
    assert "Attachment Handling" not in display
    assert "read_image" not in display
    # 无文字说明 → display 只含附件块本身，不再附加「已上传附件」冗余占位文字
    # （缩略图/文件卡即为可视指示）；占位文字本身也不得含任何指令。
    assert "已上传附件" not in display
    assert "read" not in display.lower()
    # 附件仍以块形式进入 display（缩略图渲染依赖该块，而非占位文字）
    assert isinstance(decision.display_content, list)
    assert any(getattr(b, "type", None) == "image" for b in decision.display_content)


def test_display_truncation_never_leaks_into_llm(tmp_path: Path) -> None:
    """display 与 llm 两通道解耦：文档正文只进 llm 通道（完整），display 仅源文件卡，
    绝不把解析出的长正文铺进气泡（亦无截断泄漏）。"""
    long_text = "y" * (attachments_mod._MAX_DISPLAY_TEXT_CHARS + 100)
    doc = FileBlock(type="file", path="uploads/big.md", name="big.md")
    projection = attachments_mod.project_blocks([doc], {"uploads/big.md": [TextBlock(type="text", text=long_text)]})

    display_blocks = [b for unit in projection.entries for b in unit.display_blocks]
    llm_text = _flat(attachments_mod._render_llm(projection, inlined=True, char_budget=10**9))
    # display 只含源文件卡（无解析正文，更无截断泄露）
    assert len(display_blocks) == 1
    assert display_blocks[0].type == "file"
    assert getattr(display_blocks[0], "is_source_document", False) is True
    assert display_blocks[0].path == "uploads/big.md"
    assert all(getattr(b, "text", None) != long_text for b in display_blocks)
    # llm 通道拿到完整正文
    assert long_text in llm_text


def test_repeated_file_path_keeps_image_indices_within_total() -> None:
    """同一文档被重复挂载时，引用序号不得越界。"""
    doc = FileBlock(type="file", path="uploads/spec.docx", name="spec.docx")
    projection = attachments_mod.project_blocks(
        [doc, doc],
        {"uploads/spec.docx": [_image("uploads/img/a.png"), _image("uploads/img/b.png")]},
    )
    notes = _flat(attachments_mod._render_llm(projection, inlined=True, char_budget=10**9))
    assert projection.image_total == 4
    assert "#4/4" in notes and "#3/4" in notes
    assert "#1/1" not in notes and "#5/" not in notes


def test_image_payload_without_path_is_not_announced_as_readable(tmp_path: Path) -> None:
    """无工作区路径的图片无法 read_image，不得出现在任何叙述里。"""
    decision = _route(tmp_path, "", [{"type": "image", "data": "BASE64", "mime_type": "image/png"}])
    assert decision.display_content == "" and decision.llm_content == ""
    assert decision.attachment_guidance is None
    assert decision.downgraded_image_count == 0


def test_inlined_ocr_images_need_no_tool_reference(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """有 OCR 文本且未超预算时，图片内容已在上下文，不应再要求调用工具。"""
    _stub_ocr(monkeypatch, {"u/a.png": "柱状图", "u/b.png": "折线图"})
    raw = [
        {"type": "image", "source_path": "u/a.png", "mime_type": "image/png"},
        {"type": "image", "source_path": "u/b.png", "mime_type": "image/png"},
    ]
    decision = _route(tmp_path, "对比两张图", raw)

    llm = _flat(decision.llm_content)
    assert "柱状图" in llm and "折线图" in llm
    assert "read_image" not in llm
    assert decision.downgraded_image_count == 0


def test_document_without_text_falls_back_to_file_reference() -> None:
    """提取结果为空白文本（如纯图片 pptx）时按未提取处理，给 read 引用而非空内容。"""
    doc = FileBlock(type="file", path="uploads/deck.pptx", name="deck.pptx")
    projection = attachments_mod.project_blocks(
        [doc], {"uploads/deck.pptx": [TextBlock(type="text", text="  \n")]}
    )

    assert [unit.kind for unit in projection.entries] == ["file"]
    llm = _flat(attachments_mod._render_llm(projection, inlined=True, char_budget=10**9))
    assert "uploads/deck.pptx" in llm and "read 工具" in llm


def test_workspace_escaping_paths_are_dropped(tmp_path: Path) -> None:
    """越界路径（``..`` / 绝对路径）不是附件：不得内联内容，也不得出现在任何通道。"""
    outside = tmp_path.parent / "outside_secret.md"
    outside.write_text("SECRET", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    decision = _route(
        workspace,
        "",
        [
            {"type": "file", "path": "../outside_secret.md", "name": "secret.md"},
            {"type": "file", "path": str(outside), "name": "secret.md"},
            {"type": "image", "source_path": "../outside_secret.md", "mime_type": "text/markdown"},
        ],
    )
    for channel in (decision.display_content, decision.llm_content, decision.attachment_guidance):
        assert "SECRET" not in _flat(channel) and "outside_secret" not in _flat(channel)
    assert decision.downgraded_image_count == 0


def test_image_only_document_keeps_document_kind_and_image_references() -> None:
    """只有内嵌图片的文档不是"无内容文件"：文档引用指向图像清单，图片走 read。"""
    doc = FileBlock(type="file", path="uploads/deck.docx", name="deck.docx")
    projection = attachments_mod.project_blocks(
        [doc], {"uploads/deck.docx": [_image("uploads/img/slide1.png")]}
    )

    assert [unit.kind for unit in projection.entries] == ["document"]
    llm = _flat(attachments_mod._render_llm(projection, inlined=True, char_budget=10**9))
    assert "uploads/deck.docx" in llm and "请见下方图片引用" in llm
    assert "uploads/img/slide1.png" in llm and "read 工具" in llm


# ---------------------------------------------------------------------------
# 4. 混合输入 + 超预算 → 仅引用清单
# ---------------------------------------------------------------------------

def test_mixed_input_over_budget_renders_reference_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "spec.md").write_text("x" * 12_000, encoding="utf-8")
    (tmp_path / "uploads" / "blob.xyz").write_bytes(b"binary")
    _stub_ocr(monkeypatch, {"uploads/a.png": "第一张图的文字"})

    raw = [
        {"type": "image", "source_path": "uploads/a.png", "mime_type": "image/png"},
        {"type": "image", "source_path": "uploads/b.png", "mime_type": "image/png"},
        {"type": "file", "path": "uploads/spec.md", "name": "spec.md"},
        {"type": "file", "path": "uploads/blob.xyz", "name": "blob.xyz"},
    ]
    decision = _route(tmp_path, "", raw, budget=_TinyBudget())

    llm = _flat(decision.llm_content)
    assert "超出内联预算" in llm
    for path in ("uploads/a.png", "uploads/b.png", "uploads/spec.md", "uploads/blob.xyz"):
        assert path in llm
    assert "OCR 文本已在上下文内" in llm and "其中 0 张" in llm
    assert decision.downgraded_image_count == 2
    assert decision.attachment_guidance is not None
    _assert_no_completeness_claim(llm)


def test_over_budget_document_still_references_embedded_images() -> None:
    """超预算时文档内嵌图片不会随 content_blocks 被丢弃——路径必须仍可见。"""
    doc = FileBlock(type="file", path="uploads/spec.docx", name="spec.docx")
    projection = attachments_mod.project_blocks(
        [doc],
        {
            "uploads/spec.docx": [
                TextBlock(type="text", text="x" * 20_000),
                _image("uploads/img/figure1.png"),
            ]
        },
    )

    manifest = attachments_mod._reference_manifest(projection, char_budget=10_000)
    assert "uploads/spec.docx" in manifest
    assert "uploads/img/figure1.png" in manifest and "read 工具" in manifest


def test_over_budget_document_references_embedded_image_with_ocr() -> None:
    """内嵌图带 OCR 时，超预算清单仍须给出其 path（否则该图彻底丢失）。"""
    doc = FileBlock(type="file", path="uploads/spec.docx", name="spec.docx")
    figure = _image("uploads/img/figure2.png", "图二：流程图")
    projection = attachments_mod.project_blocks(
        [doc], {"uploads/spec.docx": [TextBlock(type="text", text="x" * 20_000), figure]}
    )

    manifest = attachments_mod._reference_manifest(projection, char_budget=10_000)
    assert "uploads/img/figure2.png" in manifest
    assert "read 工具" in manifest
    # 内联模式下该图 OCR 已在正文，不应重复要求调用工具
    inline = _flat(attachments_mod._render_llm(projection, inlined=True, char_budget=10**9))
    assert "图二：流程图" in inline and "read 工具" not in inline
