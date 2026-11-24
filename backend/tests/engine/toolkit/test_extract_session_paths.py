"""文档抽取 / OCR 缓存的落点：会话内文件跟会话走，工作区根文件退回 .work。"""

from __future__ import annotations

from pathlib import Path

from prismclaw.engine.toolkit.file.ocr_cache import (
    ocr_cache_path,
    read_ocr_cache,
    write_ocr_cache,
)
from prismclaw.engine.toolkit.office.document_reader import (
    _save_image,
    to_model_path,
)


def test_session_source_lands_in_context_blocks(tmp_path: Path) -> None:
    rel = _save_image(tmp_path, "sessions/s1/inbox/report.docx", 1, ".jpg", b"img")

    assert rel.startswith("sessions/s1/context_blocks/images/")
    assert (tmp_path / rel).is_file()
    # 模型只认虚拟前缀：context_blocks/... 可被 resolver 还原到本会话
    assert to_model_path(rel).startswith("context_blocks/images/")


def test_workspace_root_source_falls_back_to_work_cache(tmp_path: Path) -> None:
    rel = _save_image(tmp_path, "notes/report.docx", 1, ".jpg", b"img")

    assert rel.startswith(".work/cache/extracted/")
    # 工作区根文件的内嵌图挂在 project/ 下，模型可读
    assert to_model_path(rel) == f"project/{rel}"


def test_to_model_path_folds_session_prefixes() -> None:
    assert to_model_path("sessions/s1/inbox/a.jpg") == "inbox/a.jpg"
    assert to_model_path("sessions/s1/outbox/b.docx") == "outbox/b.docx"
    assert to_model_path("sessions/s1/context_blocks/images/k/c.jpg") == (
        "context_blocks/images/k/c.jpg"
    )
    assert to_model_path("project/readme.md") == "project/readme.md"


def test_reextract_preserves_ocr_cache(tmp_path: Path) -> None:
    """内容未变时重抽取不重写图片——stat 指纹保持，OCR 缓存命中。"""
    rel = _save_image(tmp_path, "sessions/s1/inbox/a.docx", 1, ".png", b"png-v1")
    img_path = tmp_path / rel
    st1 = img_path.stat()

    write_ocr_cache(str(tmp_path), rel, "OCR_TEXT", "test-model")

    _save_image(tmp_path, "sessions/s1/inbox/a.docx", 1, ".png", b"png-v1")
    st2 = img_path.stat()

    assert (st1.st_ctime_ns, st1.st_ino) == (st2.st_ctime_ns, st2.st_ino), (
        "内容未变却重写了图片 → OCR 缓存指纹失效"
    )
    assert read_ocr_cache(str(tmp_path), rel) == "OCR_TEXT"


def test_reextract_rewrites_when_image_content_changes(tmp_path: Path) -> None:
    rel = _save_image(tmp_path, "sessions/s1/inbox/a.docx", 1, ".png", b"png-v1")
    assert (tmp_path / rel).read_bytes() == b"png-v1"

    _save_image(tmp_path, "sessions/s1/inbox/a.docx", 1, ".png", b"png-v2")
    assert (tmp_path / rel).read_bytes() == b"png-v2"


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
