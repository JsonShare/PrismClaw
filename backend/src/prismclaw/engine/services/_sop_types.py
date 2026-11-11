"""SOP extraction data types and formatting."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    """Outcome of an SOP extraction attempt.

    `skip_reason` is set when extraction was short-circuited before the LLM
    call (disabled / no candidates / below threshold / lock held / load
    failure). It is None when the LLM was actually called, in which case
    the four counters describe what happened to the LLM's output.

    Used by both async (rotation/compaction) and sync (/extract) paths.
    The async path returns this for logging only; the sync path renders
    user-facing messages from it.
    """

    spawned: bool = False
    skip_reason: str | None = None
    llm_returned_count: int = 0
    written: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    error: str | None = None
    rejection_reasons: list[str] = field(default_factory=list)


def format_extraction_result_zh(result: ExtractionResult) -> str:
    """Render an ExtractionResult into a Chinese user-facing message.

    Each branch maps to a distinct user-observable outcome — keep this
    exhaustive so users always get clear feedback, never a generic fallback.
    """
    if result.skip_reason == "disabled":
        return "⚠️ 自我进化功能未启用。"
    if result.skip_reason == "no_candidates":
        return "💡 当前会话还没有 tool 调用。先让 bot 执行一些实际操作（bash/read/write 等），再试 /extract。"
    if result.skip_reason == "below_threshold":
        return "💡 当前会话工作量不足，再多用几次工具后再试。"
    if result.skip_reason == "lock_held":
        return "⏳ 已有学习任务在进行中，请稍后再试。"
    if result.skip_reason == "session_not_found":
        return "⚠️ 找不到当前会话，请确认是否已切换。"
    if result.skip_reason == "no_segments":
        return "⚠️ 候选数据已被压缩清理，本次无法提取。"
    if result.skip_reason in ("redis_error",):
        return "⚠️ 存储服务暂时不可用，请稍后再试。"

    if result.error:
        return f"⚠️ 学习过程出错（{result.error}），请查看后台日志。"

    if result.llm_returned_count == 0:
        return "🤔 本次会话的工作模式不够通用，没有学到新 SOP。"

    if result.written > 0:
        msg = f"✅ 学到 {result.written} 条新 SOP！"
        extras = []
        if result.skipped_duplicate > 0:
            extras.append(f"{result.skipped_duplicate} 条已存在")
        if result.skipped_invalid > 0:
            extras.append(f"{result.skipped_invalid} 条未通过质量检查")
        if extras:
            msg += "（" + "，".join(extras) + "）"
        return msg

    if result.skipped_duplicate > 0 and result.skipped_invalid == 0:
        return f"💡 识别出 {result.skipped_duplicate} 个模式，但都已学习过。"
    if result.skipped_invalid > 0 and result.skipped_duplicate == 0:
        return f"⚠️ 识别出 {result.skipped_invalid} 个候选模式，但都未通过质量检查。"

    return (
        f"💡 识别出 {result.llm_returned_count} 个候选模式："
        f"{result.skipped_duplicate} 条已存在，"
        f"{result.skipped_invalid} 条未通过检查。"
    )
