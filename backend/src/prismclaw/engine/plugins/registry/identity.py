"""插件身份模型。

对标参考实现 ``pluginIdentifier.ts`` + ``schemas.ts`` L1430 ``PluginIdSchema``。

PluginID = "{name}@{source}"，source ∈ builtin | marketplace | session | local
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

# 对标参考实现 PluginIdSchema 正则
PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][-a-z0-9._]*@[a-z0-9][-a-z0-9._]*$", re.IGNORECASE)
NAME_PATTERN = re.compile(r"^[a-z0-9][-a-z0-9._]*$", re.IGNORECASE)

BUILTIN_SOURCE = "builtin"
MARKETPLACE_SOURCE = "marketplace"
SESSION_SOURCE = "session"
LOCAL_SOURCE = "local"

VALID_SOURCES = frozenset({BUILTIN_SOURCE, MARKETPLACE_SOURCE, SESSION_SOURCE, LOCAL_SOURCE})


def parse_plugin_identifier(plugin_id: str) -> tuple[str, str]:
    """解析 ``name@source`` 格式。

    参考实现 ``parsePluginIdentifier``。
    缺省 source 视为 ``builtin``。
    """
    if "@" not in plugin_id:
        return plugin_id, BUILTIN_SOURCE
    name, source = plugin_id.rsplit("@", 1)
    return name, source


def make_plugin_id(name: str, source: str) -> str:
    """组装 ``name@source``。"""
    return f"{name}@{source}"


def is_valid_plugin_id(plugin_id: str) -> bool:
    return bool(PLUGIN_ID_PATTERN.match(plugin_id))


def is_valid_plugin_name(name: str) -> bool:
    return bool(NAME_PATTERN.match(name))


@total_ordering
@dataclass(frozen=True, slots=True)
class PluginID:
    """插件唯一标识。对标参考实现 ``PluginIdSchema``。"""

    name: str
    source: str

    def __post_init__(self) -> None:
        if not is_valid_plugin_name(self.name):
            raise ValueError(f"Invalid plugin name: {self.name!r}")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"Invalid plugin source: {self.source!r}")

    @classmethod
    def parse(cls, plugin_id: str) -> "PluginID":
        name, source = parse_plugin_identifier(plugin_id)
        return cls(name=name, source=source)

    def __str__(self) -> str:
        return make_plugin_id(self.name, self.source)

    @property
    def canonical(self) -> str:
        """规范化字符串表示 ``name@source``。等同 ``str(plugin_id)``。"""
        return make_plugin_id(self.name, self.source)

    @property
    def priority_rank(self) -> int:
        """优先级排序值：session > marketplace > local > builtin。

        数值越大优先级越高，对标参考实现 ``mergePluginSources`` 的
        ``session > marketplace > builtin`` 顺序。
        """
        return _SOURCE_PRIORITY.get(self.source, 0)

    def __lt__(self, other: "PluginID") -> bool:
        if not isinstance(other, PluginID):
            return NotImplemented
        return (self.priority_rank, self.name, self.source) < (
            other.priority_rank,
            other.name,
            other.source,
        )


_SOURCE_PRIORITY: dict[str, int] = {
    BUILTIN_SOURCE: 0,
    LOCAL_SOURCE: 1,
    MARKETPLACE_SOURCE: 2,
    SESSION_SOURCE: 3,
}
