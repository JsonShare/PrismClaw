"""插件激活的会话粘性与替换语义（对应 §3.10 / R2 / R4 / R5）。

这些断言是同一机制的正反两面：粘性载体必须是快照里持久化的
``_plugin:<name>`` 记录，而不是 active_invocation.plugin_name（后者在
「只 @ 了某插件下的技能」时也会被写成插件名，用它做粘性会越权解锁）。
"""
from __future__ import annotations

from prismclaw.engine.execution.checkpoint.models import (
    InvokedSkillRecord,
    InvokedSkillState,
)
from prismclaw.engine.execution.loop.preparer import RunPreparer
from prismclaw.schema.services.skills import SkillInvocationState

_merge = RunPreparer._merge_invocations


def _plugin_state(name: str) -> SkillInvocationState:
    return SkillInvocationState(
        skill_name=f"_plugin:{name}",
        is_active=True,
        clear_after_terminal_result=False,
        plugin_name=name,
        invocation_context="skill",
    )


def _skill_state(name: str, plugin: str | None) -> SkillInvocationState:
    return SkillInvocationState(
        skill_name=name,
        is_active=True,
        clear_after_terminal_result=True,
        plugin_name=plugin,
        invocation_context="skill",
    )


def _scope_of_turn(chips: list[str], snapshot: InvokedSkillState) -> frozenset[str]:
    """复刻 preparer 的 scope 解析：有 chip 替换，无 chip 沿用上一轮。"""
    if chips:
        return frozenset(dict.fromkeys(c for c in chips if c))
    return frozenset(
        rec.skill_name.removeprefix("_plugin:")
        for rec in snapshot.skills.values()
        if rec.skill_name.startswith("_plugin:")
    )


def _record(name: str) -> InvokedSkillRecord:
    return InvokedSkillRecord(
        skill_name=f"_plugin:{name}",
        rendered_prompt_fragment="x",
        clear_after_terminal_result=False,
    )


# ── R2：prompt 不得出现 _plugin: ────────────────────────────────────────


def test_plugin_only_merge_does_not_leak_pseudo_skill_name():
    merged = _merge(None, [_plugin_state("legal")])
    assert merged is not None
    assert "_plugin:" not in merged.skill_name


def test_real_skill_name_kept_in_merge():
    merged = _merge(
        None,
        [_plugin_state("legal"), _skill_state("contract-review", "legal")],
    )
    assert merged is not None
    assert merged.skill_name == "contract-review"


# ── R4：粘性 ───────────────────────────────────────────────────────────


def test_sticky_scope_reuses_previous_plugin():
    snapshot = InvokedSkillState(skills={"_plugin:legal": _record("legal")})
    assert _scope_of_turn([], snapshot) == frozenset({"legal"})


def test_explicit_chip_replaces_previous_plugin():
    snapshot = InvokedSkillState(skills={"_plugin:legal": _record("legal")})
    assert _scope_of_turn(["fin"], snapshot) == frozenset({"fin"})


# ── R5：技能激活不得升级为整插件解锁（越权）────────────────────────────


def test_skill_only_activation_does_not_unlock_plugin():
    """只有真实技能被激活时，快照里没有 _plugin: 记录 → scope 为空。"""
    snapshot = InvokedSkillState(
        skills={"contract-review": InvokedSkillRecord(
            skill_name="contract-review",
            rendered_prompt_fragment="x",
            clear_after_terminal_result=True,
        )}
    )
    assert _scope_of_turn([], snapshot) == frozenset()
