"""项目级路径解析与能力布局的单一入口。

设计约束（批判性取舍）：
  * SkillSettings / PluginSettings / SubAgentSettings 是路径契约的唯一权威源，
    本模块绝不硬编码 ".prismclaw/skills" 等字符串——相对目录名由调用方从 settings
    字段传入 :func:`build_project_capability_layout`。
  * resolve_project_root() 的分层解析：环境变量 > 源码树标记 > hint 参数 > cwd，
    命中即返回，不做"多候选兼容列表"。
  * :func:`resolve_project_root_detailed` 额外返回根的定位来源与 ``confirmed`` 可信度，
    避免"env 显式锚定"与"cwd 兜底猜测"被一视同仁。
  * :func:`build_project_capability_layout` 是 skills/plugins/agents 三类项目级能力
    目录展开的 **唯一出口**：root 去重、相对目录拼接、exists 判定、布局诊断只在此
    发生一次；各发现器只消费 layout 给出的 location，不再各自拼 ``root / dir``。
  * 本模块只处理"项目级"路径。用户级 / 工作区级路径（workspaces.json、previews/、
    edits/、spills/ 等）保持在各自调用点，不在这里过度抽象，避免多租户串数据。
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from prismclaw.schema.shared.capability_layout import (
    CapabilityKind,
    CapabilityLocation,
    DiagnosticLevel,
    LayoutDiagnostic,
    ProjectCapabilityLayout,
    ResolvedRoot,
    RootSource,
)
from prismclaw.schema.shared.root_set import RootSet

# 向上探测源码树时，命中任一标记即视为项目根
# 三目录重构后：主 pyproject.toml / 源码 / 配置均位于 backend/ 下，
# 用 backend/ 前缀标记确保只在仓库根命中，避免在域分包自身 pyproject.toml
# 处误判（如 backend/src/subpackages/medical/pyproject.toml）。
_ROOT_MARKERS: tuple[str, ...] = (
    "backend/pyproject.toml",
    "backend/src/prismclaw/__init__.py",
    "backend/configs/server.yaml",
    "setup.py",
)


def _scan_marker_root(start: Path) -> Path | None:
    """从 *start* 向上查找首个含源码树标记的目录，触达文件系统根则返回 None。"""
    current = start
    while True:
        if any((current / marker).exists() for marker in _ROOT_MARKERS):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def resolve_project_root_detailed(hint: Path | None = None) -> ResolvedRoot:
    """按分层顺序解析项目根，并携带定位来源与可信度。

    优先级:
      1. PRISMCLAW_ROOT 环境变量（非空字符串）        → source=ENV（confirmed）
      2. 从 hint（如 config_file 路径）向上命中标记    → source=MARKER（confirmed）
      3. 从 Path.cwd() 向上命中标记                    → source=MARKER（confirmed）
      4. fallback 到 Path.cwd()                        → source=CWD_FALLBACK（未锚定）
    """
    # 1. 环境变量优先级最高（离线部署 / 云端显式注入）
    env_raw = os.environ.get("PRISMCLAW_ROOT")
    if env_raw and env_raw.strip():
        candidate = Path(env_raw).expanduser().resolve()
        if not candidate.exists():
            # 显式声明的根不存在就创建，保证下游 mkdir(parents=True) 可用
            candidate.mkdir(parents=True, exist_ok=True)
        return ResolvedRoot(path=candidate, source=RootSource.ENV)

    # 2 / 3. 向上扫描源码树标记（hint 优先于 cwd）
    start_points: list[Path] = []
    if hint is not None:
        hint_path = Path(hint)
        start_points.append(hint_path if hint_path.is_dir() else hint_path.parent)
    start_points.append(Path.cwd())

    for start in start_points:
        try:
            resolved_start = start.resolve()
        except Exception:
            continue
        hit = _scan_marker_root(resolved_start)
        if hit is not None:
            return ResolvedRoot(path=hit, source=RootSource.MARKER)

    # 4. fallback：信任 cwd（离线裸部署、无 pyproject 也能跑），但标记为未锚定
    return ResolvedRoot(path=Path.cwd().resolve(), source=RootSource.CWD_FALLBACK)


def resolve_project_root(hint: Path | None = None) -> Path:
    """项目根的纯路径视图（向后兼容签名）。需要可信度时用 detailed 版本。"""
    return resolve_project_root_detailed(hint).path


def _coerce_explicit_root(raw: str | Path) -> ResolvedRoot | None:
    """把调用方显式传入的 root 包装为 confirmed 的 ResolvedRoot（显式即可信）。"""
    if raw is None:
        return None
    try:
        return ResolvedRoot(
            path=Path(raw).expanduser().resolve(),
            source=RootSource.MARKER,
        )
    except Exception:
        return None


def build_project_capability_layout(
    *,
    primary: Path | ResolvedRoot | None = None,
    additional: list[str | Path] | None = None,
    skills_dir: str = "",
    plugins_dir: str = "",
    agents_dir: str = "",
    extra_skills_dirs: Sequence[str] = (),
    hint: Path | None = None,
) -> ProjectCapabilityLayout:
    """把项目根展开为 skills/plugins/agents 三类能力的唯一布局结果。

    参数:
        primary: 主项目根。``None`` 时现场调用 :func:`resolve_project_root_detailed`；
            传入 ``Path`` 视为调用方显式提供（confirmed，不产生未锚定告警）；
            传入 ``ResolvedRoot`` 则原样采用其来源。
        additional: 额外项目根（如 workspace_root），同样视为显式可信。
        skills_dir / plugins_dir / agents_dir: 各自相对根的目录名（来自 settings
            的 ``workspace_*_dir`` 字段）；空字符串表示该类未配置，不展开也不诊断。
        extra_skills_dirs: 追加的 skills 目录（如用户级 ``asset_skills_dir``
            绝对路径）。skills 是唯一有双来源的能力类：workspace 级
            ``.work/skills``（graduation / ClawHub 安装）与用户级
            ``~/.prismclaw/assets/skills``（repo 同步）都必须进入发现链。
        hint: 仅当 primary=None 现场解析时透传给根定位。

    生成的诊断:
        WARN ROOT_NOT_CONFIRMED       — 现场解析的根落到 cwd 兜底（无标记锚定）。
        WARN CAPABILITY_DIR_MISSING   — 某类配置的目录在所有 root 下均不存在。
        ERROR ALL_PROJECT_CAPABILITIES_EMPTY — 根已确证，但三类配置目录全部不存在。
    """
    # 1) 主根：现场解析 or 采用显式传入
    if primary is None:
        primary_root = resolve_project_root_detailed(hint)
    elif isinstance(primary, ResolvedRoot):
        primary_root = primary
    else:
        coerced = _coerce_explicit_root(primary)
        primary_root = coerced or resolve_project_root_detailed(hint)

    # 2) 额外根（显式可信）
    extra_roots: list[ResolvedRoot] = []
    for raw in additional or []:
        root = _coerce_explicit_root(raw)
        if root is not None:
            extra_roots.append(root)

    # 3) 用 RootSet 做有序去重（first-wins，与历史行为一致），再映射回带来源的根
    candidate_by_path: dict[Path, ResolvedRoot] = {}
    for root in (primary_root, *extra_roots):
        candidate_by_path.setdefault(root.path, root)
    ordered_paths = RootSet.from_primary_and_additional(
        primary=primary_root.path,
        additional=[root.path for root in extra_roots],
    ).roots
    roots = tuple(candidate_by_path[p] for p in ordered_paths if p in candidate_by_path)

    diagnostics: list[LayoutDiagnostic] = []
    if primary_root.source is RootSource.CWD_FALLBACK:
        diagnostics.append(
            LayoutDiagnostic(
                level=DiagnosticLevel.WARN,
                code="ROOT_NOT_CONFIRMED",
                message=(
                    "项目根未被 PRISMCLAW_ROOT 或源码树标记锚定，"
                    f"当前回退到工作目录 {primary_root.path}；项目级能力目录可能定位错误。"
                ),
            )
        )

    # 4) 三类能力目录展开（相对目录名来自 settings，空串=该类未配置）
    kind_dirs: dict[CapabilityKind, tuple[str, ...]] = {}
    if skills_dir and skills_dir.strip():
        extra = tuple(d.strip() for d in extra_skills_dirs if d and d.strip())
        kind_dirs[CapabilityKind.SKILLS] = (skills_dir.strip(), *extra)
    if plugins_dir and plugins_dir.strip():
        kind_dirs[CapabilityKind.PLUGINS] = (plugins_dir.strip(),)
    if agents_dir and agents_dir.strip():
        kind_dirs[CapabilityKind.AGENTS] = (agents_dir.strip(),)

    locations: dict[CapabilityKind, tuple[CapabilityLocation, ...]] = {}
    for kind, rel_dirs in kind_dirs.items():
        kind_locations: list[CapabilityLocation] = []
        for rel_dir in rel_dirs:
            expanded_dir = Path(rel_dir).expanduser()
            # 绝对目录（如 ~/.prismclaw/assets/skills）与项目根无关，只展开一次，避免重复扫描。
            roots_for_dir = roots[:1] if expanded_dir.is_absolute() else roots
            for root in roots_for_dir:
                candidate = expanded_dir if expanded_dir.is_absolute() else root.path / expanded_dir
                try:
                    resolved = candidate.resolve()
                except Exception:
                    resolved = candidate
                kind_locations.append(
                    CapabilityLocation(
                        kind=kind,
                        root=root,
                        path=resolved,
                        exists=resolved.is_dir(),
                    )
                )
        locations[kind] = tuple(kind_locations)

    layout = ProjectCapabilityLayout(
        roots=roots,
        locations=locations,
        diagnostics=tuple(diagnostics),
    )

    # 5) 缺失诊断（基于展开结果，在这一处统一产生）
    for kind, rel_dirs in kind_dirs.items():
        if not layout.has_capability(kind):
            diagnostics.append(
                LayoutDiagnostic(
                    level=DiagnosticLevel.WARN,
                    code="CAPABILITY_DIR_MISSING",
                    message=(
                        f"{kind.value} 能力目录 '{list(rel_dirs)}' 在所有项目根下均不存在"
                        f"（根={[str(r.path) for r in roots]}）。"
                    ),
                )
            )

    if (
        primary_root.confirmed
        and kind_dirs
        and all(not layout.has_capability(kind) for kind in kind_dirs)
    ):
        diagnostics.append(
            LayoutDiagnostic(
                level=DiagnosticLevel.ERROR,
                code="ALL_PROJECT_CAPABILITIES_EMPTY",
                message=(
                    "项目根已被显式锚定，但 skills/plugins/agents 三类能力目录全部不存在；"
                    "疑似项目根指向错误或部署内容缺失。"
                ),
            )
        )

    return ProjectCapabilityLayout(
        roots=roots,
        locations=locations,
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "resolve_project_root",
    "resolve_project_root_detailed",
    "build_project_capability_layout",
]
