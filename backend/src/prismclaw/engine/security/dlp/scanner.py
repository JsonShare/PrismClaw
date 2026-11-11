"""P3-PHI-03：L1 出口 DLP 扫描器。

出站响应安全扫描：
  1. Safe Harbor 正则扫描（复用 phi_sanitizer）
  2. k-匿名：单次响应 ≥ 30 行含 PHI 标识符 → 阻断
  3. PHI 字节级阈值：响应体 PHI 占比 ≥ 30% → 阻断
  4. DLP 编码预处理：URL/Base64/QP/NFKC 解码后再扫

输入：HTTP/Proxy 响应体
输出：DlpDecision（ALLOW / MASK / BLOCK）+ 审计
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from prismclaw.schema.shared.phi_sanitizer import apply_safe_harbor, get_all_pii_patterns
from prismclaw.engine.security.dlp.sanitizer import dlp_preprocess


class DlpAction(str, Enum):
    ALLOW = "allow"     # 放行
    MASK = "mask"       # 脱敏后放行
    BLOCK = "block"     # 阻断（k-匿名 / 字节阈值 触发）


@dataclass(frozen=True)
class DlpDecision:
    """DLP 扫描决策。"""
    action: DlpAction
    sanitized_text: str
    phi_byte_ratio: float
    masked_phi_count: int
    rows_with_phi: int
    reason: str = ""
    audit_event: dict[str, Any] = field(default_factory=dict)


@dataclass
class DlpScanResult:
    """C Plane Proxy DLP 扫描结果。

    用于代理 inline 扫描请求/响应体，与 DlpDecision 解耦，
    提供代理层更简洁的 allowed/blocked 语义。
    """
    allowed: bool
    phi_count: int
    phi_ratio: float
    blocked_identifiers: list[str]
    scan_time_ms: float


class DlpScanner:
    """出站响应 DLP 扫描器。

    默认阈值：
      - k_anonymity_rows: 30 行
      - phi_byte_ratio_threshold: 0.3 (30%)
      - phi_count_threshold: 5 (单次响应 PHI 标识符数量)
    """

    def __init__(
        self,
        *,
        k_anonymity_rows: int = 30,
        phi_byte_ratio_threshold: float = 0.3,
        phi_count_threshold: int = 5,
        inbound_phi_ratio_threshold: float = 0.5,
    ) -> None:
        self._k_rows = k_anonymity_rows
        self._phi_ratio = phi_byte_ratio_threshold
        self._phi_count_threshold = phi_count_threshold
        self._inbound_phi_ratio = inbound_phi_ratio_threshold

    def scan(
        self,
        text: str,
        *,
        worm_audit: Any = None,
        session_id: str = "",
    ) -> DlpDecision:
        """执行出站响应 DLP 扫描。

        步骤：
          1. DLP 编码预处理（URL/Base64/QP/NFKC）
          2. Safe Harbor 扫描 + 逐行统计 + PHI 字节统计（单次遍历）
          3. k-匿名检查（行数 ≥ K → BLOCK）
          4. PHI 字节占比检查（≥ 30% → BLOCK）
        """
        if not text:
            return DlpDecision(
                action=DlpAction.ALLOW,
                sanitized_text=text,
                phi_byte_ratio=0.0,
                masked_phi_count=0,
                rows_with_phi=0,
            )

        # Step 1: 编码预处理
        preprocessed = dlp_preprocess(text)

        # Step 2: 单次遍历 —— 逐行扫描 + 全局 PHI 字节/计数统计
        lines = preprocessed.splitlines()
        total_lines = max(1, len(lines))

        rows_with_phi = 0
        total_phi_count = 0
        phi_chars = 0

        # 先做全局 PHI 统计（一次 finditer 遍历）
        for _phi_type, _pattern, _ in get_all_pii_patterns():
            for m in _pattern.finditer(preprocessed):
                total_phi_count += 1
                phi_chars += len(m.group())

        # 逐行判断是否含 PHI
        for line in lines:
            line_sanitized, _ = apply_safe_harbor(line)
            if line != line_sanitized:
                rows_with_phi += 1

        phi_byte_ratio = phi_chars / max(1, len(preprocessed))

        # 全局脱敏结果
        sanitized, masked = apply_safe_harbor(preprocessed)

        # 决策
        audit_event: dict[str, Any] = {
            "session_id": session_id,
            "phi_byte_ratio": round(phi_byte_ratio, 4),
            "phi_count": total_phi_count,
            "rows_with_phi": rows_with_phi,
            "total_rows": total_lines,
            "original_length": len(preprocessed),
        }

        # k-匿名阻断
        if rows_with_phi >= self._k_rows:
            if worm_audit:
                worm_audit.log(
                    session_id,
                    "phi_batch_block",
                    {**audit_event, "reason": f"k_anonymity_exceeded: rows_with_phi={rows_with_phi} >= {self._k_rows}"},
                )
            return DlpDecision(
                action=DlpAction.BLOCK,
                sanitized_text="",
                phi_byte_ratio=phi_byte_ratio,
                masked_phi_count=total_phi_count,
                rows_with_phi=rows_with_phi,
                reason=f"k_anonymity_block: {rows_with_phi} rows with PHI (threshold {self._k_rows})",
                audit_event={"event_type": "phi_batch_block", **audit_event},
            )

        # 字节阈值阻断
        if phi_byte_ratio >= self._phi_ratio:
            if worm_audit:
                worm_audit.log(
                    session_id,
                    "phi_ratio_block",
                    {**audit_event, "reason": f"phi_ratio_exceeded: {phi_byte_ratio:.2%} >= {self._phi_ratio:.0%}"},
                )
            return DlpDecision(
                action=DlpAction.BLOCK,
                sanitized_text="",
                phi_byte_ratio=phi_byte_ratio,
                masked_phi_count=total_phi_count,
                rows_with_phi=rows_with_phi,
                reason=f"phi_ratio_block: {phi_byte_ratio:.2%} PHI (threshold {self._phi_ratio:.0%})",
                audit_event={"event_type": "phi_ratio_block", **audit_event},
            )

        # 脱敏后放行
        if masked:
            if worm_audit:
                worm_audit.log(
                    session_id,
                    "phi_masked",
                    audit_event,
                )
            return DlpDecision(
                action=DlpAction.MASK,
                sanitized_text=sanitized,
                phi_byte_ratio=phi_byte_ratio,
                masked_phi_count=total_phi_count,
                rows_with_phi=rows_with_phi,
                reason="phi_masked",
                audit_event={"event_type": "phi_masked", **audit_event},
            )

        return DlpDecision(
            action=DlpAction.ALLOW,
            sanitized_text=sanitized,
            phi_byte_ratio=phi_byte_ratio,
            masked_phi_count=0,
            rows_with_phi=rows_with_phi,
        )

    def scan_for_proxy(
        self,
        body: str | bytes,
        direction: Literal["outbound", "inbound"],
    ) -> DlpScanResult:
        """C Plane Proxy inline DLP 扫描。

        用于代理在请求/响应体上执行 inline PHI 扫描。
        封装 DlpDecision 为代理层 DlpScanResult。

        Args:
            body: 请求或响应体
            direction: "outbound"（请求侧）或 "inbound"（响应侧）

        Returns:
            DlpScanResult: allowed/blocked + PHI 统计
        """
        t0 = time.monotonic()

        # 规范化为 str
        if isinstance(body, bytes):
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                text = body.decode("utf-8", errors="replace")
        else:
            text = body

        if not text:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return DlpScanResult(
                allowed=True,
                phi_count=0,
                phi_ratio=0.0,
                blocked_identifiers=[],
                scan_time_ms=round(elapsed_ms, 3),
            )

        # DLP 编码预处理
        preprocessed = dlp_preprocess(text)

        # PHI 统计
        phi_count = 0
        phi_chars = 0
        blocked_identifiers: list[str] = []

        for phi_type, pattern, _ in get_all_pii_patterns():
            matches = list(pattern.finditer(preprocessed))
            if matches:
                phi_count += len(matches)
                for m in matches:
                    phi_chars += len(m.group())
                    blocked_identifiers.append(phi_type)

        phi_ratio = phi_chars / max(1, len(preprocessed))

        # 阻断判定（按 direction 使用不同阈值）
        allowed = True

        if direction == "outbound":
            # 出站阈值：phi_ratio >= 0.3 → block
            if phi_ratio >= self._phi_ratio:
                allowed = False

            # phi_count >= 5 → block
            if phi_count >= self._phi_count_threshold:
                allowed = False

            # k-anonymity: 30+ distinct PHI identifiers → block
            distinct_types = set(blocked_identifiers)
            if len(distinct_types) >= self._k_rows:
                allowed = False
        else:
            # 入站阈值：phi_ratio >= 0.5 → block
            if phi_ratio >= self._inbound_phi_ratio:
                allowed = False

        elapsed_ms = (time.monotonic() - t0) * 1000

        return DlpScanResult(
            allowed=allowed,
            phi_count=phi_count,
            phi_ratio=round(phi_ratio, 4),
            blocked_identifiers=blocked_identifiers,
            scan_time_ms=round(elapsed_ms, 3),
        )
