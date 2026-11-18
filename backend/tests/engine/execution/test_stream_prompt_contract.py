"""契约测试：运行时指令只能经 `_append_system_directive` 写入 system。

防止回归——历史上两个注入函数各自把 `{"role": "system"}` 追加进 system 块数组，
那是消息形状，不是文本块形状。
"""
from __future__ import annotations

from pathlib import Path

import prismclaw.engine.execution.loop.stream as stream_module

_SOURCE = Path(stream_module.__file__).read_text(encoding="utf-8")


def test_no_role_system_message_appended_to_system_blocks():
    assert '"role": "system"' not in _SOURCE
    assert "'role': 'system'" not in _SOURCE


def test_both_injection_sites_go_through_the_single_helper():
    # 1 处 def + 软着陆 / token 预算两个调用点
    assert _SOURCE.count("_append_system_directive(") >= 3


def test_system_is_only_mutated_inside_the_helper():
    # str 与 list 两个分支各一次，除此之外不得有其它直接赋值
    assert _SOURCE.count("_pr.system = ") == 2
