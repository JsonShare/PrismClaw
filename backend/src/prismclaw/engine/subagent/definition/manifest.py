from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=1024)

    # -- New fields (Phase 2) --
    context_mode: Literal["inherit", "isolated"] = Field(
        default="isolated",
        description=(
            "上下文模式："
            "'inherit' → 继承父级上下文（可共享 Prompt Cache）；"
            "'isolated' → 零上下文（全新代理）"
        ),
    )
    # -- Prompt Cache sharing configuration --
    max_inherit_messages: int = Field(
        default=20,
        ge=1,
        le=100,
        description=(
            "最多可继承的父会话消息条数"
            "（仅在 context_mode='inherit' 时生效）。"
            "在保留近期上下文的同时限制 token 用量。"
            "默认：20，范围：[1, 100]"
        ),
    )

    # -- Existing fields (unchanged) --
    model: str | None = None
    max_turns: int | None = Field(default=None, alias="maxTurns")
    timeout_seconds: float | None = None

    preload_skills: list[str] = Field(default_factory=list, alias="skills")

    system_prompt: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name_format(cls, value: str) -> str:
        if not _NAME_PATTERN.match(value):
            raise ValueError(
                f"name must be lowercase letters/digits/hyphens, "
                f"start with a letter, 1-64 chars (got {value!r})"
            )
        return value
