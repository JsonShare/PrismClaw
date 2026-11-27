from __future__ import annotations

import logging

import pytest

from prismclaw.runtime.skills.eligibility import (
    filter_eligible,
    is_model_visible,
    is_user_visible,
)
from prismclaw.schema.agent.domains import InvocationPolicy, resolve_invocation_policy
from prismclaw.schema.services.skills import SkillManifest


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ({}, InvocationPolicy.AUTO),
        ({"invocation_policy": "auto"}, InvocationPolicy.AUTO),
        ({"invocation_policy": "user_only"}, InvocationPolicy.USER_ONLY),
        ({"invocation_policy": "model_only"}, InvocationPolicy.MODEL_ONLY),
        ({"invocation_policy": "disabled"}, InvocationPolicy.DISABLED),
        ({"user-invocable": False}, InvocationPolicy.MODEL_ONLY),
        ({"disable-model-invocation": True}, InvocationPolicy.USER_ONLY),
        (
            {"user-invocable": False, "disable-model-invocation": True},
            InvocationPolicy.DISABLED,
        ),
        ({"disable": True}, InvocationPolicy.DISABLED),
        ({"disable": False, "user-invocable": True}, InvocationPolicy.AUTO),
        (
            {"invocation_policy": "auto", "user-invocable": False},
            InvocationPolicy.AUTO,
        ),
    ],
)
def test_invocation_policy_resolution(
    frontmatter: dict[str, object], expected: InvocationPolicy
) -> None:
    assert resolve_invocation_policy(frontmatter) is expected


def test_explicit_invocation_policy_must_be_known(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="prismclaw.schema.agent.domains"):
        policy = resolve_invocation_policy({"invocation_policy": "sometimes"})

    assert policy is InvocationPolicy.DISABLED
    assert caplog.records


def _skill(policy: InvocationPolicy) -> SkillManifest:
    return SkillManifest(name="demo", description="demo", invocation_policy=policy)


@pytest.mark.parametrize(
    ("policy", "user_visible", "model_visible"),
    [
        (InvocationPolicy.AUTO, True, True),
        (InvocationPolicy.USER_ONLY, True, False),
        (InvocationPolicy.MODEL_ONLY, False, True),
        (InvocationPolicy.DISABLED, False, False),
    ],
)
def test_visibility_by_policy(
    policy: InvocationPolicy, user_visible: bool, model_visible: bool
) -> None:
    skill = _skill(policy)

    assert is_user_visible(skill) is user_visible
    assert is_model_visible(skill) is model_visible


def test_environment_filter_keeps_policy_decisions_to_visibility() -> None:
    skills = [
        _skill(InvocationPolicy.AUTO),
        _skill(InvocationPolicy.USER_ONLY),
        _skill(InvocationPolicy.MODEL_ONLY),
        _skill(InvocationPolicy.DISABLED),
    ]

    assert filter_eligible(skills) == skills


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, InvocationPolicy.AUTO),
        ({"invocation_policy": "model_only"}, InvocationPolicy.MODEL_ONLY),
        ({"invocation_policy": "user_only"}, InvocationPolicy.USER_ONLY),
        ({"invocation_policy": "nonsense"}, InvocationPolicy.DISABLED),
        ({"user-invocable": False}, InvocationPolicy.MODEL_ONLY),
    ],
)
def test_expert_command_pseudo_manifest_reuses_policy_resolution(
    metadata: dict[str, object], expected: InvocationPolicy
) -> None:
    """命令合成伪 manifest 必须复用统一解析，不能把 model_only 改写成 auto。"""
    from prismclaw.engine.execution.capabilities.assembly import (
        _pseudo_skill_manifest_for_command,
    )
    from prismclaw.schema.agent.capability import ExpertCommandCapability

    manifest = _pseudo_skill_manifest_for_command(
        ExpertCommandCapability(
            name="draft", description="draft", body="body", metadata=dict(metadata)
        ),
        expert_name="writer",
    )

    assert manifest.invocation_policy is expected
