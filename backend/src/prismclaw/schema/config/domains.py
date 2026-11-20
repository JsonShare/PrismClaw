"""域模块配置。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainSettings:
    """域模块配置 — 通过 settings.domains 访问。

    域通过 entry_points(group="prismclaw.domains") 钩子注册，安装即生效。
    运行期可用此处 ``enabled`` 对已安装的域做可选裁剪。
    """
    enabled: list[str] = field(default_factory=list)
    """启用的域模块列表。空列表 = 加载所有已安装的域（推荐）。"""
