"""
shared/utils/__init__.py - 工具函数库
提供通用的辅助函数
"""

__version__ = "1.0.0"

import os
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime


def ensure_dir(path: str) -> Path:
    """
    确保目录存在，不存在则创建

    Args:
        path: 目录路径

    Returns:
        Path对象
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_file_hash(filepath: str, algorithm: str = "md5") -> str:
    """
    计算文件哈希值

    Args:
        filepath: 文件路径
        algorithm: 哈希算法（md5/sha1/sha256）

    Returns:
        哈希值字符串
    """
    if algorithm == "md5":
        hasher = hashlib.md5()
    elif algorithm == "sha1":
        hasher = hashlib.sha1()
    elif algorithm == "sha256":
        hasher = hashlib.sha256()
    else:
        raise ValueError(f"Unsupported algorithm: {algorithm}")

    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def format_number(
    value: float,
    decimals: int = 2,
    trailing_zeros: bool = False
) -> str:
    """
    格式化数字

    Args:
        value: 数值
        decimals: 小数位数
        trailing_zeros: 是否保留尾部零

    Returns:
        格式化后的字符串
    """
    if trailing_zeros:
        return f"{value:.{decimals}f}"
    else:
        return f"{value:.{decimals}g}"


def format_pvalue(p: float) -> str:
    """
    格式化p值

    Args:
        p: p值

    Returns:
        格式化后的字符串
    """
    if p < 0.001:
        return "p<0.001"
    elif p < 0.01:
        return f"p={format_number(p, 3)}"
    else:
        return f"p={format_number(p, 2)}"


def format_ci(lower: float, upper: float, decimals: int = 2) -> str:
    """
    格式化置信区间

    Args:
        lower: 下限
        upper: 上限
        decimals: 小数位数

    Returns:
        格式化后的字符串，如 "1.23 - 4.56"
    """
    return f"{format_number(lower, decimals)} - {format_number(upper, decimals)}"


def parse_json_safe(json_str: str, default: Any = None) -> Any:
    """
    安全解析JSON字符串

    Args:
        json_str: JSON字符串
        default: 解析失败时的默认值

    Returns:
        解析后的对象或默认值
    """
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return default


def timestamp_now() -> str:
    """
    获取当前时间戳字符串

    Returns:
        ISO格式时间戳字符串
    """
    return datetime.now().isoformat()


def truncate_string(s: str, max_length: int = 50, suffix: str = "...") -> str:
    """
    截断字符串

    Args:
        s: 原字符串
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的字符串
    """
    if len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix
