"""技能发现双来源回归测试。

历史缺陷：安装链写 ``<ws>/.work/skills``，但发现链只展开用户级
``~/.prismclaw/assets/skills``（绝对路径），导致 graduation / ClawHub 安装的
技能永不进入目录。本测试锁定两条来源都必须被扫描：
  * 用户级 assets/skills（repo 同步，绝对路径）；
  * workspace 级 ``.work/skills``（运行时安装，相对路径）。
并锁定 walk-up 动态发现默认读取 ``workspace_skills_dir``。
"""
from __future__ import annotations

from pathlib import Path

from prismclaw.infra.settings import SkillSettings
from prismclaw.runtime.skills.discovery import discover_skill_dirs_for_paths
from prismclaw.runtime.skills.provider import DefaultSkillProvider


def _write_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill {name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_provider_scans_workspace_installed_and_user_level_skills(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    user_skills = tmp_path / "home" / "assets" / "skills"
    _write_skill(ws / ".work" / "skills", "installed")
    _write_skill(user_skills, "asset")

    settings = SkillSettings(
        asset_skills_dir=str(user_skills),
        workspace_skills_dir=".work/skills",
    )
    provider = DefaultSkillProvider(settings=settings)

    names = {skill.name for skill in provider.list_skills(str(ws))}

    assert names == {"installed", "asset"}


def test_walk_up_default_targets_workspace_skills_dir(tmp_path: Path):
    ws = tmp_path / "ws"
    skill_root = ws / ".work" / "skills"
    _write_skill(skill_root, "walked")
    touched = ws / "sub" / "file.txt"
    touched.parent.mkdir(parents=True, exist_ok=True)
    touched.write_text("x", encoding="utf-8")

    found = {p.resolve() for p in discover_skill_dirs_for_paths([touched], [ws])}

    assert skill_root.resolve() in found
