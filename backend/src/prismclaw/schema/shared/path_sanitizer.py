"""服务器内部路径脱敏——用户可见文本里的宿主绝对路径替换。

LLM 在工具执行过程中会看到宿主真实路径（bash 输出、工具结果的物理落点、
文件报错里的绝对路径等），然后在自然语言回答、工具执行详情（ExecutionTimeline）
里暴露出来。这些实现细节对用户没有意义，且在多实例/迁移场景下容易失效。

本模块提供 ``redact_server_paths`` 这一个公开 API——在所有用户可见出口
（WebSocket 实时正文、ExecutionTimeline 的 args_text/output/error、
历史回放的正文与 tool_result）统一调用，保证路径暴露零出口。

设计约束：
    - 只匹配**宿主绝对路径**，保留虚拟路径前缀（outputs/、project/、inbox/ 等）
    - 纯文本变换，不修改原始 LLM 内部链路（执行引擎状态、LLM 上下文里的路径不受影响）
    - 幂等：替换结果 ``[内部路径]`` 不会被后续再次匹配
"""

from __future__ import annotations

import re

__all__ = ["redact_server_paths", "split_stream_safe", "StreamingPathRedactor"]


# ── 模式定义 ──────────────────────────────────────────────────────────
#
# 覆盖场景（来自实际用户可见回答的暴露点）：
#   1. /tmp/xxx       — 临时文件（批处理、渲染中间件）
#   2. ~/.prismclaw/xxx — PrismClaw 用户配置、插件、skills 目录
#   3. ~/.config/xxx    — 通用用户配置目录
#   4. /Users/xxx/xxx   — macOS 宿主用户路径
#   5. /home/xxx/xxx    — Linux 宿主用户路径
#   6. /var/xxx, /etc/xxx, /opt/xxx, /srv/xxx, /run/xxx — 系统路径
#   7. sessions/<sid>/.work/xxx — 会话私有工作区内部文件
#
# 不匹配（保留）：
#   - outputs/xxx、project/xxx、inbox/xxx — 虚拟路径前缀
#   - 纯文件名（无 / 或只有相对路径）
#   - 代码块里的 URL（http://、https://）

# 宿主绝对路径：/tmp/、/var/、/etc/、/opt/、/srv/、/run/、/proc/、/sys/、/dev/
_SYS_ABS_PATH_RE = re.compile(
    r"""(?<![A-Za-z0-9._/-])          # 前面不能是路径字符（避免误伤拼接）
        (
          /tmp/|/var/|/etc/|/opt/|/srv/|/run/|/proc/|/sys/|/dev/
        )
        ([\w.\-@]+/)*[\w.\-@]+         # 路径主体（允许多级目录 + 文件名）
      """,
    re.VERBOSE,
)

# 用户 home 路径：~/.xxx、/Users/xxx/、/home/xxx/
_HOME_PATH_RE = re.compile(
    r"""(
          ~/\.[\w.\-@/]+              # ~/.prismclaw/... 或 ~/.config/...
        |
          /Users/[\w.\-@]+/[\w.\-@/]+  # /Users/name/...
        |
          /home/[\w.\-@]+/[\w.\-@/]+   # /home/name/...
      )""",
    re.VERBOSE,
)

# 会话私有工作区内部：sessions/<sid>/.work/xxx
_SESSION_PRIVATE_PATH_RE = re.compile(
    r"""sessions/[\w#:.\-]+/\.work/[\w.\-@/]+""",
    re.VERBOSE,
)

_REPLACEMENT = "[内部路径]"


def redact_server_paths(text: str) -> str:
    """把服务器内部路径替换为 ``[内部路径]``。

    纯文本变换——不触碰执行引擎内部状态、不修改 LLM 上下文里的原始路径，
    只在推送给前端（WebSocket、历史回放）前过滤用户可见文本。

    Args:
        text: 待过滤文本（可以是 None 或空串，安全返回原值/空串）

    Returns:
        过滤后的文本；若输入为 None 返回空串
    """
    if not text:
        return text or ""
    out = text
    out = _HOME_PATH_RE.sub(_REPLACEMENT, out)
    out = _SYS_ABS_PATH_RE.sub(_REPLACEMENT, out)
    out = _SESSION_PRIVATE_PATH_RE.sub(_REPLACEMENT, out)
    return out


# ── 流式跨分片脱敏 ────────────────────────────────────────────────────
#
# 问题：实时推送按 LLM 的 token 分片，一个绝对路径可能被切成两片。
# 若对每片单独 redact，前片里的 ``/tmp/g`` 会被替换成 ``[内部路径]``，
# 后片 ``z_pages/doctor1.html`` 不再以绝对路径开头，于是原样漏出——
# 用户实时看到 ``[内部路径]z_pages/doctor1.html``。
#
# 解法：把「可能是路径中段」的尾部滞留到下一片再一起处理，也就是延迟
# 推送这一小段。判定标准是尾部连续路径字符段里出现 ``/`` 或 ``~``。

# 路径候选字符：\w 含 Unicode 字母（中文不会误伤，因为不含 / 或 ~ 就不滞留）
_PATH_CANDIDATE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._-@/~"
)
# 滞留上限：超过则放弃保护直接推送，避免长串路径把正文无限延迟。
# 取值需大于最长可能的不完整路径片段（含替换串 [内部路径] 本身）。
MAX_STREAM_HOLDBACK = 256


def split_stream_safe(buffer: str) -> tuple[str, str]:
    """把流式缓冲区切成 (可安全推送部分, 需滞留的尾部)。

    滞留条件是尾部连续路径字符段里含 ``/`` 或 ``~``——它可能是被分片切断的
    绝对路径中段，必须等下一片拼接后再脱敏。其余情况整段可推。

    Args:
        buffer: 到目前为止累积但尚未推送的文本

    Returns:
        (safe, holdback)：safe 可立即脱敏推送；holdback 留给下次拼接
    """
    if not buffer:
        return "", ""
    limit = max(0, len(buffer) - MAX_STREAM_HOLDBACK)
    idx = len(buffer)
    while idx > limit and buffer[idx - 1] in _PATH_CANDIDATE_CHARS:
        idx -= 1
    candidate = buffer[idx:]
    if candidate and ("/" in candidate or candidate.startswith("~")):
        return buffer[:idx], candidate
    return buffer, ""


class StreamingPathRedactor:
    """有状态的流式路径脱敏器：跨分片拼接后再替换，避免路径被切断。

    用法：每个 TextChunk 调 :meth:`feed`，把返回值推给前端；本轮结束时调
    :meth:`flush` 取回滞留的尾巴（同样需要推送并计入正文，否则正文缺结尾）。
    """

    __slots__ = ("_buffer",)

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> str:
        """累积新分片，返回本次可安全推送的已脱敏文本（可能为空串）。"""
        self._buffer = f"{self._buffer}{text or ''}"
        safe, holdback = split_stream_safe(self._buffer)
        self._buffer = holdback
        return redact_server_paths(safe)

    def flush(self) -> str:
        """本轮结束：吐出滞留部分（已脱敏）。调用后状态清空。"""
        pending, self._buffer = self._buffer, ""
        return redact_server_paths(pending)
