"""专家包 Manifest 的不透明解析模型。

本模型只负责把 ``.plugin/plugin.json`` 解析成对象，字段是否存在由包级校验
``validate_expert_package`` 决定：``hooks``、``commands``、``mcp`` 等禁项在该层
显式拒绝，不在此处做语义解释。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prismclaw.engine.experts.registry.identity import is_valid_expert_name


class ExpertAuthor(BaseModel):
    """专家包作者信息。"""

    name: str | None = None
    email: str | None = None
    url: str | None = None

    model_config = ConfigDict(extra="ignore")


class CommandMetadata(BaseModel):
    """命令元数据。

    source 和 content 互斥。
    """

    source: str | None = None
    content: str | None = None
    description: str | None = None
    argument_hint: str | None = Field(default=None, alias="argumentHint")
    model: str | None = None
    allowed_tools: list[str] | None = Field(default=None, alias="allowedTools")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="after")
    def _check_exclusive(self) -> CommandMetadata:
        if self.source is not None and self.content is not None:
            raise ValueError("Command 'source' and 'content' are mutually exclusive")
        return self


class ExpertManifest(BaseModel):
    """专家包 manifest。

    commands 支持三种格式：
    1. 单路径字符串: ``"./commands"``
    2. 路径数组: ``["./cmd1", "./cmd2"]``
    3. 对象映射: ``{"slug": {"source": "./x.md", "description": "..."}}``
    """

    schema_version: int = Field(
        default=1,
        description="Manifest schema version. Only 1 accepted for now.",
        alias="schemaVersion",
    )
    name: str
    version: str | None = None
    description: str | None = None
    author: ExpertAuthor | None = None
    dependencies: list[str] | None = None  # 非空字符串形式的包名列表
    expert_type: Literal["agent", "team"] | None = Field(default=None, alias="expertType")
    agent_name: str | None = Field(default=None, alias="agentName")
    # 展示分组。完整校验由 ExpertPackageManifest 负责（含必填约束），
    # 此处仅保留原值供展示层读取；缺失时为 None，不阻断加载。
    category_id: str | None = Field(default=None, alias="categoryId")
    category_name: str | None = Field(default=None, alias="categoryName")

    hooks: str | dict[str, Any] | list[Any] | None = None
    commands: str | list[str] | dict[str, CommandMetadata] | None = None
    agents: str | list[str] | None = None
    skills: str | list[str] | None = None
    porting: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="after")
    def _validate(self) -> ExpertManifest:
        if self.schema_version != 1:
            raise ValueError(f"unsupported schema_version={self.schema_version}, expected 1")
        if not is_valid_expert_name(self.name):
            raise ValueError(f"Invalid expert name: {self.name!r}")
        return self


def parse_manifest(raw: dict[str, Any], *, fallback_name: str | None = None) -> ExpertManifest:
    """从原始字典解析 manifest，允许 fallback_name 覆盖。

    raw dict 在加载入口立即解析为 pydantic 模型，后续全程操作 model 对象。
    """
    data = dict(raw)
    if fallback_name and "name" not in data:
        data["name"] = fallback_name
    return ExpertManifest.model_validate(data)
