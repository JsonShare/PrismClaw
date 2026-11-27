"""专家包身份模型。

ExpertID = "{name}@experts"：唯一来源为专家包目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ExpertID / 专家包名的合法字符集正则
EXPERT_ID_PATTERN = re.compile(r"^[a-z0-9][-a-z0-9._]*@[a-z0-9][-a-z0-9._]*$", re.IGNORECASE)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

EXPERT_SOURCE = "experts"

VALID_SOURCES = frozenset({EXPERT_SOURCE})


def parse_expert_identifier(expert_id: str) -> tuple[str, str]:
    """解析 ``name@source`` 格式的标识。缺省 source 视为 ``experts``。"""
    if "@" not in expert_id:
        return expert_id, EXPERT_SOURCE
    name, source = expert_id.rsplit("@", 1)
    return name, source


def make_expert_id(name: str, source: str) -> str:
    """组装 ``name@source``。"""
    return f"{name}@{source}"


def is_valid_expert_id(expert_id: str) -> bool:
    return bool(EXPERT_ID_PATTERN.match(expert_id))


def is_valid_expert_name(name: str) -> bool:
    return bool(NAME_PATTERN.match(name))


@dataclass(frozen=True, slots=True)
class ExpertID:
    """专家包唯一标识。"""

    name: str
    source: str

    def __post_init__(self) -> None:
        if not is_valid_expert_name(self.name):
            raise ValueError(f"Invalid expert name: {self.name!r}")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"Invalid expert source: {self.source!r}")

    @classmethod
    def parse(cls, expert_id: str) -> ExpertID:
        name, source = parse_expert_identifier(expert_id)
        return cls(name=name, source=source)

    def __str__(self) -> str:
        return make_expert_id(self.name, self.source)

    @property
    def canonical(self) -> str:
        """规范化字符串表示 ``name@source``。等同 ``str(expert_id)``。"""
        return make_expert_id(self.name, self.source)
