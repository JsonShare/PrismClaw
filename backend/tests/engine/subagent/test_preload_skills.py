"""成员 md `skills:`（preload_skills）的解析优先级。

专家包声明的预加载技能优先取本包版本；包内没有时回落到通用技能。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.prompting import resolve_preloaded_skill_context
from prismclaw.schema.services.skills import SkillManifest


class _FakeSkillRuntime:
    def __init__(self, mapping: dict[str, SkillManifest]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def resolve_skill(self, name: str, *, workspace_path: Path) -> SkillManifest | None:
        self.calls.append(name)
        return self.mapping.get(name)


def _manifest(*names: str) -> AgentManifest:
    return AgentManifest(name="member", description="member agent", skills=list(names))


def _skill(name: str, body: str) -> SkillManifest:
    return SkillManifest(name=name, description=f"desc {name}", body=body, source="skill")


def test_preload_prefers_expert_package_version(tmp_path: Path) -> None:
    expert = _skill("medical-clinical:clinical-note-extract", "EXPERT BODY")
    global_skill = _skill("clinical-note-extract", "GLOBAL BODY")
    runtime = _FakeSkillRuntime(
        {
            "medical-clinical:clinical-note-extract": expert,
            "clinical-note-extract": global_skill,
        }
    )
    parent = SimpleNamespace(
        workspace_path=str(tmp_path),
        session_scope=SimpleNamespace(team_name="medical-clinical"),
    )

    result = resolve_preloaded_skill_context(
        parent_ctx=parent,  # type: ignore[arg-type]
        manifest=_manifest("clinical-note-extract"),
        skill_runtime=runtime,
    )

    assert [skill.body for skill in result.manifests] == ["EXPERT BODY"]
    assert runtime.calls == ["medical-clinical:clinical-note-extract"]


def test_preload_falls_back_to_shared_skill(tmp_path: Path) -> None:
    shared = _skill("shared-tool", "SHARED BODY")
    runtime = _FakeSkillRuntime({"shared-tool": shared})
    parent = SimpleNamespace(
        workspace_path=str(tmp_path),
        session_scope=SimpleNamespace(team_name="medical-clinical"),
    )

    result = resolve_preloaded_skill_context(
        parent_ctx=parent,  # type: ignore[arg-type]
        manifest=_manifest("shared-tool"),
        skill_runtime=runtime,
    )

    assert [skill.body for skill in result.manifests] == ["SHARED BODY"]
    assert runtime.calls == ["medical-clinical:shared-tool", "shared-tool"]


def test_preload_missing_skill_raises(tmp_path: Path) -> None:
    runtime = _FakeSkillRuntime({})
    parent = SimpleNamespace(workspace_path=str(tmp_path), session_scope=None)

    with pytest.raises(LookupError, match="not found"):
        resolve_preloaded_skill_context(
            parent_ctx=parent,  # type: ignore[arg-type]
            manifest=_manifest("missing"),
            skill_runtime=runtime,
        )
