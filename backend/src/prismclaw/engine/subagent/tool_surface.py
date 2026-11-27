"""子代理工具面的唯一定义。

子代理不再通过 manifest 声明工具，也不再有团队/普通两套口径：两者都只拿
``EXPERT_MODE_TOOLS``（不含 ``TEAM_LEAD_TOOLS``，因此不能再派生/控制子代理）。
``restricted=True`` 表示永远按白名单收窄，绝不回退成「全放行」。
"""

from __future__ import annotations

from prismclaw.engine.execution.tools.expert_mode import EXPERT_MODE_TOOLS
from prismclaw.engine.experts.scope import ToolSurface

SUBAGENT_TOOL_SURFACE = ToolSurface(
    active=EXPERT_MODE_TOOLS,
    deferred=EXPERT_MODE_TOOLS,
    member=EXPERT_MODE_TOOLS,
    restricted=True,
)

__all__ = ["SUBAGENT_TOOL_SURFACE"]
