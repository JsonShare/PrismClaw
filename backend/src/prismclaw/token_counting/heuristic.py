"""Local heuristic token counter (default provider).

Coefficients are calibrated against production tokenizers. They live here —
not at call sites — so the whole codebase shares one definition and tuning a
provider is a single config change.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

# 默认校准值（可被 token_counting.heuristic.* 覆盖）。
# 2026-11-13 glm-53 drift 复核：CJK 0.90（中文模型 CJK≈0.9-1.0 tok/char），
# ASCII 3.4（英文散文≈5.3 字符/token、JSON≈3.2 字符/token 的中间值）。
CJK_TOKENS_PER_CHAR = 0.90
OTHER_CHARS_PER_TOKEN = 3.4
# 工具 schema JSON 的 UTF-8 字节/token（生产 drift sentinel 实测校准）。
WIRE_BYTES_PER_TOKEN = 3.5
# 通用请求 JSON 的 UTF-8 字节/token（对应旧实现的 //3）。
JSON_BYTES_PER_TOKEN = 3.0
# 单条消息固定信封开销（role/content/JSON 括号）。
MESSAGE_ENVELOPE_TOKENS = 8
# 单个 tool_call 的 id/type/function 信封开销。
TOOL_CALL_ENVELOPE_TOKENS = 5

_CJK_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]"
)

_ESTIMATOR_V2_ENV = "PRISMCLAW_COMPACTION_ESTIMATOR_V2"


def _estimator_v2_enabled() -> bool:
    """Rollback switch: ``false`` restores the legacy ``len(text) // 4`` path."""
    raw = os.environ.get(_ESTIMATOR_V2_ENV, "true")
    return str(raw).strip().lower() not in ("false", "0", "no")


class HeuristicTokenCounter:
    """CJK-weighted/byte-weighted local estimator.

    This is the behavior the codebase shipped before the plugin extraction;
    defaults preserve it exactly.
    """

    name = "heuristic"

    def __init__(
        self,
        *,
        cjk_tokens_per_char: float = CJK_TOKENS_PER_CHAR,
        other_chars_per_token: float = OTHER_CHARS_PER_TOKEN,
        wire_bytes_per_token: float = WIRE_BYTES_PER_TOKEN,
        json_bytes_per_token: float = JSON_BYTES_PER_TOKEN,
        message_envelope_tokens: int = MESSAGE_ENVELOPE_TOKENS,
        tool_call_envelope_tokens: int = TOOL_CALL_ENVELOPE_TOKENS,
    ) -> None:
        self.cjk_tokens_per_char = float(cjk_tokens_per_char)
        self.other_chars_per_token = float(other_chars_per_token)
        self.wire_bytes_per_token = float(wire_bytes_per_token)
        self.json_bytes_per_token = float(json_bytes_per_token)
        self.message_envelope_tokens = int(message_envelope_tokens)
        self.tool_call_envelope_tokens = int(tool_call_envelope_tokens)

    def count_text(
        self,
        text: str,
        *,
        image_tokens_each: int = 0,
        image_placeholder: str = "",
    ) -> int:
        if not text:
            return 0
        if image_tokens_each > 0 and image_placeholder:
            image_count = text.count(image_placeholder)
            if image_count:
                remainder = text.replace(image_placeholder, "")
                return self._count_plain(remainder) + image_count * int(image_tokens_each)
        return self._count_plain(text)

    def _count_plain(self, text: str) -> int:
        if not text:
            return 0
        if not _estimator_v2_enabled():
            return max(1, len(text) // 4)
        # sub() runs in C: cjk = len − len(after removing CJK chars)
        cjk = len(text) - len(_CJK_RE.sub("", text))
        other = len(text) - cjk
        return max(
            1,
            int(cjk * self.cjk_tokens_per_char + other / self.other_chars_per_token),
        )

    def count_message(
        self,
        msg: Mapping[str, Any],
        *,
        image_tokens_each: int = 0,
    ) -> int:
        total = self.message_envelope_tokens
        content = msg.get("content")
        if isinstance(content, str):
            total += self.count_text(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in ("image_url", "image"):
                    total += int(image_tokens_each)
                else:
                    text = block.get("text") or ""
                    if isinstance(text, str):
                        total += self.count_text(text)
                    # Anthropic-style tool_use input JSON.
                    tool_input = block.get("input")
                    if isinstance(tool_input, dict):
                        total += self.count_text(json.dumps(tool_input, ensure_ascii=False))
        # name field (optional, e.g. named tool outputs)
        name = msg.get("name")
        if isinstance(name, str) and name:
            total += self.count_text(name)
        # tool_call_id on role="tool" messages.
        tool_call_id = msg.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            total += self.count_text(tool_call_id)
        # assistant tool_calls: count id/type/function envelope + name/arguments.
        for call in msg.get("tool_calls") or []:
            fn = (call or {}).get("function") or {}
            call_id = (call or {}).get("id") or ""
            call_name = fn.get("name") or ""
            args = fn.get("arguments")
            total += self.count_text(call_id) + self.tool_call_envelope_tokens
            if isinstance(args, str):
                total += self.count_text(call_name) + self.count_text(args)
            elif isinstance(args, dict):
                total += self.count_text(call_name) + self.count_text(str(args))
        return total

    def count_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        image_tokens_each: int = 0,
    ) -> int:
        return sum(
            self.count_message(message, image_tokens_each=image_tokens_each)
            for message in messages
        )

    def count_tool_schemas(self, schemas: Sequence[Mapping[str, Any]]) -> int:
        if not schemas:
            return 0
        canonical = json.dumps(
            list(schemas),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return self.count_bytes(len(canonical.encode("utf-8")), kind="wire")

    def count_bytes(
        self,
        n_bytes: int,
        *,
        kind: Literal["json", "wire"] = "json",
    ) -> int:
        if n_bytes <= 0:
            return 0
        divisor = self.wire_bytes_per_token if kind == "wire" else self.json_bytes_per_token
        return int(n_bytes // divisor)
