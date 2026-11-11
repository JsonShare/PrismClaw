from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_HANDOFF_TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_REQUEST_HANDOFF_TOOL_NAME = "request_handoff"


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
    execution_mode: Literal["sync", "parallel"] = Field(
        default="parallel",
        description=(
            "执行模式："
            "'sync' → 顺序执行；"
            "'parallel' → 与其他子代理并发执行。"
            "默认：'parallel'。"
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
    allowed_tools: list[str] | None = Field(default=None, alias="tools")
    max_turns: int | None = None
    timeout_seconds: float | None = None

    preload_skills: list[str] = Field(default_factory=list)

    can_handoff_to: list[str] | None = None

    system_prompt: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_tools_alias(cls, data: object) -> object:
        if isinstance(data, dict):
            if data.get("allowed_tools") is not None:
                data.pop("tools", None)
        return data

    @field_validator("name")
    @classmethod
    def _validate_name_format(cls, value: str) -> str:
        if not _NAME_PATTERN.match(value):
            raise ValueError(
                f"name must be lowercase letters/digits/hyphens, "
                f"start with a letter, 1-64 chars (got {value!r})"
            )
        return value

    @field_validator("can_handoff_to")
    @classmethod
    def _validate_can_handoff_to(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError(
                "can_handoff_to must be either omitted (None=open) or a non-empty "
                "list of agent names; empty list is ambiguous and not supported"
            )
        seen: set[str] = set()
        for entry in value:
            if not _HANDOFF_TARGET_PATTERN.match(entry):
                raise ValueError(
                    f"can_handoff_to entry {entry!r} must match format "
                    f"^[a-z][a-z0-9-]*$ (lowercase + hyphens, start with letter)"
                )
            if entry in seen:
                raise ValueError(f"can_handoff_to has duplicate entries: {entry!r}")
            seen.add(entry)
        return value

    @model_validator(mode="after")
    def _validate_handoff_tool_consistency(self) -> "AgentManifest":
        if self.can_handoff_to is None:
            return self
        if self.allowed_tools is None:
            return self
        if _REQUEST_HANDOFF_TOOL_NAME not in self.allowed_tools:
            raise ValueError(
                "manifest declares can_handoff_to but allowed_tools excludes "
                "request_handoff; the whitelist would be unreachable"
            )
        return self
