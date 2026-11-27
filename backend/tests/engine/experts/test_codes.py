from prismclaw.engine.experts import ExpertError, ExpertErrorCode, codes


def test_expert_error_codes_match_spec_without_duplicates() -> None:
    expected = {
        "LEGACY_SUITE_UNSUPPORTED",
        "BUILTIN_EXPERT_NOT_LISTED",
        "EXPERT_UNAVAILABLE",
        "EXPERT_NOT_FOUND",
        "EXPERT_ID_AMBIGUOUS",
        "EXPERT_TOOL_SURFACE_EMPTY",
        "EXPERT_PROMPT_TOO_LONG",
        "SKILL_SELECTION_LIMIT",
        "SKILL_TOOL_UNRESOLVED",
        "TEAM_REGISTRY_UNAVAILABLE",
        "EXPERT_FORBIDDEN",
        "MANIFEST_AGENT_TOOLS_FORBIDDEN",
        "MANIFEST_AGENT_TOO_LONG",
        "MANIFEST_INVALID",
        "MANIFEST_EXPERT_DEPENDS_ON_EXPERT",
        "MANIFEST_EXPERT_HAS_HOOKS_OR_MCP",
        "BUILTIN_REGISTRY_LOAD_FAILED",
        "MODULE_DISABLED",
        "STATE_UNKNOWN_MODULE",
        "STATE_KIND_MISMATCH",
        "CAPABILITY_POLICY_LOAD_FAILED",
    }

    members = ExpertErrorCode.__members__
    assert len(members) == 21
    assert set(members) == expected
    assert len(set(members.values())) == 21
    assert all(code.value == name for name, code in members.items())

    # 枚举是唯一事实源，不允许再沉淀同名的模块级字符串常量
    module_codes = {name for name in vars(codes) if name.isupper()}
    assert module_codes == set()


def test_expert_error_carries_code_message_and_detail() -> None:
    detail = {"expert_id": "missing-expert"}

    error = ExpertError(ExpertErrorCode.EXPERT_NOT_FOUND, "Expert not found", detail)

    assert error.code is ExpertErrorCode.EXPERT_NOT_FOUND
    assert error.message == "Expert not found"
    assert error.detail is detail
    assert str(error) == "Expert not found"
