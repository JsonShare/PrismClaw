"""PrismClaw 沙箱档位定义。

沙箱隔离永远存在，不允许"无沙箱"概念。
"""
from __future__ import annotations

from enum import Enum


# ── 沙箱档位（隔离永远存在，不允许"无沙箱"概念）───────────────────


class SandboxTier(str, Enum):
    """容器隔离档位。

    每个工具调用都运行在容器内，档位决定文件系统/网络/进程命名空间的隔离强度。
    旧的 ``NoSandboxPolicy`` / ``SandboxSettings.enforce=false`` 不再存在。
    """

    STANDARD = "standard"
    """allowWrite=workspace; allowRead=workspace+$USER_SAFE; allowNet=常用域名白名单"""

    TIGHT = "tight"
    """+ denyRead 敏感用户目录; + denyNet *.corp 内网域名"""

    STRICT = "strict"
    """allowRead=仅 workspace; allowNet=[]; denyPidNS=on — 医疗 PHI / 法律案件"""
