"""PII/PHI 脱敏器 — 通用 PII 模式 + 域注册扩展。

核心提供通用 PII 脱敏模式（电话、邮箱、身份证、SSN 等），
域特定 PHI 模式（MRN、床号、入院日期等）由域模块通过注册表注入。

核心 API：
  - apply_safe_harbor(text) -> (sanitized_text, phi_masked: bool)
  - get_all_pii_patterns() -> 合并后的完整模式列表

性能目标：P99 ≤ 5ms/1KB 文本。
"""
from __future__ import annotations

import re
from typing import Any

from prismclaw.schema.shared._phi_pattern_registry import get_domain_phi_patterns


# ── 通用 PII 模式（域无关，始终生效） ─────────────────────────────────

_CORE_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # 1. 姓名：通用前缀（姓名/Name），医疗前缀由域 bundle 注册
    ("NAME", re.compile(
        r'(?:姓名)[：:]\s*[\u4e00-\u9fff]{2,4}'
        r'|'
        r'(?:Name)\s*[:：]\s*[A-Z][a-z]+\s+[A-Z][a-z]+',
        re.IGNORECASE
    ), "[PHI_REDACTED_NAME]"),

    # 2. SSN
    ("SSN", re.compile(
        r'(?:SSN|Social Security|社保号|身份证号|身份證號|社保卡号)[-\s:]?\d{3}[-\s]?\d{2}[-\s]?\d{4}\b'
    ), "[PHI_REDACTED_SSN]"),

    # 3. 出生日期
    ("DOB", re.compile(
        r'(?:出生日期|生日|DOB|Date of Birth|Birth Date|Birthdate)[：:]\s*'
        r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?',
        re.IGNORECASE
    ), "[PHI_REDACTED_DOB]"),

    # 4. 电话号码
    ("PHONE", re.compile(
        r'\b1[3-9]\d{9}\b'
        r'|'
        r'\b\(\d{3}\)\s*\d{3}[-.]?\d{4}\b'
        r'|'
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'
        r'|'
        r'(?:电话|手机|联系方式|Phone|Tel|Mobile)[：:]\s*[\d\-\s+]{7,20}',
        re.IGNORECASE
    ), "[PHI_REDACTED_PHONE]"),

    # 5. FAX 传真号码
    ("FAX", re.compile(
        r'(?:传真|传真号|传真号码|Fax|Fax Number|FAX)[-\s：:]*[\d\-\s+]{7,20}',
        re.IGNORECASE
    ), "[PHI_REDACTED_FAX]"),

    # 6. 邮箱地址
    ("EMAIL", re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    ), "[PHI_REDACTED_EMAIL]"),

    # 7. 地址
    ("ADDRESS", re.compile(
        r'(?:地址|住址|Address|Addr)[：:]\s*[\u4e00-\u9fff\w\s\d号楼\d号]{5,100}'
        r'|'
        r'\b\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct)\.?\s*,?\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b',
        re.IGNORECASE
    ), "[PHI_REDACTED_ADDRESS]"),

    # 8. 身份证号
    ("ID_CARD", re.compile(
        r'\b(?:身份证|身份证号|ID Card|ID Number)[：:]\s*\d{15}(\d{2}[0-9Xx])?\b'
        r'|'
        r'\b\d{17}[\dXx]\b'
    ), "[PHI_REDACTED_ID_CARD]"),

    # 9. 年龄≥89
    ("AGE_89", re.compile(
        r'(?:年龄|Age)[：:]\s*(?:9[0-9]|\d{3,})\s*(?:岁|years|yrs?)?'
        r'|'
        r'\b年龄[：:]\s*89岁?\b',
        re.IGNORECASE
    ), "[PHI_REDACTED_AGE]"),

    # 10. 车牌号
    ("PLATE", re.compile(
        r'(?:车牌号|车牌|License Plate|Plate No)[：:]\s*[\u4e00-\u9fff][A-Z][A-Z0-9]{5,6}'
        r'|'
        r'\b[\u4e00-\u9fff][A-Z][A-Z0-9]{5,6}\b'
    ), "[PHI_REDACTED_PLATE]"),

    # 11. 设备ID
    ("DEVICE", re.compile(
        r'(?:设备ID|设备编号|Device ID|Device SN|Serial Number)[：:]\s*[A-Za-z0-9\-_]{4,30}'
    ), "[PHI_REDACTED_DEVICE]"),

    # 12. IP 地址
    ("IP", re.compile(
        r'\b(?!10\.\d{1,3}\.\d{1,3}\.\d{1,3})(?!172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})'
        r'(?!192\.168\.\d{1,3}\.\d{1,3})(?!127\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?:\d{1,3}\.){3}\d{1,3}\b'
    ), "[PHI_REDACTED_IP]"),

    # 13. 生物标识
    ("BIOMETRIC", re.compile(
        r'(?:指纹|虹膜|面部识别|声纹|DNA|Fingerprint|Iris|Facial Recognition|Voiceprint|Biometric)[：:]\s*[A-Za-z0-9\-_=+/]{4,100}',
        re.IGNORECASE
    ), "[PHI_REDACTED_BIOMETRIC]"),

    # 14. 银行账号/卡号
    ("BANK_ACCOUNT", re.compile(
        r'(?:银行账号|银行卡号|账户号|卡号|Bank Account|Account Number|Card Number|IBAN|SWIFT)[-\s：:]*[\d]{8,34}'
        r'|'
        r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
        re.IGNORECASE
    ), "[PHI_REDACTED_BANK_ACCOUNT]"),

    # 15. 照片/图像 base64 嵌入
    ("PHOTO_HASH", re.compile(
        r'(?:data:image/(?:png|jpeg|jpg|gif|webp|bmp);base64,)[A-Za-z0-9+/=]{100,}',
        re.IGNORECASE
    ), "[PHI_REDACTED_PHOTO_HASH]"),
]


# ── 合并 API ──────────────────────────────────────────────────────────


def get_all_pii_patterns() -> list[tuple[str, re.Pattern[str], str]]:
    """返回核心 PII 模式 + 域注册模式的合并列表。"""
    return _CORE_PII_PATTERNS + get_domain_phi_patterns()


# ── 向后兼容 ──────────────────────────────────────────────────────────


def _get_safe_harbor_patterns() -> list[tuple[str, re.Pattern[str], str]]:
    """向后兼容：消费方通过此函数获取合并后的模式列表。"""
    return get_all_pii_patterns()


# 模块级占位（注册表运行时填充，模块加载时可能为空）。
# 消费方应优先使用 get_all_pii_patterns() / _get_safe_harbor_patterns()。
_SAFE_HARBOR_PATTERNS: list[tuple[str, re.Pattern[str], str]] = []


# ── 核心 API ──────────────────────────────────────────────────────────


def apply_safe_harbor(
    text: str,
    *,
    worm_audit: Any = None,
    session_id: str = "",
) -> tuple[str, bool]:
    """对文本执行 PII/PHI 脱敏（核心通用 + 域注册模式）。

    Args:
        text: 待脱敏文本
        worm_audit: WORM 审计写入器（可选）
        session_id: 会话 ID（用于审计日志）

    Returns:
        (sanitized_text, phi_masked): 脱敏后文本 + 是否有脱敏发生
    """
    if not text:
        return text, False

    all_patterns = get_all_pii_patterns()
    result = text
    any_masked = False
    redaction_summary: dict[str, int] = {}

    for phi_type, pattern, replacement in all_patterns:
        count = len(pattern.findall(result))
        if count > 0:
            result = pattern.sub(replacement, result)
            any_masked = True
            redaction_summary[phi_type] = count

    if any_masked and worm_audit is not None:
        worm_audit.log(
            session_id,
            "safe_harbor_applied",
            {
                "redaction_count": sum(redaction_summary.values()),
                "redaction_types": redaction_summary,
                "text_length": len(text),
                "phi_masked": True,
            },
        )

    return result, any_masked
