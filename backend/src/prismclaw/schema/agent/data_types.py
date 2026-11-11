from __future__ import annotations

"""Cache strategy and content block types shared across layers.

Consolidates CacheStrategySnapshot (formerly cache_types.py) and
TextBlock/ImageBlock/FileBlock/ContentBlock (formerly content_types.py)
into a single module to reduce file fragmentation in contracts/agent/.
"""

from typing import Literal
from typing_extensions import TypedDict, Union

from pydantic import BaseModel


# ── Cache strategy ──────────────────────────────────────────────────────

class CacheStrategySnapshot(TypedDict, total=False):
    request_mode: str | None
    strategy_kind: str
    provider_adapter: str
    static_digest: str
    static_segment_count: int
    cacheable_segment_count: int
    trailing_part_count: int
    model_supports_cache_blocks: bool
    prior_strategy_found: bool
    prior_static_digest: str | None
    reused_prior_strategy: bool
    replacement_state_digest: str | None
    replacement_decision_count: int
    prior_replacement_state_digest: str | None
    reused_replacement_state: bool
    section_hashes: dict[str, str]


# ── Content blocks ──────────────────────────────────────────────────────

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str
    source_path: str | None = None
    text: str = ""          # OCR 识别结果


class FileBlock(BaseModel):
    type: Literal["file"] = "file"
    path: str
    name: str = ""
    mime_type: str | None = None
    size_bytes: int | None = None
    # 标记「这是被分解展示的源文档本身」（如上传的 Word 经抽取后内联了图片+文字，
    # 但仍保留此文件卡作为「来源记录」）。前端据此把源文档卡渲染在内容之前作为表头，
    # 而非混在普通附件里。
    is_source_document: bool = False


ContentBlock = Union[TextBlock, ImageBlock, FileBlock]


__all__ = [
    "CacheStrategySnapshot",
    "TextBlock",
    "ImageBlock",
    "FileBlock",
    "ContentBlock",
]
