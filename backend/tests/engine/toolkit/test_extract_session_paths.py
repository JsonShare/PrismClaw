"""文档抽取 / OCR 缓存的落点：会话内文件跟会话走，工作区根文件退回 .work。"""
from __future__ import annotations

from pathlib import Path

from prismclaw.engine.toolkit.file.ocr_cache import (
    ocr_cache_path,
    read_ocr_cache,
    write_ocr_cache,
)
from prismclaw.engine.toolkit.office.document_image_extract import (
    _save_image_block,
    to_model_path,
)


def test_session_source_lands_in_context_blocks(tmp_path: Path) -> None:
    rel = _save_image_block(
        tmp_path, "sessions/s1/inbox/report.docx", 1, ".jpg", b"img"
    )

    assert rel.startswith("sessions/s1/context_blocks/images/")
    assert (tmp_path / rel).is_file()
    # 模型只认虚拟前缀：context_blocks/... 可被 resolver 还原到本会话
    assert to_model_path(rel).startswith("context_blocks/images/")


def test_workspace_root_source_falls_back_to_work_cache(tmp_path: Path) -> None:
    rel = _save_image_block(tmp_path, "notes/report.docx", 1, ".jpg", b"img")

    assert rel.startswith(".work/cache/extracted/")
    assert to_model_path(rel) == rel


def test_to_model_path_folds_session_prefixes() -> None:
    assert to_model_path("sessions/s1/inbox/a.jpg") == "inbox/a.jpg"
    assert to_model_path("sessions/s1/outputs/b.docx") == "outputs/b.docx"
    assert to_model_path("sessions/s1/context_blocks/images/k/c.jpg") == (
        "context_blocks/images/k/c.jpg"
    )
    assert to_model_path("project/readme.md") == "project/readme.md"


def test_ocr_cache_follows_session(tmp_path: Path) -> None:
    image = tmp_path / "sessions" / "s1" / "inbox" / "a.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"img")

    write_ocr_cache(str(tmp_path), str(image), "OCR_TEXT", "test-model")

    cache = ocr_cache_path(str(tmp_path), str(image))
    assert cache.is_relative_to(tmp_path / "sessions" / "s1" / ".work" / "cache" / "ocr")
    assert read_ocr_cache(str(tmp_path), str(image)) == "OCR_TEXT"


def test_ocr_cache_root_file_stays_workspace_level(tmp_path: Path) -> None:
    image = tmp_path / "project" / "a.jpg"
    image.parent.mkdir()
    image.write_bytes(b"img")

    write_ocr_cache(str(tmp_path), str(image), "TEXT", "m")

    cache = ocr_cache_path(str(tmp_path), str(image))
    assert cache.is_relative_to(tmp_path / ".work" / "cache" / "ocr")
