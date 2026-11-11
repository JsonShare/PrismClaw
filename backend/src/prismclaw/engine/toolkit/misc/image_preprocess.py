"""图片预处理：base64 传输前按大小决定压缩策略。

vLLM 远程部署且服务器不互通，base64 是唯一图片传入路径。本模块在编码前
按文件大小分流：

- >100MB：拒绝（纯防 OOM）
- <5MB：不压缩，保留原格式（OCR 质量优先；5MB 图 base64 ≈ 6.7MB，
  远低于 vLLM 32MB body 限制，传输安全）
- ≥5MB：降采样到 2048 长边 + JPEG q85，利用 vLLM 内部降采样特性
  （max_pixels 默认 2048×2048，超采样被丢弃，大 payload 纯浪费）

边界：
- GIF 取首帧（Pillow 默认，与 vLLM 行为一致）
- TIFF 多页只取首页（二期评估是否需分页处理）
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

_MAX_LONG_EDGE = 2048              # Qwen3-VL 有效分辨率上限（默认 max_pixels）
_HARD_MAX_FILE_SIZE = 100_000_000  # 100MB，纯防 OOM
_COMPRESS_THRESHOLD = 5_000_000    # 5MB 以下不压缩（OCR 质量优先）

# 从 read_image.py 迁移：小图保留原格式需要 mime 映射
_MIME_MAP: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "jfif": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}


class ImageTooLargeError(ValueError):
    """图片过大，无法处理。"""


def _mime_for(ext: str) -> str:
    """扩展名 -> mime。未知扩展名回退 image/jpeg（vLLM 通用兜底）。"""
    return _MIME_MAP.get(ext.lower().lstrip("."), "image/jpeg")


def compress_for_vision(path: Path) -> tuple[bytes, str]:
    """返回 (bytes, mime)。

    - >100MB：抛 ImageTooLargeError
    - <5MB：不压缩，保留原格式（OCR 质量优先，base64 后 <7MB 安全）
    - ≥5MB：降采样到 2048 长边 + JPEG q85（利用 vLLM 内部降采样特性）

    边界：GIF 取首帧（Pillow 默认，与 vLLM 一致）；TIFF 多页只取首页
    （二期评估是否需分页处理）。
    """
    size = path.stat().st_size
    if size > _HARD_MAX_FILE_SIZE:
        raise ImageTooLargeError(f"图片过大（{size // 1048576}MB），无法处理")
    ext = path.suffix.lower().lstrip(".")
    # 小图保留原格式不压缩：压缩对 OCR 小字有损，5MB 阈值平衡质量与 payload。
    if size < _COMPRESS_THRESHOLD:
        return path.read_bytes(), _mime_for(ext)
    img = Image.open(path)
    if max(img.size) > _MAX_LONG_EDGE:
        ratio = _MAX_LONG_EDGE / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.Resampling.LANCZOS,
        )
    # RGBA/P/LA 等带透明通道或调色板模式无法直接存 JPEG，需转 RGB。
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), "image/jpeg"


def compress_for_vision_bytes(img_data: bytes, ext: str = "") -> tuple[bytes, str]:
    """与 compress_for_vision 逻辑相同，但输入为 bytes 而非文件路径。

    用于 runtime.read_bytes() 获取图片后，在 C Plane 内完成压缩与 base64 编码。
    """
    size = len(img_data)
    if size > _HARD_MAX_FILE_SIZE:
        raise ImageTooLargeError(f"图片过大（{size // 1048576}MB），无法处理")
    # 小图保留原格式不压缩
    if size < _COMPRESS_THRESHOLD:
        return img_data, _mime_for(ext)
    img = Image.open(io.BytesIO(img_data))
    if max(img.size) > _MAX_LONG_EDGE:
        ratio = _MAX_LONG_EDGE / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.Resampling.LANCZOS,
        )
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), "image/jpeg"
