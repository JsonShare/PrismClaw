"""Session custom entry 契约：``CustomType`` 的每个取值都必须可被写入与解析。

历史缺陷：新增 ``parallel_task_runtime`` / ``run_takeover_state`` 写入时
未同步 ``CustomType``，pydantic 直接拒绝整条记录，生产写入必然失败。
"""
from __future__ import annotations

from typing import get_args

import pytest

from prismclaw.schema.session.types import CustomEntry, CustomPayload, CustomType

_CUSTOM_TYPES = get_args(CustomType)


@pytest.mark.parametrize("custom_type", _CUSTOM_TYPES)
def test_every_custom_type_is_accepted_by_custom_entry(custom_type: str) -> None:
    entry = CustomEntry(
        id="e1",
        parent_id=None,
        custom_type=custom_type,
        data={},
    )
    assert entry.custom_type == custom_type


def test_every_custom_type_has_a_payload_branch() -> None:
    members = get_args(get_args(CustomPayload)[0])
    covered = {
        get_args(member.model_fields["custom_type"].annotation)[0]
        for member in members
    }
    assert covered == set(_CUSTOM_TYPES)
