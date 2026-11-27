"""基础技能继承的回归测试。

背景：专家包加载时会把全局基础技能目录（``extra_skill_dirs``）合并进
``expert.skills``，导致：
1. 基础技能出现在每个专家的技能清单里（展示层混入非本包技能）；
2. 运行时被注册成 ``<expert>:<skill>``，每个专家各持一份副本，
   catalog 膨胀，且裸名调用命中多个副本而 ``ambiguous``。

期望契约：
- ``expert.skills`` 只含本包自有技能；继承技能进入 ``inherited_skills``。
- catalog 中基础技能只注册一次，``expert_name is None``（全局能力）。
- 裸名激活可解析，不再歧义。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismclaw.assembly.experts.load_wiring import assemble_expert_load_result
from prismclaw.engine.execution.capabilities.catalog import (
    RuntimeCatalog,
    SkillCatalogEntry,
)
from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager


def _write_skill(skills_dir: Path, name: str) -> None:
    target = skills_dir / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} 描述\n---\n# {name}\n",
        encoding="utf-8",
    )


def _write_expert(experts_dir: Path, name: str, own_skill: str) -> None:
    root = experts_dir / name
    (root / ".plugin").mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} package description",
        "expertType": "agent",
        "agentName": f"{name}-lead",
        "displayName": {"en": name, "zh": name},
        "profession": {"en": name, "zh": name},
        "displayDescription": {
            "en": f"{name} description for testing inherited skill handling",
            "zh": "用于验证基础技能继承行为的中文描述，覆盖专业能力和输出边界。",
        },
        "avatar": "avatars/lead.png",
        "categoryId": "test",
        "categoryName": "测试分组",
        "defaultInitPrompt": {"en": "Start", "zh": "开始"},
        "tags": ["test", "demo", "expert"],
        "quickPrompts": [
            {"en": "Start", "zh": "开始"},
            {"en": "Continue", "zh": "继续"},
            {"en": "Wrap up", "zh": "收尾"},
        ],
        "members": [],
    }
    (root / ".plugin" / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (root / "agents" / f"{name}-lead.md").write_text(
        f"---\nname: {name}-lead\ndescription: {name} lead agent\n---\n"
        f"{name} persona prompt\n",
        encoding="utf-8",
    )
    _write_skill(root / "skills", own_skill)


@pytest.fixture
def loaded(tmp_path: Path):
    """两个专家包 + 一份全局基础技能，走真实加载与注册链路。"""
    experts_dir = tmp_path / "experts"
    base_skills = tmp_path / "base-skills"
    experts_dir.mkdir()
    base_skills.mkdir()
    _write_expert(experts_dir, "alpha-law", "alpha-playbook")
    _write_expert(experts_dir, "beta-med", "beta-playbook")
    _write_skill(base_skills, "pdf")
    return experts_dir, base_skills


async def _build_catalog(experts_dir: Path, base_skills: Path):
    result = await assemble_expert_load_result(
        directories=[experts_dir],
        extra_skill_dirs=[base_skills],
    )
    manager = ExtensionManager()
    manager.load_from_load_result(result)
    entries = [
        SkillCatalogEntry(
            name=d.manifest.name,
            description=d.manifest.description or "",
            location=d.manifest.file_path or "",
            instructions=d.manifest.body or "",
            manifest=d.manifest,
            expert_name=d.expert_name,
        )
        for d in manager.iter_skill_descriptors()
    ]
    return result, RuntimeCatalog(skill_entries=tuple(entries))


@pytest.mark.asyncio
async def test_inherited_skills_not_listed_as_expert_own_skills(loaded) -> None:
    """专家技能清单不得混入继承的基础技能。"""
    experts_dir, base_skills = loaded
    result, _catalog = await _build_catalog(experts_dir, base_skills)

    alpha = next(p for p in result.enabled if p.name == "alpha-law")
    own_names = {s.name for s in alpha.skills}
    assert own_names == {"alpha-playbook"}, (
        f"expert.skills 混入了非本包技能: {sorted(own_names)}"
    )


@pytest.mark.asyncio
async def test_base_skill_registered_once_as_global(loaded) -> None:
    """基础技能在 catalog 中只应有一份，且不属于任何专家。"""
    experts_dir, base_skills = loaded
    _result, catalog = await _build_catalog(experts_dir, base_skills)

    pdf_entries = [
        e for e in catalog.list_skill_entries() if e.name.split(":")[-1] == "pdf"
    ]
    assert len(pdf_entries) == 1, (
        f"基础技能被复制了 {len(pdf_entries)} 份: {[e.name for e in pdf_entries]}"
    )
    assert pdf_entries[0].expert_name is None, (
        f"基础技能被错误归属到专家: {pdf_entries[0].expert_name}"
    )
    assert pdf_entries[0].name == "pdf", (
        f"基础技能不应拼接专家前缀: {pdf_entries[0].name}"
    )


@pytest.mark.asyncio
async def test_bare_name_activation_is_not_ambiguous(loaded) -> None:
    """裸名激活基础技能必须可解析（此前命中多份副本导致 ambiguous）。"""
    experts_dir, base_skills = loaded
    _result, catalog = await _build_catalog(experts_dir, base_skills)

    resolution = catalog.resolve_skill_reference("pdf")
    assert resolution.kind == "resolved", (
        f"裸名激活失败: kind={resolution.kind}, members={resolution.member_names}"
    )
    assert resolution.entry is not None


@pytest.mark.asyncio
async def test_own_skill_keeps_expert_namespace(loaded) -> None:
    """自有技能仍保留专家命名空间，避免跨包重名冲突。"""
    experts_dir, base_skills = loaded
    _result, catalog = await _build_catalog(experts_dir, base_skills)

    resolution = catalog.resolve_skill_reference("alpha-law:alpha-playbook")
    assert resolution.kind == "resolved"
    assert resolution.entry is not None
    assert resolution.entry.expert_name == "alpha-law"
