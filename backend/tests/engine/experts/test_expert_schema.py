from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest

from prismclaw.assembly.experts.agent_manifest_loader import DefaultExpertAgentLoader
from prismclaw.engine.experts import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading.load_strategy import DeclarativeLoadStrategy
from prismclaw.engine.experts.manifest.expert_schema import (
    EXPERT_AGENT_MAX_TOKENS,
    validate_expert_package,
)
from prismclaw.engine.experts.paths import MANIFEST_REL_PATH
from prismclaw.engine.experts.registry.identity import EXPERT_SOURCE, ExpertID

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "expert_packages"
_VALID_FIXTURE = _FIXTURES / "valid-agent"
_TEAM_FIXTURE = _FIXTURES / "team-lead-mismatch"
_INVALID_CASES = (
    ("missing-agent", ExpertErrorCode.MANIFEST_INVALID),
    ("agent-tools", ExpertErrorCode.MANIFEST_AGENT_TOOLS_FORBIDDEN),
    ("agent-too-long", ExpertErrorCode.MANIFEST_AGENT_TOO_LONG),
    ("hooks-declaration", ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP),
    ("team-lead-mismatch", ExpertErrorCode.MANIFEST_INVALID),
)


def _read_manifest(package: Path) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        (package / MANIFEST_REL_PATH).read_text(encoding="utf-8")
    )
    return raw


def _detail(error: ExpertError) -> dict[str, Any]:
    detail = error.detail
    assert isinstance(detail, dict)
    return detail


def _write_manifest(package: Path, manifest: dict[str, Any]) -> None:
    (package / MANIFEST_REL_PATH).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def _copy_fixture(tmp_path: Path, source: Path = _VALID_FIXTURE) -> Path:
    package = tmp_path / source.name
    shutil.copytree(source, package)
    return package


def _write_agent(package: Path, frontmatter: str, body: str = "你是医学科研专家。") -> None:
    agent_name = _read_manifest(package)["agentName"]
    (package / "agents" / f"{agent_name}.md").write_text(
        f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_valid_example_loads_without_warnings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    package = _copy_fixture(tmp_path)
    strategy = DeclarativeLoadStrategy(
        agent_loader=DefaultExpertAgentLoader(), data_base_dir=tmp_path / "data"
    )

    with caplog.at_level(logging.WARNING):
        manifest = validate_expert_package(package, _read_manifest(package))
        loaded, errors = await strategy.load(
            ExpertID(name="valid-agent", source=EXPERT_SOURCE), package
        )

    assert manifest.agent_name == "valid-agent"
    assert errors == []
    assert loaded is not None
    assert loaded.agents
    agents = loaded.agents
    assert [agent.name for agent in agents] == ["valid-agent"]
    assert agents[0].system_prompt.startswith("你是医学科研专家")
    assert "tools:" not in agents[0].system_prompt
    assert caplog.records == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("fixture_name", "expected_code"), _INVALID_CASES)
async def test_invalid_fixture_is_rejected_as_a_whole_package(
    fixture_name: str, expected_code: ExpertErrorCode, tmp_path: Path
) -> None:
    package = _FIXTURES / fixture_name
    raw = _read_manifest(package)

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, raw)

    assert exc_info.value.code is expected_code
    assert _detail(exc_info.value) is not None
    strategy = DeclarativeLoadStrategy(
        agent_loader=DefaultExpertAgentLoader(), data_base_dir=tmp_path / "data"
    )
    loaded, errors = await strategy.load(
        ExpertID(name=fixture_name, source=EXPERT_SOURCE), package
    )

    assert loaded is None
    assert len(errors) == 1
    assert errors[0].type == expected_code.value
    assert errors[0].source == EXPERT_SOURCE
    assert errors[0].expert == fixture_name
    assert errors[0].detail == _detail(exc_info.value)


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "version",
        "description",
        "expertType",
        "agentName",
        "displayName",
        "profession",
        "displayDescription",
        "avatar",
        "categoryId",
        "defaultInitPrompt",
    ],
)
def test_manifest_required_fields(field: str) -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    raw.pop(field)

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_VALID_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize("field", ["displayName", "profession", "defaultInitPrompt"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_localized_fields_require_en_and_zh(field: str, language: str) -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    raw[field].pop(language)

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_VALID_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize(("length", "warns"), [(39, True), (40, False), (50, False), (51, True)])
def test_display_description_chinese_length_is_advisory(
    length: int, warns: bool, caplog: pytest.LogCaptureFixture
) -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    raw["displayDescription"] = {"en": "Research description", "zh": "界" * length}

    with caplog.at_level(logging.WARNING, logger="prismclaw.engine.experts.manifest.expert_schema"):
        manifest = validate_expert_package(_VALID_FIXTURE, raw)

    assert manifest.display_description is not None
    assert bool(caplog.records) is warns


@pytest.mark.parametrize("field", ["hooks", "mcp", "mcpServers", "mcp_servers"])
def test_hook_and_mcp_keys_are_forbidden_even_when_empty(field: str) -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    raw[field] = None

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_VALID_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP
    assert _detail(exc_info.value)["fields"] == [field]


@pytest.mark.parametrize("field", ["commands", ".lsp.json", "lspServers", "lsp_servers"])
def test_command_and_lsp_keys_are_invalid(field: str) -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    raw[field] = None

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_VALID_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize(
    ("relative_path", "is_directory", "expected_code"),
    [
        ("hooks", True, ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP),
        ("mcp.json", False, ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP),
        (".mcp.json", False, ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP),
        (".plugin/mcp.json", False, ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP),
        ("commands", True, ExpertErrorCode.MANIFEST_INVALID),
        (".lsp.json", False, ExpertErrorCode.MANIFEST_INVALID),
    ],
)
def test_forbidden_package_file_declarations_are_rejected(
    tmp_path: Path,
    relative_path: str,
    is_directory: bool,
    expected_code: ExpertErrorCode,
) -> None:
    package = _copy_fixture(tmp_path)
    path = package / relative_path
    if is_directory:
        path.mkdir()
    else:
        path.write_text("{}", encoding="utf-8")

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _read_manifest(package))

    assert exc_info.value.code is expected_code
    assert str(path) in _detail(exc_info.value)["paths"]


@pytest.mark.parametrize("field", ["tools", "allowed_tools", "allowedTools"])
def test_all_agent_tool_field_spellings_are_forbidden(tmp_path: Path, field: str) -> None:
    package = _copy_fixture(tmp_path)
    _write_agent(package, f"name: valid-agent\ndescription: test\n{field}: []")

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _read_manifest(package))

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_AGENT_TOOLS_FORBIDDEN
    assert _detail(exc_info.value)["fields"] == [field]


@pytest.mark.parametrize("declaration", ["maxTurns: 1", "skills: []"])
def test_agent_max_turns_and_skills_aliases_are_accepted(
    tmp_path: Path, declaration: str
) -> None:
    package = _copy_fixture(tmp_path)
    _write_agent(package, f"name: valid-agent\ndescription: test\n{declaration}")

    manifest = validate_expert_package(package, _read_manifest(package))

    assert manifest.agent_name == "valid-agent"


@pytest.mark.parametrize(
    ("field", "canonical"),
    [
        ("preloadSkills", "preload_skills"),
    ],
)
def test_camel_case_agent_fields_remain_rejected(
    tmp_path: Path, field: str, canonical: str
) -> None:
    package = _copy_fixture(tmp_path)
    _write_agent(package, f"name: valid-agent\ndescription: test\n{field}: []")

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _read_manifest(package))

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["field_name"] == field
    assert _detail(exc_info.value)["canonical_field"] == canonical


def test_agent_name_must_match_filename_and_body_must_not_be_empty(tmp_path: Path) -> None:
    package = _copy_fixture(tmp_path)
    _write_agent(package, "name: other-agent\ndescription: test")

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _read_manifest(package))
    assert _detail(exc_info.value)["expected_name"] == "valid-agent"

    _write_agent(package, "name: valid-agent\ndescription: test", body="  \n")
    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _read_manifest(package))
    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID


def test_agent_expert_agent_name_need_not_equal_package_name(tmp_path: Path) -> None:
    package = _copy_fixture(tmp_path)
    (package / "agents" / "valid-agent.md").unlink()
    raw = _read_manifest(package)
    raw["agentName"] = "other-agent"
    _write_manifest(package, raw)
    _write_agent(package, "name: other-agent\ndescription: test")

    manifest = validate_expert_package(package, _read_manifest(package))

    assert manifest.agent_name == "other-agent"


def test_generic_team_lead_name_is_rejected() -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    raw["agentName"] = "team-lead"

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_VALID_FIXTURE, raw)

    assert _detail(exc_info.value)["field"] == "agentName"


def _valid_team_manifest() -> dict[str, Any]:
    raw = _read_manifest(_TEAM_FIXTURE)
    raw["teamInfo"]["leadAgent"] = raw["agentName"]
    raw["members"][0]["role"] = "lead"
    return raw


def test_valid_team_and_team_profession_contract() -> None:
    raw = _valid_team_manifest()
    manifest = validate_expert_package(_TEAM_FIXTURE, raw)
    assert manifest.team_info is not None
    assert manifest.team_info.lead_agent == "team-lead-mismatch-team-lead"

    raw["profession"]["zh"] = "不同的团队职称"
    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_TEAM_FIXTURE, raw)
    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["field"] == "profession"


def test_team_member_agents_must_all_appear_in_members() -> None:
    raw = _valid_team_manifest()
    raw["members"] = raw["members"][:1]

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_TEAM_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["missing_members"] == ["medication-safety"]


def test_team_member_agents_must_not_contain_lead() -> None:
    raw = _valid_team_manifest()
    raw["teamInfo"]["memberAgents"].append("team-lead-mismatch-team-lead")

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_TEAM_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["lead_agent"] == "team-lead-mismatch-team-lead"


def test_team_lead_must_match_agent_name() -> None:
    raw = _valid_team_manifest()
    raw["teamInfo"]["leadAgent"] = "medication-safety"

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_TEAM_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["agent_name"] == "team-lead-mismatch-team-lead"


@pytest.mark.parametrize(
    ("case", "expected_field"),
    [
        ("unexpected_member", "members"),
        ("duplicate_member", "members"),
        ("duplicate_member_agent", "teamInfo.memberAgents"),
        ("wrong_role", "members"),
    ],
)
def test_team_declarations_require_exact_consistent_membership(
    case: str, expected_field: str
) -> None:
    raw = _valid_team_manifest()
    if case == "unexpected_member":
        raw["members"].append({**raw["members"][1], "id": "unlisted-member"})
    elif case == "duplicate_member":
        raw["members"].append(raw["members"][1])
    elif case == "duplicate_member_agent":
        raw["teamInfo"]["memberAgents"].append("medication-safety")
    else:
        raw["members"][1]["role"] = "lead"

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_TEAM_FIXTURE, raw)

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["field"] == expected_field


def test_team_member_agent_file_must_exist(tmp_path: Path) -> None:
    package = _copy_fixture(tmp_path, _TEAM_FIXTURE)
    (package / "agents" / "medication-safety.md").unlink()

    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _valid_team_manifest())

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_INVALID
    assert _detail(exc_info.value)["field"] == "teamInfo.memberAgents"
    assert _detail(exc_info.value)["agent_name"] == "medication-safety"


def test_tags_quick_prompts_and_default_init_prompt_contract() -> None:
    raw = _read_manifest(_VALID_FIXTURE)
    prompt = {"en": "What are we researching?", "zh": "这次要研究什么问题？"}
    raw["tags"] = ["research", "evidence", "medical"]
    raw["quickPrompts"] = [
        prompt,
        {"en": "Review this paper", "zh": "帮我审阅这篇论文"},
        {"en": "Design a study", "zh": "设计一个研究方案"},
    ]
    manifest = validate_expert_package(_VALID_FIXTURE, raw)
    assert manifest.tags is not None and len(manifest.tags) == 3
    assert manifest.quick_prompts is not None and len(manifest.quick_prompts) == 3

    for tags in (["one", "two"], ["one", "two", "three", "four"]):
        bad = {**raw, "tags": tags}
        with pytest.raises(ExpertError) as exc_info:
            validate_expert_package(_VALID_FIXTURE, bad)
        assert _detail(exc_info.value)["field"] == "tags"

    for prompts in ([prompt], [*raw["quickPrompts"], {"en": "More", "zh": "更多"}]):
        bad = {**raw, "quickPrompts": prompts}
        with pytest.raises(ExpertError) as exc_info:
            validate_expert_package(_VALID_FIXTURE, bad)
        assert _detail(exc_info.value)["field"] == "quickPrompts"

    raw["defaultInitPrompt"] = {"en": "Another", "zh": "另一个"}
    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(_VALID_FIXTURE, raw)
    assert _detail(exc_info.value)["field"] == "defaultInitPrompt"


@pytest.mark.asyncio
async def test_agents_manifest_array_is_not_used_for_expert_discovery(tmp_path: Path) -> None:
    package = _copy_fixture(tmp_path)
    raw = _read_manifest(package)
    raw["agents"] = ["./elsewhere"]
    _write_manifest(package, raw)
    elsewhere = package / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "rogue.md").write_text("not valid frontmatter", encoding="utf-8")
    strategy = DeclarativeLoadStrategy(
        agent_loader=DefaultExpertAgentLoader(), data_base_dir=tmp_path / "data"
    )

    loaded, errors = await strategy.load(
        ExpertID(name="valid-agent", source=EXPERT_SOURCE), package
    )

    assert errors == []
    assert loaded is not None
    assert [a.name for a in loaded.agents] == ["valid-agent"]


def test_long_fixture_exceeds_real_token_limit() -> None:
    package = _FIXTURES / "agent-too-long"
    with pytest.raises(ExpertError) as exc_info:
        validate_expert_package(package, _read_manifest(package))

    assert exc_info.value.code is ExpertErrorCode.MANIFEST_AGENT_TOO_LONG
    assert _detail(exc_info.value)["estimated_tokens"] > EXPERT_AGENT_MAX_TOKENS
