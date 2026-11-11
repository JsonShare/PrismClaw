"""Pure text utility functions with zero PrismClaw internal dependencies.

Moved from engine.services.chat_assistant_service to break
delivery → engine dependency (G2).
"""
from __future__ import annotations


def _looks_like_chinese_text(text: str) -> bool:
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def _parse_json_string_list(raw: str, *, max_items: int) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    payload = text[start : end + 1]
    try:
        import json
        data = json.loads(payload)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                out.append(cleaned)
        if len(out) >= max_items:
            break
    return out
