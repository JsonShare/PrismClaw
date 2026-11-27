"""专家包持久数据目录。

专家包内容可用 ``${PRISMCLAW_EXPERT_DATA}`` 指向本包稳定目录；该目录在包重装/
升级后保留。本模块是"目录在哪、包名如何变成安全目录名"的唯一来源。

布局（默认全局，与 memory 基目录一致）::

    ~/.prismclaw/expert-data/<sanitized-expert-name>/

基目录可覆盖，便于测试与异构部署重定向。
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "DEFAULT_DATA_BASE_DIRNAME",
    "get_expert_data_dir",
    "resolve_data_base_dir",
]

DEFAULT_DATA_BASE_DIRNAME = "expert-data"

# 包名可能含文件系统不安全字符（如 ``/``）。非 ``[A-Za-z0-9._-]`` 连续段折叠成 ``_``。
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def resolve_data_base_dir(data_base_dir: Path | str | None = None) -> Path:
    """返回专家数据目录的基目录；不创建。"""
    if data_base_dir is not None:
        return Path(data_base_dir)
    return Path.home() / ".prismclaw" / DEFAULT_DATA_BASE_DIRNAME


def get_expert_data_dir(data_base_dir: Path | str | None, expert_name: str) -> Path:
    """返回某个专家包的持久数据目录；不创建。

    包名被清洗为单段安全名字，保证不会逃逸出基目录。
    """
    sanitized = _UNSAFE_CHARS_RE.sub("_", expert_name).strip("._") or "expert"
    return resolve_data_base_dir(data_base_dir) / sanitized
