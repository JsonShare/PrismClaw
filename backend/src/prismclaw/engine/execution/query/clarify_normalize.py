"""AskUserQuestion 契约边界归一化（单一真相源）。

LLM 输出的 questions 常有非规范写法：省略 ``description``、把选项写成
纯字符串、或把 label 写成 ``value``/``name`` 别名。这些非规范输入必须在
**契约边界**一次性归一成前端可信任的 JSON 结构，前端只做直读渲染、不再做
形状猜测（"通过 JSON 交互，不要解析文本"）。

两个调用点共享本模块：
- ``runner.py`` 的执行中途暂停注入点（实时 ``tool.clarify_request`` 事件）；
- ``channels/web/routes.py`` 的消息序列化边界（历史重放读取已持久化的
  tool_calls.arguments）。
"""

from __future__ import annotations

from typing import Any


def _clarify_option_label(o: object) -> str:
    """从单个选项原始值中提取 label。

    兼容 LLM 常见的非规范写法：纯字符串、或把 label 写成 value/name。
    归一的单一入口，避免容错逻辑散落到各客户端。"""
    if isinstance(o, str):
        return o.strip()
    if isinstance(o, dict):
        for key in ("label", "value", "name"):
            v = o.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def normalize_clarify_questions(raw: object) -> list[dict[str, Any]]:
    """把 LLM 可能非规范的 questions 归一成前端可信任的 JSON 结构。

    - 丢弃无 question 文本 / 无 options / options 全部无 label 的问题；
    - 每个保留的选项保证为 ``{label, description}`` 对象，description 缺省为空串。

    幂等：对已归一化的输入再次调用返回等价结果。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        question = q.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        options_raw = q.get("options")
        if not isinstance(options_raw, list):
            continue
        options: list[dict[str, str]] = []
        for o in options_raw:
            label = _clarify_option_label(o)
            if not label:
                continue
            desc = o.get("description") if isinstance(o, dict) else ""
            options.append(
                {
                    "label": label,
                    "description": desc if isinstance(desc, str) else "",
                }
            )
        if not options:
            continue
        out.append(
            {
                "question": question.strip(),
                "header": q.get("header") if isinstance(q.get("header"), str) else "",
                "multi_select": bool(q.get("multi_select") or q.get("multiSelect")),
                "options": options,
            }
        )
    return out
