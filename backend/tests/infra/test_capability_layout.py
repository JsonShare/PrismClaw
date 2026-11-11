"""项目能力布局（L0 值对象 + infra builder）单一事实源的回归测试。

覆盖重构计划验收标准：
  * 三种项目根来源（env / marker / cwd_fallback）与 confirmed 可信度；
  * 单个能力目录缺失 → WARN（不降级）；
  * 已确证根但三类配置目录全空 → ERROR（health 降级依据）；
  * 健康布局零诊断、summary_dict JSON 安全；
  * 未锚定（workspace-only / 裸部署）即使目录全空也不得误报 ERROR；
  * RootSet 有序去重（first-wins，resolve 归一化）。

conftest 会 autouse 注入 PRISMCLAW_ROOT，因此涉及 marker / cwd_fallback 的
用例必须显式 monkeypatch.delenv 接管环境。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismclaw.infra.paths import (
    build_project_capability_layout,
    resolve_project_root_detailed,
)
from prismclaw.schema.shared import (
    CapabilityKind,
    DiagnosticLevel,
    RootSource,
)

SKILLS = "backend/.prismclaw/skills"
PLUGINS = "backend/.prismclaw/plugins"
AGENTS = "backend/.prismclaw/agents"


def _make_marker_tree(root: Path) -> Path:
    """造一个可被 marker 扫描命中的仓库根（backend/src/prismclaw/__init__.py）。"""
    pkg = root / "backend" / "src" / "prismclaw"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# 项目根分层解析
# --------------------------------------------------------------------------- #

def test_root_source_env_is_confirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "explicit_root"
    target.mkdir()
    monkeypatch.setenv("PRISMCLAW_ROOT", str(target))

    resolved = resolve_project_root_detailed()

    assert resolved.source is RootSource.ENV
    assert resolved.confirmed is True
    assert resolved.path == target.resolve()


def test_root_source_marker_walks_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PRISMCLAW_ROOT", raising=False)
    repo = _make_marker_tree(tmp_path / "repo")
    nested = repo / "backend" / "src" / "prismclaw"

    resolved = resolve_project_root_detailed(hint=nested)

    assert resolved.source is RootSource.MARKER
    assert resolved.confirmed is True
    assert resolved.path == repo.resolve()


def test_root_source_cwd_fallback_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("PRISMCLAW_ROOT", raising=False)
    # /var/folders 之上无任何项目 marker，扫描必然落空
    monkeypatch.chdir(tmp_path)

    resolved = resolve_project_root_detailed()

    assert resolved.source is RootSource.CWD_FALLBACK
    assert resolved.confirmed is False
    assert resolved.path == tmp_path.resolve()


# --------------------------------------------------------------------------- #
# 健康 / 缺失 / 全空 的诊断分级
# --------------------------------------------------------------------------- #

def test_healthy_layout_has_no_diagnostics_and_locates_three_kinds(tmp_path: Path):
    root = _make_marker_tree(tmp_path / "repo")
    for rel in (SKILLS, PLUGINS, AGENTS):
        (root / rel).mkdir(parents=True, exist_ok=True)

    layout = build_project_capability_layout(
        primary=root,
        skills_dir=SKILLS,
        plugins_dir=PLUGINS,
        agents_dir=AGENTS,
    )

    assert layout.roots[0].confirmed is True
    assert len(layout.existing_locations_of(CapabilityKind.SKILLS)) == 1
    assert len(layout.existing_locations_of(CapabilityKind.PLUGINS)) == 1
    assert len(layout.existing_locations_of(CapabilityKind.AGENTS)) == 1
    assert layout.diagnostics == ()
    assert layout.has_error is False
    # 启动日志 / health 复用的快照必须可 JSON 序列化
    json.dumps(layout.summary_dict())


def test_single_missing_kind_warns_but_does_not_error(tmp_path: Path):
    root = _make_marker_tree(tmp_path / "repo")
    (root / SKILLS).mkdir(parents=True, exist_ok=True)
    # plugins / agents 目录刻意不创建

    layout = build_project_capability_layout(
        primary=root,
        skills_dir=SKILLS,
        plugins_dir=PLUGINS,
        agents_dir=AGENTS,
    )

    missing_codes = [d.code for d in layout.warnings]
    assert missing_codes.count("CAPABILITY_DIR_MISSING") == 2
    assert len(layout.missing_locations_of(CapabilityKind.PLUGINS)) == 1
    assert len(layout.missing_locations_of(CapabilityKind.AGENTS)) == 1
    assert layout.has_capability(CapabilityKind.SKILLS) is True
    assert layout.has_error is False  # 仍有 skills，不应升级为 ERROR


def test_confirmed_root_with_all_kinds_empty_is_error(tmp_path: Path):
    root = _make_marker_tree(tmp_path / "repo")  # 已锚定，但三类目录都不存在

    layout = build_project_capability_layout(
        primary=root,
        skills_dir=SKILLS,
        plugins_dir=PLUGINS,
        agents_dir=AGENTS,
    )

    codes = {d.code: d.level for d in layout.diagnostics}
    assert codes["ALL_PROJECT_CAPABILITIES_EMPTY"] is DiagnosticLevel.ERROR
    assert layout.has_error is True
    assert len(layout.errors) == 1


def test_unconfigured_kinds_are_neither_expanded_nor_diagnosed(tmp_path: Path):
    """空串=未配置：只配 skills 时不得为 plugins/agents 生成 location 或告警。"""
    root = _make_marker_tree(tmp_path / "repo")
    (root / SKILLS).mkdir(parents=True, exist_ok=True)

    layout = build_project_capability_layout(primary=root, skills_dir=SKILLS)

    assert layout.locations_of(CapabilityKind.PLUGINS) == ()
    assert layout.locations_of(CapabilityKind.AGENTS) == ()
    assert layout.diagnostics == ()


def test_unconfirmed_root_all_empty_only_warns(tmp_path: Path, monkeypatch):
    """workspace-only / 裸部署（cwd 兜底、未锚定）即使全空也不得误报 ERROR。"""
    monkeypatch.delenv("PRISMCLAW_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    layout = build_project_capability_layout(
        primary=None,
        skills_dir=SKILLS,
        plugins_dir=PLUGINS,
        agents_dir=AGENTS,
    )

    codes = [d.code for d in layout.diagnostics]
    assert "ROOT_NOT_CONFIRMED" in codes
    assert "ALL_PROJECT_CAPABILITIES_EMPTY" not in codes
    assert layout.has_error is False


# --------------------------------------------------------------------------- #
# RootSet 有序去重
# --------------------------------------------------------------------------- #

def test_roots_are_deduped_first_wins_and_ordered(tmp_path: Path):
    root = _make_marker_tree(tmp_path / "repo")
    second = tmp_path / "ws"
    second.mkdir()

    layout = build_project_capability_layout(
        primary=root,
        # 第一个经 "."/字符串与 primary 解析后同一目录，应被去重；second 保留
        additional=[str(root) + "/.", str(second)],
        skills_dir=SKILLS,
    )

    root_paths = [r.path for r in layout.roots]
    assert root_paths == [root.resolve(), second.resolve()]
    assert all(r.confirmed for r in layout.roots)


def test_skills_dual_source_expands_workspace_and_extra_absolute(tmp_path: Path):
    """skills 是唯一双来源能力类：workspace 相对目录 + 用户级绝对目录都要展开。

    绝对目录只展开一次（不随 root 数量重复），相对目录拼到每个 root。
    """
    root = _make_marker_tree(tmp_path / "repo")
    second = tmp_path / "ws"
    second.mkdir()
    (second / ".work" / "skills").mkdir(parents=True)
    extra = tmp_path / "home" / "assets" / "skills"
    extra.mkdir(parents=True)

    layout = build_project_capability_layout(
        primary=root,
        additional=[second],
        skills_dir=".work/skills",
        extra_skills_dirs=(str(extra),),
    )

    paths = [p.resolve() for p in layout.existing_locations_of(CapabilityKind.SKILLS)]
    assert (second / ".work" / "skills").resolve() in paths
    assert extra.resolve() in paths
    # 绝对目录只出现一次，不被两个 root 重复展开
    assert paths.count(extra.resolve()) == 1
