"""运行时指令注入：块形状、幂等分隔符、失败不置标志。"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.execution.loop.stream import _SOFT_LANDING_PROMPT, _append_system_directive


def _state(system):
    packed = SimpleNamespace(system=system)
    preflight = SimpleNamespace(packed_request=packed)
    return SimpleNamespace(prepared_turn=SimpleNamespace(preflight=preflight))


def test_str_system_appends_single_separator():
    state = _state("base prompt\n\n")

    assert _append_system_directive(state, "  directive  ", reason="r") is True

    assert state.prepared_turn.preflight.packed_request.system == "base prompt\n\ndirective"


def test_list_system_appends_text_block_not_message():
    state = _state([{"type": "text", "text": "base"}])

    assert _append_system_directive(state, "directive", reason="r") is True

    blocks = state.prepared_turn.preflight.packed_request.system
    assert blocks[0] == {"type": "text", "text": "base"}
    assert blocks[1] == {"type": "text", "text": "directive"}
    assert all("role" not in block for block in blocks)


def test_soft_landing_prompt_is_appended_verbatim():
    state = _state("base")

    assert _append_system_directive(
        state, _SOFT_LANDING_PROMPT, reason="soft_landing", remaining_hard_s="12.0",
    ) is True

    assert state.prepared_turn.preflight.packed_request.system.endswith(
        _SOFT_LANDING_PROMPT.strip()
    )


def test_missing_system_returns_false_so_flag_stays_unset():
    warned = _append_system_directive(_state(None), "directive", reason="r")

    assert warned is False


def test_missing_prepared_turn_returns_false():
    state = SimpleNamespace(prepared_turn=None)

    assert _append_system_directive(state, "directive", reason="r") is False


def test_empty_directive_returns_false():
    state = _state("base")

    assert _append_system_directive(state, "   ", reason="r") is False
    assert state.prepared_turn.preflight.packed_request.system == "base"
