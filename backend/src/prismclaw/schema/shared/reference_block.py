"""回答末尾的「参考来源」结构化块。

模型不再在正文里罗列来源清单，而是在回答结尾输出一个机器可读块：

    <!--references
    [{"title": "...", "publisher": "...", "year": "2024",
      "url": "https://...", "kind": "guideline"}]
    -->

HTML 注释形式有两个好处：Markdown 渲染器天然隐藏它；被分片切断时也能
用同一套「未闭合即滞留」的流式过滤处理。提供三个出口：

    - ``parse_reference_block``   —— 解析出结构化来源并返回剥离后的正文
    - ``strip_reference_block``   —— 仅剥离（历史回放、非 Web 通道）
    - ``ReferenceBlockStreamFilter`` —— 流式过滤器，不让块出现在实时正文里
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

__all__ = [
    "ReferenceSource",
    "ReferenceBlockStreamFilter",
    "normalize_reference_sources",
    "parse_reference_block",
    "strip_reference_block",
]

# 允许的来源类型：前端按类型上色/加标签，未知值统一落到 web。
REFERENCE_KINDS = frozenset(
    {"guideline", "paper", "drug", "database", "news", "web", "book", "report"}
)

MAX_SOURCES = 12

_OPEN_MARKER = "<!--references"
_OPEN_RE = re.compile(r"<!--\s*references\b", re.IGNORECASE)
_CLOSE_MARKER = "-->"


class ReferenceSource(BaseModel):
    """一条参考来源。仅 url 或 title 至少有其一。"""

    title: str = ""
    url: str | None = None
    publisher: str | None = None
    year: str | None = None
    kind: str = "web"


def _clean_str(value: object, *, limit: int = 300) -> str | None:
    # 模型常把 year 写成数字（2024）而非字符串，这里统一成 str。
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(int(value)) if float(value).is_integer() else str(value)
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:limit]


def _clean_url(value: object) -> str | None:
    url = _clean_str(value)
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _coerce_source(raw: object) -> ReferenceSource | None:
    if isinstance(raw, ReferenceSource):
        title = raw.title.strip()
        url = _clean_url(raw.url)
        if not title and not url:
            return None
        kind = (raw.kind or "web").lower()
        return ReferenceSource(
            title=title or (url or ""),
            url=url,
            publisher=raw.publisher,
            year=raw.year,
            kind=kind if kind in REFERENCE_KINDS else "web",
        )
    if isinstance(raw, str):
        url = _clean_url(raw)
        if not url:
            return None
        return ReferenceSource(url=url, title=url, kind="web")
    if not isinstance(raw, dict):
        return None
    title = _clean_str(raw.get("title"))
    url = _clean_url(raw.get("url"))
    if not title and not url:
        return None
    kind = (_clean_str(raw.get("kind"), limit=32) or "web").lower()
    if kind not in REFERENCE_KINDS:
        kind = "web"
    return ReferenceSource(
        title=title or (url or ""),
        url=url,
        publisher=_clean_str(raw.get("publisher"), limit=80),
        year=_clean_str(raw.get("year"), limit=16),
        kind=kind,
    )


def normalize_reference_sources(raw_items: object) -> list[ReferenceSource]:
    """把任意历史/回退形态（字符串 URL、dict、模型对象）归一成结构化来源。"""
    if not isinstance(raw_items, list):
        return []
    out: list[ReferenceSource] = []
    seen: set[str] = set()
    for raw in raw_items:
        source = _coerce_source(raw)
        if source is None:
            continue
        key = (source.url or source.title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
        if len(out) >= MAX_SOURCES:
            break
    return out


def _iter_json_arrays(text: str) -> list[str]:
    """扫出文本里所有括号配平的 JSON 数组片段（容忍前后夹带说明文字）。"""
    arrays: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                arrays.append(text[start : i + 1])
                start = -1
    return arrays


def _parse_payload(inner: str) -> list[ReferenceSource]:
    text = inner.strip()
    if not text:
        return []
    # 容错：模型偶尔写成 ```json ... ```、带尾随逗号，或块内夹了说明文字。
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # 多个来源块被拼在一起、或块内夹了说明文字：逐个数组单独解析。
        merged: list[Any] = []
        for chunk in _iter_json_arrays(text):
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            merged.extend(parsed if isinstance(parsed, list) else [parsed])
        if not merged:
            return []
        payload = merged
    if isinstance(payload, dict):
        payload = payload.get("sources") or payload.get("references") or []
    return normalize_reference_sources(payload)


def parse_reference_block(text: str) -> tuple[str, list[ReferenceSource]]:
    """返回 (剥离块后的正文, 结构化来源)。块可出现在任意位置。"""
    raw = text or ""
    if not raw:
        return "", []

    out: list[str] = []
    sources: list[ReferenceSource] = []
    cursor = 0
    while True:
        opener = _OPEN_RE.search(raw, cursor)
        if opener is None:
            out.append(raw[cursor:])
            break
        out.append(raw[cursor : opener.start()])
        close = raw.find(_CLOSE_MARKER, opener.end())
        if close < 0:
            # 未闭合：其后全部视为块内容（模型截断时不得把 JSON 漏给用户）。
            sources.extend(_parse_payload(raw[opener.end() :]))
            cursor = len(raw)
            break
        sources.extend(_parse_payload(raw[opener.end() : close]))
        cursor = close + len(_CLOSE_MARKER)

    cleaned = "".join(out)
    # 块通常独占末尾几行，剥完留下多余空行会让气泡底部发空。
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, normalize_reference_sources(sources)


def strip_reference_block(text: str) -> str:
    cleaned, _ = parse_reference_block(text)
    return cleaned


# 未闭合块异常长时放弃滞留，避免正文被无限延迟。
_MAX_HOLDBACK = 4096


def _holdback_start(buffer: str) -> int:
    """返回可安全输出的切点（其后可能是未闭合的块起始标记）。"""
    idx = buffer.rfind("<")
    if idx < 0:
        return len(buffer)
    tail = buffer[idx:]
    if len(tail) > len(_OPEN_MARKER) + 8:
        return len(buffer)
    normalized = re.sub(r"\s+", "", tail).lower()
    if normalized and _OPEN_MARKER.startswith(normalized):
        return idx
    return len(buffer)


class ReferenceBlockStreamFilter:
    """有状态的流式过滤器：吞掉参考来源块，不推进到实时正文。"""

    __slots__ = ("_buffer", "_dropping", "_captured")

    def __init__(self) -> None:
        self._buffer = ""
        self._dropping = False
        # 被吞掉的块内文本：流结束后由 take_captured_sources 一次性解析。
        self._captured = ""

    def feed(self, text: str) -> str:
        if text:
            self._buffer = f"{self._buffer}{text}"
        out: list[str] = []
        while self._buffer:
            if self._dropping:
                close = self._buffer.find(_CLOSE_MARKER)
                if close < 0:
                    # 保留末尾可能是 "-->" 前缀的 2 个字符，其余全部计入块内容。
                    keep = min(len(self._buffer), len(_CLOSE_MARKER) - 1)
                    if keep:
                        self._captured += self._buffer[:-keep]
                        self._buffer = self._buffer[-keep:]
                    else:
                        self._captured += self._buffer
                        self._buffer = ""
                    if len(self._captured) > _MAX_HOLDBACK * 8:
                        self._captured = self._captured[-_MAX_HOLDBACK :]
                    return "".join(out)
                self._captured += self._buffer[:close]
                self._buffer = self._buffer[close + len(_CLOSE_MARKER) :]
                self._dropping = False
                continue

            opener = _OPEN_RE.search(self._buffer)
            if opener is not None:
                out.append(self._buffer[: opener.start()])
                self._buffer = self._buffer[opener.end() :]
                self._dropping = True
                continue

            cut = _holdback_start(self._buffer)
            out.append(self._buffer[:cut])
            self._buffer = self._buffer[cut:]
            break
        return "".join(out)

    def flush(self) -> str:
        """流结束：未闭合块整段丢弃（内容留给来源解析），其余原样吐出。"""
        pending, self._buffer = self._buffer, ""
        if self._dropping:
            self._captured += pending
            self._dropping = False
            return ""
        return pending

    def take_captured_sources(self) -> list[ReferenceSource]:
        """解析已吞掉的块内容（流结束后调用；可重复调用）。"""
        captured, self._captured = self._captured, ""
        return _parse_payload(captured)
