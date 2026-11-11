"""工作区目录的单一权威源。

布局：

  {workspace_base}/web_{user_id}/{workspace_id}/
  ├── inputs/                             # workspace 级共享输入（所有 session 只读）
  ├── <用户项目文件>                       # 用户项目区（project/ 前缀直达原件）
  ├── sessions/<session_id>/
  │   ├── uploads/                        # session 私有输入（本 session 只读）
  │   ├── outputs/                        # session 私有产出（本 session 读写）
  │   ├── context_blocks/                 # session prompt 组装归档（本 session 只读）
  │   ├── tool_results/                   # session 大 tool result 归档（本 session 读写）
  │   └── .work/tmp/                      # session 私有系统 tmp
  └── .work/                              # workspace 系统区（引擎私有，用户不触碰）
      ├── configs/ previews/ spills/ edits/ runtime_jobs/ bash_state/
      └── envs/ node_modules/ sandbox/ skills/ cache/

约定（有意为之）：
  * ``user_id`` 直接拼进目录名、不做重命名——认证层已保证可信。
  * ``session_id`` 是 UUID（[a-f0-9-]），直接拼进路径、天然安全。
  * 本模块只解析路径，**不创建目录**；创建由调用方按需负责。
"""
from __future__ import annotations

import re
from pathlib import Path

from prismclaw.infra.settings import load_settings

__all__ = [
    "resolve_workspace_dir",
    "resolve_workspace_inputs_dir",
    "resolve_workspace_config_dir",
    "resolve_session_dir",
    "session_dir_in",
    "resolve_session_uploads_dir",
    "resolve_session_outputs_dir",
    "resolve_session_context_blocks_dir",
    "resolve_session_tool_results_dir",
]

_DEFAULT_WORKSPACE_ID = "default"

# 安全路径段：允许 ASCII [a-zA-Z0-9_-] + 中文 \u4e00-\u9fa5
# 与 Web 层 workspace_ids.WORKSPACE_ID_ALLOWED_RE 对齐——
# 中文目录名在 macOS/Linux ext4 上完全没问题（UTF-8 路径是 POSIX 标准）。
# 注意：仍禁止空格、/、\、.、.. 等路径穿越/特殊字符。
_SAFE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$")


def _safe_segment(raw: str, *, label: str = "segment") -> str:
    """验证路径段安全。不安全直接抛——不做静默净化。"""
    if not raw or not _SAFE_SEGMENT_RE.match(raw):
        raise ValueError(f"unsafe {label}: {raw!r}")
    return raw


# ---------------------------------------------------------------------------
# workspace 级（跨 session 共享）
# ---------------------------------------------------------------------------

def resolve_workspace_dir(
    user_id: str, workspace_id: str = _DEFAULT_WORKSPACE_ID
) -> Path:
    """返回该用户指定工作区的根目录（不创建）。"""
    workspace_base = Path(load_settings().workspaces.default).expanduser()
    user_root = workspace_base / f"web_{_safe_segment(user_id, label='user_id')}"
    if workspace_id == _DEFAULT_WORKSPACE_ID:
        return user_root / "default"
    return user_root / _safe_segment(workspace_id, label="workspace_id")


def resolve_workspace_inputs_dir(
    user_id: str, workspace_id: str = _DEFAULT_WORKSPACE_ID
) -> Path:
    """workspace 级共享输入目录：{ws}/inputs/。

    用户上传的项目/数据集等共享资产放在这里，所有 session 在 sandbox 里只读可见。
    """
    return resolve_workspace_dir(user_id, workspace_id) / "inputs"


def resolve_workspace_config_dir(
    user_id: str, workspace_id: str = _DEFAULT_WORKSPACE_ID
) -> Path:
    """workspace 级配置覆盖目录：{ws}/.work/configs/。"""
    return resolve_workspace_dir(user_id, workspace_id) / ".work" / "configs"


# ---------------------------------------------------------------------------
# session 级（会话私有）
# ---------------------------------------------------------------------------

def resolve_session_dir(
    user_id: str,
    workspace_id: str,
    session_id: str,
) -> Path:
    """会话级根目录：{ws}/sessions/<session_id>/。"""
    return session_dir_in(resolve_workspace_dir(user_id, workspace_id), session_id)


def session_dir_in(workspace_root: Path, session_id: str) -> Path:
    """从已知工作区根推导会话目录：{ws}/sessions/<session_id>/。

    引擎侧入口（ToolContext.workspace_path 已是工作区根，无需再经
    user_id/workspace_id 解析）。与 ``resolve_session_dir`` 共用同一
    sanitize 规则：subagent session_id 形如 ``parent#sub:uuid``，其中
    ``#``/``:`` 不能出现在路径段里，统一替换为 ``-``。
    """
    safe_sid = session_id.replace("#", "-").replace(":", "-")
    return workspace_root / "sessions" / _safe_segment(safe_sid, label="session_id")


def resolve_session_uploads_dir(
    user_id: str, workspace_id: str, session_id: str
) -> Path:
    """会话私有输入：{ws}/sessions/<sid>/uploads/。"""
    return resolve_session_dir(user_id, workspace_id, session_id) / "uploads"


def resolve_session_outputs_dir(
    user_id: str, workspace_id: str, session_id: str
) -> Path:
    """会话私有产出：{ws}/sessions/<sid>/outputs/。"""
    return resolve_session_dir(user_id, workspace_id, session_id) / "outputs"


def resolve_session_context_blocks_dir(
    user_id: str, workspace_id: str, session_id: str
) -> Path:
    """会话 prompt 组装归档：{ws}/sessions/<sid>/context_blocks/。

    大 context block 落盘归档（Agent prompt 里会引用 "context_blocks/cb_xxx.txt"）。
    sandbox 里只读挂载。
    """
    return resolve_session_dir(user_id, workspace_id, session_id) / "context_blocks"


def resolve_session_tool_results_dir(
    user_id: str, workspace_id: str, session_id: str
) -> Path:
    """会话大 tool result 归档：{ws}/sessions/<sid>/tool_results/。

    大 tool 结果（读全文本、查大型 JSON）落盘归档，Agent 后续可引用读完整内容。
    sandbox 里 RW 挂载（写归档）。
    """
    return resolve_session_dir(user_id, workspace_id, session_id) / "tool_results"
