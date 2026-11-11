"""共享 OCR 缓存工具 — 缓存路径计算、读取、写入。

所有缓存都是引擎副产物，统一落在 workspace 系统区
``<ws>/.work/cache/ocr/<source-key>/``，绝不写进用户源文件所在目录（D7）。
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_abs_path(workspace_path: str, source_path: str) -> Path:
    """将 source_path 解析为绝对路径。

    若 source_path 已经是绝对路径则直接使用；
    否则拼接 workspace_path / source_path。
    """
    p = Path(source_path)
    if p.is_absolute():
        return p
    return Path(workspace_path) / source_path


def _cache_dir(workspace_path: str, abs_path: Path) -> Path:
    """按源文件绝对路径派生稳定的缓存目录。

    用哈希而非目录镜像，避免把 ``inputs/...``、``sessions/<sid>/...`` 的层级
    结构复制进缓存区（用户源目录名可能含路径分隔语义）。
    """
    source_key = hashlib.sha1(
        str(abs_path.resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:16]
    return Path(workspace_path) / ".work" / "cache" / "ocr" / source_key


def ocr_cache_path(workspace_path: str, source_path: str) -> Path:
    """根据源文件 stat 属性计算 OCR 缓存路径（绝对路径）。

    缓存位于 ``<ws>/.work/cache/ocr/<source-key>/``，
    用文件的 size_ctime_ns_ino 作为指纹。
    stat 失败时返回 Path("/nonexistent/_cache_miss.json")。
    """
    abs_path = _resolve_abs_path(workspace_path, source_path)
    try:
        st = abs_path.stat()
    except OSError:
        return Path("/nonexistent/_cache_miss.json")

    fingerprint = f"{st.st_size}_{st.st_ctime_ns}_{st.st_ino}"
    cache_filename = f"{fingerprint}.json"
    return _cache_dir(workspace_path, abs_path) / cache_filename


def read_ocr_cache(workspace_path: str, source_path: str) -> str:
    """从 OCR 缓存读取识别结果。命中返回文本，未命中或损坏返回空字符串。"""
    cache_file = ocr_cache_path(workspace_path, source_path)
    if not cache_file.exists():
        return ""
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data.get("text", "")
    except Exception:
        pass
    return ""


def write_ocr_cache(workspace_path: str, source_path: str, text: str, model: str) -> None:
    """将 OCR 结果写入缓存。失败时静默忽略。"""
    cache_file = ocr_cache_path(workspace_path, source_path)
    if str(cache_file).startswith("/nonexistent"):
        return
    abs_path = _resolve_abs_path(workspace_path, source_path)
    try:
        st = abs_path.stat()
    except OSError:
        return
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_path": source_path,
            "size": st.st_size,
            "ctime_ns": st.st_ctime_ns,
            "ino": st.st_ino,
            "model": model,
            "text": text,
            "created_at": datetime.now().isoformat(),
        }
        cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def analysis_md_path(workspace_path: str, source_path: str) -> Path:
    """根据源文件 stat 属性计算 analysis.md 路径（绝对路径）。

    与 OCR 缓存同属引擎副产物，落在 ``<ws>/.work/cache/ocr/<source-key>/``。
    """
    abs_path = _resolve_abs_path(workspace_path, source_path)
    try:
        st = abs_path.stat()
    except OSError:
        return Path("/nonexistent/_cache_miss.analysis.md")

    fingerprint = f"{st.st_size}_{st.st_ctime_ns}_{st.st_ino}"
    md_filename = f"{fingerprint}.analysis.md"
    return _cache_dir(workspace_path, abs_path) / md_filename
