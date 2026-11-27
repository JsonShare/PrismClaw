from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

JsonScalar = str | int | float | bool | None
if TYPE_CHECKING:
    JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
else:
    JsonValue = JsonScalar | dict | list


class EffectClass(StrEnum):
    """工具执行的效果分类——schema 层定义，被 execution/checkpoint 和 toolkit 共同引用。"""

    PURE_READ = "pure_read"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_MUTATION = "external_mutation"
    UNKNOWN_EFFECT = "unknown_effect"


@dataclass(slots=True)
class ExpertCommandCapability:
    name: str
    description: str
    body: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)
