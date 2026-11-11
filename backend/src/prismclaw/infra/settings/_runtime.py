"""Global runtime root configuration.

``server.yaml`` 里的 ``runtime`` 段——定义技能/插件共享的全局运行时根。
所有用户共用，node_modules、pip --target 包、fonts 等重资产存这里，
一次安装全 workspace 生效。
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """全局运行时根配置。"""

    default: str = Field(
        default="~/.prismclaw/runtime",
        description="全局共享运行时根（node_modules / pip --target / fonts 等重资产目录）。"
        "支持 ~ 展开；解析时由 runtime_root.resolve_runtime_root() 接管。",
    )
    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")
