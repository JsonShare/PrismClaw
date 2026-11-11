"""PHI 脱敏钩子 — PRE_TOOL 钩子，仅对医疗域工具生效。

在工具执行前拦截参数，对受保护健康信息（PHI）进行脱敏处理，
防止患者身份信息泄露到非授权工具或外部服务。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..._protocols import HookHandler, HookContext, HookResult, HookOutcome

from ...config.settings import MEDICAL_TOOL_PREFIXES

logger = logging.getLogger(__name__)

# PHI 正则模式（脱敏用），replacement 为字符串或可调用对象
_PHI_PATTERNS: list[tuple[str, Any]] = [
    # 中国身份证号（18 位）
    (r"\b\d{17}[\dXx]\b", "[ID_REDACTED]"),
    # 手机号
    (r"\b1[3-9]\d{9}\b", "[PHONE_REDACTED]"),
    # 中文姓名（2-4 字，前后有上下文标识）
    (r"(?:姓名|患者|病人)[：:]\s*[\u4e00-\u9fff]{2,4}", "姓名：[NAME_REDACTED]"),
    # 病历号
    (r"(?:病历号|住院号|门诊号)[：:]\s*\S+", lambda m: m.group(0).rsplit("：", 1)[0] + "：[MRN_REDACTED]"),
]


class PHIRedactHook(HookHandler):
    """PHI 脱敏钩子 — PRE_TOOL 事件，仅对医疗域工具生效。"""

    @property
    def name(self) -> str:
        return "phi_redact"

    @property
    def event(self) -> str:
        return "pre_tool"

    async def execute(self, context: HookContext) -> HookResult:
        """执行 PHI 脱敏检查。"""
        tool_name = context.tool_name or ""

        if not any(tool_name.startswith(prefix) for prefix in MEDICAL_TOOL_PREFIXES):
            return HookResult.success()

        tool_input = context.tool_input
        if not isinstance(tool_input, dict):
            return HookResult.success()

        redacted = self._redact_phi(tool_input)
        if redacted is not None:
            return HookResult(
                outcome=HookOutcome.SUCCESS,
                updated_input=redacted,
                additional_context="[PHI脱敏] 已对工具参数中的受保护健康信息进行脱敏处理",
            )

        return HookResult.success()

    @staticmethod
    def _redact_phi(tool_input: dict[str, Any]) -> dict[str, Any] | None:
        """对工具参数字典进行 PHI 脱敏。"""
        import json

        original = json.dumps(tool_input, ensure_ascii=False)
        redacted = original

        for pattern, replacement in _PHI_PATTERNS:
            if callable(replacement):
                redacted = re.sub(pattern, replacement, redacted)
            else:
                redacted = re.sub(pattern, replacement, redacted)

        if redacted == original:
            return None

        return json.loads(redacted)
