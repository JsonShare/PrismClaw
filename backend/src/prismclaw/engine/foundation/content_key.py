"""内容寻址键（foundation 通用工具）。

对基础类型载荷做稳定 sha256：字典排序、紧凑分隔符、default=str
兜底不可序列化对象。同一逻辑载荷任何顺序都得到同一键。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def content_key(*, session_id: str, payload: dict[str, Any]) -> str:
    """返回 ``{session_id}:{sha256[:32]}`` 形式的稳定内容键。"""
    normalized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{session_id}:{digest[:32]}"