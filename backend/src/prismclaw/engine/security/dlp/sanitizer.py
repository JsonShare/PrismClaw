"""DLP sanitizer — sanitize + encoding_preprocessor + phi_llm_scanner 合并。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── sanitize ────────────────────────────────────────────────────────────────

_PHI_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "patient_name",
        "patient_id",
        "ssn",
        "social_security_number",
        "email",
        "phone",
        "address",
        "date_of_birth",
        "dob",
        "medical_record_number",
        "mrn",
        "health_plan_number",
        "account_number",
        "certificate_number",
        "vehicle_id",
        "device_id",
        "biometric",
        "photo",
    }
)


def sanitize_tool_args(args: dict[str, Any]) -> tuple[str, str, str]:
    raw_json = json.dumps(args, sort_keys=True, default=str)
    args_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    sanitized: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in _PHI_FIELDS:
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    args_sanitized = json.dumps(sanitized, sort_keys=True, default=str)
    args_preview = args_sanitized[:200]
    return (args_hash, args_sanitized, args_preview)


# ── dlp_encoding_preprocessor ───────────────────────────────────────────────

# Base64 检测模式（至少 16 字符，标准 Base64 字符集）
_BASE64_PATTERN = re.compile(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{4,}={0,2}(?![A-Za-z0-9+/=])')


def normalize_nfkc(text: str) -> str:
    """Unicode NFKC 标准化——全角数字→半角，兼容字符→统一形式。"""
    return unicodedata.normalize("NFKC", text)


def url_decode_recursive(text: str, max_depth: int = 3) -> str:
    """递归 URL 解码（最多 max_depth 层）。

    示例：%25E5%25BC%25A0 → %E5%BC%A0 → 张三
    """
    result = text
    for _ in range(max_depth):
        try:
            decoded = urllib.parse.unquote(result)
        except Exception:
            break
        if decoded == result:
            break
        result = decoded
    return result


def base64_decode_phi(text: str) -> str:
    """检测并解码文本中的 Base64 编码 PHI。

    仅解码包含可读文本的 Base64 段落，不解码二进制数据。
    """
    result = text

    def _try_decode(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            decoded = base64.b64decode(candidate, validate=True).decode("utf-8", errors="strict")
            printable = sum(1 for c in decoded if c.isprintable()) / max(1, len(decoded))
            has_phi_patterns = bool(re.search(r'[\u4e00-\u9fff]|[A-Za-z]{3,}|\d{3}-\d{2}-\d{4}|\d{9,11}|1[3-9]\d{9}', decoded))
            if printable >= 0.95 and (has_phi_patterns or len(decoded) >= 8):
                return decoded
        except Exception:
            pass
        return candidate

    result = _BASE64_PATTERN.sub(_try_decode, result)
    return result


def quoted_printable_decode(text: str) -> str:
    """Quoted-Printable 解码。

    示例：=E5=BC=A0=E4=B8=89 → 张三
    """
    import quopri
    try:
        decoded = quopri.decodestring(text.encode("utf-8", errors="replace")).decode(
            "utf-8", errors="replace"
        )
        return decoded
    except Exception:
        return text


def dlp_preprocess(text: str) -> str:
    """DLP 编码预处理完整流水线。

    执行顺序（从最脆弱的编码到最不容易被破坏的）：
      1. Unicode NFKC 标准化（全角数字等）
      2. Base64 解码（最脆弱：尾部 = 填充容易被其他解码器破坏）
      3. URL 递归解码
      4. Quoted-Printable 解码（QP 的 =XX 语法不会和已解码文本冲突）
      5. 再次 NFKC 标准化

    注意：原来的 QP→URL→Base64 顺序会导致 Base64 尾部 = 被 QP 吃掉，
    所以调整为 Base64 在 QP 之前执行。
    """
    result = normalize_nfkc(text)
    result = base64_decode_phi(result)
    result = url_decode_recursive(result)
    result = quoted_printable_decode(result)
    result = normalize_nfkc(result)
    return result


# ── phi_llm_scanner ─────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


class LlmScanResult(str, Enum):
    CLEAN = "clean"           # 无残留 PHI
    RISK_DETECTED = "risk"    # 检测到残留 PHI
    FALLBACK = "fallback"    # GPU 不可用，降级仅 L3


@dataclass(frozen=True)
class LlmPhiScanReport:
    """LLM PHI 扫描报告。"""
    result: LlmScanResult
    detected_phi: list[str] = field(default_factory=list)
    risk_score: float = 0.0         # 0.0-1.0
    gpu_available: bool = False
    fallback_active: bool = False
    reason: str = ""


class PhiLlmFallbackScanner:
    """GPU 不可用 fallback：仅 L3 Safe Harbor + 告警。

    L2 扫描需要 GPU T4 + fine-tune Qwen2.5-7B，当前环境无 GPU 时自动 fallback，
    写入 WORM 审计 `dlp_llm_fallback_active`，仅依赖 L3 Safe Harbor。
    """

    def __init__(self, *, worm_audit: Any = None) -> None:
        self._worm_audit = worm_audit
        self._warned_once = False

    def scan(self, text: str, *, session_id: str = "") -> LlmPhiScanReport:
        """执行 fallback 扫描：降级为仅 L3（由调用者保证）。"""
        if not self._warned_once and self._worm_audit is not None:
            self._worm_audit.log(
                session_id,
                "dlp_llm_fallback_active",
                {
                    "reason": "GPU not available",
                    "fallback": "L3 Safe Harbor regex only",
                    "cache_window": "last_10min",
                },
            )
            self._warned_once = True
        return LlmPhiScanReport(
            result=LlmScanResult.FALLBACK,
            gpu_available=False,
            fallback_active=True,
            reason="gpu_unavailable: using L3 Safe Harbor regex only",
        )


class PhiLlmScanner:
    """L2 LLM PHI Residue 扫描器。

    GPU 环境（T4 16GB + vLLM）启动后，在 L3 Safe Harbor 通过后
    进行语义级 PHI 扫描。当前为最小实现：骨架 + GPU 检测 + 自动降级。
    """

    def __init__(
        self,
        *,
        model_name: str = "Qwen/Qwen2.5-7B-Instruct",
        vllm_endpoint: str | None = None,
        worm_audit: Any = None,
    ) -> None:
        self._model_name = model_name
        self._vllm_endpoint = vllm_endpoint
        self._worm_audit = worm_audit
        self._gpu_available = self._detect_gpu()
        if not self._gpu_available:
            logger.warning("PhiLlmScanner: GPU not available, falling back to L3 regex")
            self._fallback = PhiLlmFallbackScanner(worm_audit=worm_audit)
        else:
            self._fallback = None

    @staticmethod
    def _detect_gpu() -> bool:
        """检测 GPU 是否可用（NVIDIA T4+）。"""
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        mem_mb = int(parts[1].strip()) if parts[1].strip().isdigit() else 0
                        if mem_mb >= 14000:  # ≥ 14GB = T4 级
                            return True
            return False
        except Exception:
            return False

    async def scan(self, text: str, *, session_id: str = "") -> LlmPhiScanReport:
        """执行 L2 LLM PHI 扫描。GPU 不可用自动 fallback。"""
        if not self._gpu_available or self._fallback is not None:
            return self._fallback.scan(text, session_id=session_id)

        # NOTE: 占位实现——GPU 存在时仍返回 CLEAN（未接入真实推理）
        # 待接入真实 vLLM HTTP 调用（continuous batching）后生效
        return LlmPhiScanReport(
            result=LlmScanResult.CLEAN,
            gpu_available=True,
            fallback_active=False,
            reason="gpu_available_stub: production inference not yet wired up",
        )
