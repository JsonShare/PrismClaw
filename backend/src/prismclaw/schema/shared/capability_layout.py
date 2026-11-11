"""项目级能力布局（skills / plugins / agents）的单一事实源。

本模块是 L0 纯值对象：只依赖 pathlib / enum / dataclasses，不 import 任何
业务层（infra / engine / assembly / runtime）。它回答三个问题：

1. 项目根是怎么定位到的（``ResolvedRoot.source`` / ``confirmed``）；
2. 三类项目级能力在磁盘上的实际目录在哪、是否存在（``CapabilityLocation``）；
3. 布局展开过程中产生了哪些可观测信号（``LayoutDiagnostic``）。

布局的"计算"（读 settings 契约 + 解析根 + 展开相对目录）发生在 ``infra/paths``，
三个发现器（skills / plugins / agents）只消费本对象给出的 location，不再各自
拼接 ``root / 相对目录``，从而消除三处重复的 root 去重 / 拼接 / exists 判定。

注意：本对象只表达 **project 级**能力。workspace 级（previews/edits/spills 等）
与 user 级（~/.prismclaw/assets）路径不经过这里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RootSource(str, Enum):
    """项目根的定位来源，可信度依次递减。"""

    ENV = "env"
    """``PRISMCLAW_ROOT`` 显式注入（离线/云端部署）。"""

    MARKER = "marker"
    """从 hint 或 cwd 向上命中源码树标记而锚定。"""

    CWD_FALLBACK = "cwd_fallback"
    """未命中任何标记，兜底信任当前工作目录（未经锚定，可能定位错）。"""


@dataclass(frozen=True, slots=True)
class ResolvedRoot:
    """一个已解析的项目根及其可信度。"""

    path: Path
    source: RootSource

    @property
    def confirmed(self) -> bool:
        """根是否被显式锚定（env 或 marker）。cwd 兜底为 False。"""
        return self.source is not RootSource.CWD_FALLBACK


class CapabilityKind(str, Enum):
    """三类项目级能力。value 同时用于诊断与日志标识。"""

    SKILLS = "skills"
    PLUGINS = "plugins"
    AGENTS = "agents"


class DiagnosticLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LayoutDiagnostic:
    """布局展开阶段产生的一条可观测信号。"""

    level: DiagnosticLevel
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class CapabilityLocation:
    """某一类能力在某个 root 下展开出的目录。"""

    kind: CapabilityKind
    root: ResolvedRoot
    path: Path
    exists: bool


@dataclass(frozen=True, slots=True)
class ProjectCapabilityLayout:
    """一次完整的项目级能力布局展开结果。"""

    roots: tuple[ResolvedRoot, ...]
    locations: dict[CapabilityKind, tuple[CapabilityLocation, ...]] = field(
        default_factory=dict
    )
    diagnostics: tuple[LayoutDiagnostic, ...] = ()

    def locations_of(self, kind: CapabilityKind) -> tuple[CapabilityLocation, ...]:
        """该类能力的全部展开目录（含不存在的——供诊断）。"""
        return self.locations.get(kind, ())

    def existing_locations_of(self, kind: CapabilityKind) -> tuple[Path, ...]:
        """该类能力实际存在的目录路径（发现器只应扫描这些）。"""
        return tuple(loc.path for loc in self.locations.get(kind, ()) if loc.exists)

    def missing_locations_of(self, kind: CapabilityKind) -> tuple[Path, ...]:
        """该类能力配置了但磁盘上不存在的目录。"""
        return tuple(loc.path for loc in self.locations.get(kind, ()) if not loc.exists)

    def has_capability(self, kind: CapabilityKind) -> bool:
        """该类能力是否至少有一个存在的目录。"""
        return any(loc.exists for loc in self.locations.get(kind, ()))

    @property
    def errors(self) -> tuple[LayoutDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.level is DiagnosticLevel.ERROR)

    @property
    def warnings(self) -> tuple[LayoutDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.level is DiagnosticLevel.WARN)

    @property
    def has_error(self) -> bool:
        return any(d.level is DiagnosticLevel.ERROR for d in self.diagnostics)

    def summary_dict(self) -> dict:
        """JSON 安全的布局快照：根来源、三类已存在/缺失目录、诊断列表。

        供启动聚合日志与 ``/api/health`` 共用，避免在展示层重复遍历 location。
        """
        def kind_block(kind: CapabilityKind) -> dict:
            locs = self.locations.get(kind, ())
            return {
                "existing": [str(loc.path) for loc in locs if loc.exists],
                "missing": [str(loc.path) for loc in locs if not loc.exists],
            }

        return {
            "roots": [
                {
                    "path": str(root.path),
                    "source": root.source.value,
                    "confirmed": root.confirmed,
                }
                for root in self.roots
            ],
            "skills": kind_block(CapabilityKind.SKILLS),
            "plugins": kind_block(CapabilityKind.PLUGINS),
            "agents": kind_block(CapabilityKind.AGENTS),
            "diagnostics": [
                {
                    "level": d.level.value,
                    "code": d.code,
                    "message": d.message,
                }
                for d in self.diagnostics
            ],
        }


__all__ = [
    "RootSource",
    "ResolvedRoot",
    "CapabilityKind",
    "CapabilityLocation",
    "DiagnosticLevel",
    "LayoutDiagnostic",
    "ProjectCapabilityLayout",
]
