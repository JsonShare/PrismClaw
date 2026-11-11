"""记忆写入前安全拦截器。

在 CompositeMemoryStore.store() 内部调用，覆盖所有写入路径
（capture / tiers / compiler / 用户直接调用）。

职责：
  1. 不可见 Unicode 隐写扫描（notary 独有）
  2. 注入关键词检测（notary 独有）
  3. 凭据/PHI 检查（委托给 DlpScanner.scan()）

设计原则：
  - fail-open：任何检查异常不阻断写入（CE3）
  - 结果写入 origin_stage 后缀，不新增 MemoryEntry 字段
  - notary=None 时 store() 零开销
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── 不可见 Unicode 字符集 ──
_HIDDEN_UNICODE_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u202e\u202d]")

# ── 注入检测正则（中英文指令注入） ──
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"忽略\s*(?:上面|之前|先前|以上)?\s*(?:的\s*)?(?:指令|规则|约束)", re.I),
    re.compile(r"forget\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|rules)", re.I),
    re.compile(r"你(?:现在|从此)?(?:是|变成|扮演)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)", re.I),
    re.compile(r"system\s*:\s*(?:overwrite|inject|append|replace)", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"忽略一切限制", re.I),
    re.compile(r"(?:不用|不要|无需)(?:遵守|遵循|理会)", re.I),
    re.compile(r"假装(?:你是|成)", re.I),
    re.compile(r"pretend\s+(?:you\s+are|to\s+be)", re.I),
    re.compile(r"新(?:的)?(?:人设|角色|身份)", re.I),
    re.compile(r"sudo\s+rm", re.I),
    re.compile(r"DROP\s+TABLE", re.I),
    re.compile(r"(?:exec|eval|compile)\s*\(", re.I),
    re.compile(r"<\s*/?\s*(?:system|prompt|instructions)\s*>", re.I),
    re.compile(r"(?:\\\\n|\\\\r).*?(?:system|prompt|instruction)", re.I),
    re.compile(r"从现在起.*?(?:你|你的)(?:是|角色|身份)", re.I),
]

_INJECTION_SCORE_PER_HIT = 0.15
_INJECTION_SCORE_CAP = 1.0


@dataclass
class NotaryResult:
    """公证器检查结果。"""
    content: str             # 清除/脱敏后的内容
    injection_score: float   # 注入评分 0-1
    has_hidden_unicode: bool
    redacted_by_dlp: bool    # 是否经 DLP 脱敏


class MemoryNotary:
    """记忆写入前安全拦截器。"""

    def __init__(self, *, dlp_scanner=None) -> None:
        self._dlp_scanner = dlp_scanner

    def inspect(self, content: str) -> NotaryResult:
        """对内容执行安全检查 + 脱敏。返回修改后的内容和标记。"""
        if not content:
            return NotaryResult(
                content=content, injection_score=0.0,
                has_hidden_unicode=False, redacted_by_dlp=False,
            )

        result = content
        has_hidden_unicode = False
        redacted_by_dlp = False

        # 1. 不可见 Unicode 隐写扫描
        if _HIDDEN_UNICODE_RE.search(result):
            has_hidden_unicode = True
            result = _HIDDEN_UNICODE_RE.sub("", result)

        # 2. 注入关键词检测（用原文检测，不因脱敏而漏检）
        injection_score = self._injection_score(content)

        # 3. 凭据/PHI 检查（委托给 DlpScanner）
        if self._dlp_scanner is not None:
            try:
                dlp_decision = self._dlp_scanner.scan(result)
                if dlp_decision.action.value in ("mask", "block"):
                    result = dlp_decision.sanitized_text
                    redacted_by_dlp = True
            except Exception:
                logger.warning("DLP scan failed, skipping DLP check", exc_info=True)

        return NotaryResult(
            content=result,
            injection_score=injection_score,
            has_hidden_unicode=has_hidden_unicode,
            redacted_by_dlp=redacted_by_dlp,
        )

    @staticmethod
    def _injection_score(text: str) -> float:
        """计算注入评分。每条正则命中加 0.15，封顶 1.0。"""
        hits = sum(1 for p in _INJECTION_PATTERNS if p.search(text))
        return min(hits * _INJECTION_SCORE_PER_HIT, _INJECTION_SCORE_CAP)
