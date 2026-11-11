"""域模块配置。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainSettings:
    """域模块配置 — 通过 settings.domains 访问。

    域启用由 scripts/compile/domains.yaml 统一管理（编译时裁剪），
    运行时自动加载所有已安装的域包，无需手动配置启用列表。
    """
    enabled: list[str] = field(default_factory=list)
    """启用的域模块列表。空列表 = 加载所有已安装的域（推荐）。"""
