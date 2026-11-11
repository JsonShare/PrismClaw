"""解析聊天输入框正文里的内联卡片标记。

输入框把技能 / 专家 / 引用文件做成正文里的原子卡片，序列化成
``<|kind|percent-encoded-value|>`` 随 content 一起发送。前端不再单独发
designated_skills / designated_plugins / referenced_files，正文是唯一真相源；
本模块在构造 RunRequest 前把这些标记拆出来，并把正文还原成模型可读的
``@标题`` 文本（路径信息仍通过 referenced_files 注入）。
"""

from __future__ import annotations

import re
from urllib.parse import unquote

_MARKER_RE = re.compile(r"<\|(skill|plugin|file)\|([^|<>]*)\|>")


def _decode(raw: str) -> str:
    try:
        return unquote(raw)
    except Exception:  # noqa: BLE001 - 畸形的百分号编码按原样保留
        return raw


def _file_label(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name or path


def extract_mentions(text: str) -> tuple[str, list[str], list[str], list[str]]:
    """返回 (模型可读正文, 技能名列表, 专家名列表, 引用文件路径列表)。

    技能/专家按出现顺序去重（可一次激活多个）；文件路径去重但保持出现顺序。
    没有标记时原样返回，零开销。
    """
    if "<|" not in text:
        return text, [], [], []

    skills: list[str] = []
    plugins: list[str] = []
    files: list[str] = []
    seen_files: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        value = _decode(match.group(2))
        if kind == "skill":
            if value and value not in skills:
                skills.append(value)
            return f"@{value}"
        if kind == "plugin":
            if value and value not in plugins:
                plugins.append(value)
            return f"@{value}"
        if value and value not in seen_files:
            seen_files.add(value)
            files.append(value)
        return f"@{_file_label(value)}"

    readable = _MARKER_RE.sub(_replace, text)
    return readable, skills, plugins, files
