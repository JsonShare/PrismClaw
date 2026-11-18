"""模型原生工具调用标记（DSML）的纯文本剥离。

部分推理模型（DeepSeek V3.1 系列）在端点未启用 tool-call parser 时，会把
原生工具调用协议以**纯文本**写进 ``content``。

⚠️ 线上实证（2026-11-18，Redis 全部含标记的会话条目统计）：真实形态是

    <｜DSML｜calls>
    <｜DSML｜invoke name="bash">
    <｜DSML｜parameter name="command" string="true">cd /tmp && python3 ...</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜calls>

三个必须同时满足的细节，缺一即漏：

1. 竖线是**全角** U+FF5C ``｜``，不是 ASCII ``|``。9/9 样本无一使用 ASCII。
2. 块名是 ``calls``（22 开 / 14 闭），不是 ``tool_calls``。
3. 标记可能带空格：``< | DSML | calls >``（另一条平台链路观察到）。

早期实现只认 ``<|DSML|tool_calls>``（ASCII + tool_calls），对上述真实数据
``has_tool_markup`` 返回 False、``strip_tool_markup`` 原样放行——兜底逻辑写了
但从未生效。本模块的回归测试直接内嵌线上样本，不得再用自造样本自证。

提供三个出口：

    - ``strip_tool_markup``  —— 一次性剥离（落库、历史回放、非流式出口）
    - ``has_tool_markup``    —— 判断文本里是否存在标记
    - ``MarkupStreamFilter`` —— 流式过滤器，吞掉被分片切断的标记

设计约束：与 ``path_sanitizer`` 一致，纯文本变换，不触碰执行引擎内部状态。
"""

from __future__ import annotations

import re

__all__ = [
    "strip_tool_markup",
    "has_tool_markup",
    "MarkupStreamFilter",
]

# 竖线：ASCII 与全角 U+FF5C 都要认（线上真实数据是全角）。
_BAR = r"[|\uff5c]"
# 分隔符两侧允许空白：``< | DSML | calls >``。
_GAP = r"\s*"
_TAGNAME = r"[A-Za-z_][A-Za-z0-9_]*"
# 块名：线上是 calls，部分端点写 tool_calls，两者都认。
_CALLS = r"(?:tool_)?calls"

_OPEN_CALLS_RE = re.compile(
    rf"<{_GAP}{_BAR}{_GAP}DSML{_GAP}{_BAR}{_GAP}{_CALLS}{_GAP}>",
    re.IGNORECASE,
)
_CLOSE_CALLS_RE = re.compile(
    rf"<{_GAP}/{_GAP}{_BAR}{_GAP}DSML{_GAP}{_BAR}{_GAP}{_CALLS}{_GAP}>",
    re.IGNORECASE,
)

# 完整块：开标签到闭标签整段删除（内含的脚本/参数一并消失）。
# 未闭合时吃到文本结尾——半截块同样是残留，不能展示。
_TOOL_CALLS_BLOCK_RE = re.compile(
    _OPEN_CALLS_RE.pattern + r".*?(?:" + _CLOSE_CALLS_RE.pattern + r"|\Z)",
    re.DOTALL | re.IGNORECASE,
)

# 单标记（含闭合形式）：``<｜DSML｜invoke ...>``、``</｜DSML｜parameter>``。
# 同时兼容 ``<|tool_call|>`` 这类单名形式，避免只能修 DSML。
_MARKUP_TAG_RE = re.compile(
    rf"<{_GAP}/{_GAP}{_BAR}{_GAP}{_TAGNAME}{_GAP}"
    rf"(?:{_BAR}{_GAP})?[^>]*>",
    re.IGNORECASE,
)

_BAR_CHARS = frozenset("|\uff5c")


def has_tool_markup(text: str) -> bool:
    """文本中是否存在原生工具调用标记。"""
    raw = text or ""
    if not raw:
        return False
    return bool(_TOOL_CALLS_BLOCK_RE.search(raw) or _MARKUP_TAG_RE.search(raw))


def strip_tool_markup(text: str) -> str:
    """剥离原生工具调用标记（先删整块，再删残留单标记）。"""
    if not text:
        return ""
    out = _TOOL_CALLS_BLOCK_RE.sub("", text)
    out = _MARKUP_TAG_RE.sub("", out)
    return out


# 流式滞留上限：未闭合标记异常长时放弃滞留，避免正文被无限延迟。
_MAX_MARKUP_HOLDBACK = 256


class MarkupStreamFilter:
    """有状态的流式过滤器：吞掉被分片切断的原生工具调用标记。

    ``feed`` 的返回值已不含标记，但**可能仍含被切断的路径**，通常再串一个
    :class:`~prismclaw.schema.shared.path_sanitizer.StreamingPathRedactor`。
    """

    __slots__ = ("_buffer", "_dropping")

    def __init__(self) -> None:
        self._buffer = ""
        # True 表示已进入未闭合的 calls 块，后续内容整段吞掉直到闭合。
        self._dropping = False

    def feed(self, text: str) -> str:
        if text:
            self._buffer = f"{self._buffer}{text}"
        out: list[str] = []
        while self._buffer:
            if self._dropping:
                match = _CLOSE_CALLS_RE.search(self._buffer)
                if match is None:
                    # 块内所有内容都属工具调用参数，直接丢弃；但保留可能被
                    # 切断的闭标签前缀，否则闭标签分片跨边界时永远等不到。
                    if len(self._buffer) > _MAX_MARKUP_HOLDBACK:
                        idx = self._buffer.rfind("<")
                        self._buffer = self._buffer[idx:] if idx >= 0 else ""
                    return "".join(out)
                self._buffer = self._buffer[match.end() :]
                self._dropping = False
                continue

            opening = _OPEN_CALLS_RE.search(self._buffer)
            if opening is not None:
                out.append(_MARKUP_TAG_RE.sub("", self._buffer[: opening.start()]))
                self._buffer = self._buffer[opening.end() :]
                self._dropping = True
                continue

            # 无整块标记：按单标记剥离，并滞留未闭合的标记前缀。
            cut = self._holdback_start(self._buffer)
            out.append(_MARKUP_TAG_RE.sub("", self._buffer[:cut]))
            self._buffer = self._buffer[cut:]
            break
        return "".join(out)

    def flush(self) -> str:
        """流结束：吐出剩余文本（剥掉残留标记；未闭合块整段丢弃）。"""
        pending, self._buffer = self._buffer, ""
        if self._dropping:
            # 块始终未闭合：其后内容都属于工具调用残留。
            self._dropping = False
            return ""
        return _MARKUP_TAG_RE.sub("", pending)

    @staticmethod
    def _holdback_start(buffer: str) -> int:
        """返回可安全输出的切点（其后为未闭合的标记前缀，需滞留）。

        只有当尾部确实可能是 DSML 标记前缀时才滞留：必须有竖线（``|``/``｜``）
        或仅为孤立的 ``<`` / ``</``。这样普通文本里的 ``<``（如 ``a < b``）与
        HTML/XML 片段 ``<b>`` 都不会被长期扣住。
        """
        idx = buffer.rfind("<")
        if idx < 0:
            return len(buffer)
        tail = buffer[idx:]
        if ">" in tail:
            return len(buffer)
        if tail in ("<", "< ", "</", "</ "):
            return idx
        if _BAR_CHARS & set(tail):
            return idx
        return len(buffer)
