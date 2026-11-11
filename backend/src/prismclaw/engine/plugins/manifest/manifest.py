"""插件 Manifest Schema。

对标参考实现 ``schemas.ts`` L1340 ``PluginManifestSchema``，做减法。

抄的字段：metadata、hooks、commands、agents、skills、
mcp_servers、user_config、dependencies。
不抄的字段：channels、lsp_servers、keywords、settings、output_styles。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prismclaw.engine.plugins.registry.identity import is_valid_plugin_name


class PluginAuthor(BaseModel):
    """对标参考实现 ``PluginAuthorSchema``。"""

    name: str | None = None
    email: str | None = None
    url: str | None = None

    model_config = ConfigDict(extra="ignore")


class UserConfigOption(BaseModel):
    """用户可配置选项。参考实现 ``PluginUserConfigOptionSchema`` (L560)。

    sensitive=True 时存 keychain，非 sensitive 存 settings.json。
    值通过 ``${user_config.KEY}`` 在 hook command 中引用。
    """

    type: Literal["string", "number", "boolean", "directory", "file"] = "string"
    title: str = ""
    description: str = ""
    required: bool = False
    default: str | int | bool | list[str] | None = None
    multiple: bool = False
    sensitive: bool = False
    min: float | None = None
    max: float | None = None

    model_config = ConfigDict(extra="ignore")


class CommandMetadata(BaseModel):
    """命令元数据。参考实现 ``CommandMetadataSchema`` (L430)。

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
    def _check_exclusive(self) -> "CommandMetadata":
        if self.source is not None and self.content is not None:
            raise ValueError("Command 'source' and 'content' are mutually exclusive")
        return self


class PluginManifest(BaseModel):
    """插件 manifest。对标参考实现 ``PluginManifestSchema`` (L1340)。

    commands 支持三种格式（对标参考实现 L1410）：
    1. 单路径字符串: ``"./commands"``
    2. 路径数组: ``["./cmd1", "./cmd2"]``
    3. 对象映射: ``{"slug": {"source": "./x.md", "description": "..."}}``
    """

    name: str
    version: str | None = None
    description: str | None = None
    author: PluginAuthor | None = None
    dependencies: list[str] | None = None  # "name" 或 "name@marketplace"

    hooks: str | dict[str, Any] | list[Any] | None = None
    commands: str | list[str] | dict[str, CommandMetadata] | None = None
    agents: str | list[str] | None = None
    skills: str | list[str] | None = None
    mcp_servers: str | dict[str, Any] | list[Any] | None = Field(default=None, alias="mcpServers")
    user_config: dict[str, UserConfigOption] | None = Field(default=None, alias="userConfig")
    porting: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="after")
    def _validate_name(self) -> "PluginManifest":
        if not is_valid_plugin_name(self.name):
            raise ValueError(f"Invalid plugin name: {self.name!r}")
        return self


def parse_manifest(raw: dict[str, Any], *, fallback_name: str | None = None) -> PluginManifest:
    """从原始字典解析 manifest，允许 fallback_name 覆盖。

    raw dict 在加载入口立即解析为 pydantic 模型，后续全程操作 model 对象。
    """
    data = dict(raw)
    if fallback_name and "name" not in data:
        data["name"] = fallback_name
    return PluginManifest.model_validate(data)
