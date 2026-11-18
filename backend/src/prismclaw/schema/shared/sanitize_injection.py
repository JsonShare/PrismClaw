"""P3-FUS-01：L3 上下文注入前统一 Safe Harbor 脱敏。

注入点（§23.2 L3）：
  1. Fact Sheet 推送前（fact_sheet.py CustomEntry 生成后）
  2. 压缩摘要渲染前（session_projection.py _render_summary_with_source_ref）

统一入口：sanitize_before_context_injection(text) -> (sanitized_text, phi_masked)
"""
from __future__ import annotations

import logging

from prismclaw.schema.shared.phi_sanitizer import apply_safe_harbor

logger = logging.getLogger(__name__)


def sanitize_before_context_injection(
    text: str,
    *,
    session_id: str = "",
    layer: str = "L3",
    source: str = "",
) -> tuple[str, bool]:
    """LLM 上下文注入前的统一脱敏入口。

    Args:
        text: 待脱敏文本（fact_sheet / 压缩摘要 / 回读文本）
        session_id: 会话 ID
        layer: 层级标记（L1/L2/L3）
        source: 来源标记（fact_sheet / compaction_summary）

    Returns:
        (sanitized_text, phi_masked)
    """
    if not text:
        return text, False
    sanitized, masked = apply_safe_harbor(text, session_id=session_id)
    if masked:
        # 安全事件留痕：只记 layer/source/长度差，绝不记录脱敏前后文本内容（PHI 红线）。
        logger.info(
            "phi masked before context injection: layer=%s source=%s session_id=%s "
            "orig_len=%d sanitized_len=%d",
            layer, source, session_id, len(text), len(sanitized),
        )
    return sanitized, masked
