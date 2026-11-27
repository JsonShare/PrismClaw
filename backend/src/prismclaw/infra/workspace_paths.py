"""工作区目录的单一权威源。

布局：

  {workspace_base}/web_{user_id}/
  ├── biz_domain/<domain>/                # 固定业务域（与工作区同级，跨工作区共享）
  │   ├── inbox/<record_id>/              # 业务原件输入
  │   ├── outbox/<record_id>/             # 业务产出
  │   └── .work/                          # 业务域私有临时区
  └── <workspace_id>/                     # 工作区（default 亦落成 default/）
      ├── <用户项目文件>                   # 用户项目区（project/ 前缀直达原件）
      ├── sessions/<session_id>/          # 对话任务（跑 agent loop）
      │   ├── inbox/                      # session 私有输入
      │   ├── outbox/                     # session 私有产出（本 session 读写）
      │   ├── context_blocks/             # session prompt 组装归档（本 session 只读）
      │   │   └── images/                 # 文档抽取出的图片（7 天 TTL，删会话即回收）
      │   ├── tool_results/               # session 大 tool result 归档（本 session 读写）
      │   └── .work/                      # session 私有系统区
      │       ├── tmp/                    # 临时文件
      │       └── cache/ocr/              # 会话内文件（inbox/ 图片等）的 OCR 缓存
      └── .work/                          # workspace 系统区（引擎私有，用户不触碰）
          ├── state/                      # 删了丢功能：configs/ skills/
          ├── audit/                      # 删了丢撤销：edits/{blobs,log.jsonl,review_events.jsonl}
          ├── cache/                      # 删了重算：ocr/ extracted/(旧布局) previews/ projects/
          └── run/                        # 删了无所谓：tmp/ spills/ jobs/

两类任务的区别（见 SDD 5.2.8.1）：
  * 对话任务跑 agent loop，需要 context_blocks/tool_results/.work 等引擎机制目录；
  * 业务域是纯 Python 编排调用 LLM，不跑 agent loop，只有业务语义的
    inbox / outbox（+ 预留的 .work）几组目录。

业务域**不带 workspace_id**：它是用户级数据，与工作区正交 —— 同一用户的
业务数据在任何工作区里都指向同一份原件，避免多工作区之间的文件复制。

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
    "USER_DIR_PREFIX",
    "USER_DIR_GLOB",
    "WORK_DIR",
    "WORK_STATE",
    "WORK_AUDIT",
    "WORK_CACHE",
    "WORK_RUN",
    "work_dir",
    "resolve_user_root",
    "resolve_workspace_dir",
    "resolve_biz_domain_dir",
    "resolve_session_dir",
    "session_dir_in",
    "session_dir_name",
    "normalize_session_rel_path",
    "SESSION_SUBDIRS",
    "ensure_session_layout",
    "resolve_session_inbox_dir",
]

_DEFAULT_WORKSPACE_ID = "default"

# 工作区系统区四组（判定依据只有一条：删了会怎样）。
#   state/ 删了丢功能 | audit/ 删了丢撤销 | cache/ 删了重算 | run/ 删了无所谓
# 组名变更必须同步 ``engine/toolkit/builtin/bash.py`` 里的 shell 字符串。
WORK_DIR = ".work"
WORK_STATE = "state"
WORK_AUDIT = "audit"
WORK_CACHE = "cache"
WORK_RUN = "run"

# 用户级目录名前缀。路径拼接一律走 :func:`resolve_user_root`；
# 需要按布局反查路径时用下面两个 glob 模式，避免各处硬编码 ``web_*``。
USER_DIR_PREFIX = "web_"
USER_DIR_GLOB = f"{USER_DIR_PREFIX}*"

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
# workspace 系统区（.work 四组）
# ---------------------------------------------------------------------------


def work_dir(workspace_root: Path, *parts: str) -> Path:
    """工作区系统区路径：``<ws>/.work/<parts...>``（不创建）。

    **这是 ``.work/`` 唯一的 Python 侧拼点**：引擎状态、审计、缓存、运行时
    产物一律经由它派生，避免 ``root / ".work" / ...`` 在多处散落。
    """
    return Path(workspace_root).joinpath(WORK_DIR, *parts)


# ---------------------------------------------------------------------------
# 用户级
# ---------------------------------------------------------------------------


def resolve_user_root(user_id: str, *, base: Path | None = None) -> Path:
    """用户根目录：``{workspace_base}/web_<user_id>/``（不创建）。

    **这是 ``web_`` 前缀唯一的拼接点**。调用方一律用它推导用户级路径，
    不要自己写 ``f"web_{user_id}"`` —— 前缀一旦改动（或需要校验用户 id
    合法性），分散拼接会漏改，且拼错时不会报错、只会静默落到另一个目录。

    ``base`` 供已经持有 workspace 根（装配期快照 / 测试注入）的调用方传入，
    避免与 :func:`load_settings` 产生第二个来源；缺省读 settings。
    """
    workspace_base = base if base is not None else Path(load_settings().workspaces.default)
    return (
        Path(workspace_base).expanduser()
        / f"{USER_DIR_PREFIX}{_safe_segment(user_id, label='user_id')}"
    )


# ---------------------------------------------------------------------------
# workspace 级（跨 session 共享）
# ---------------------------------------------------------------------------


def resolve_workspace_dir(user_id: str, workspace_id: str = _DEFAULT_WORKSPACE_ID) -> Path:
    """返回该用户指定工作区的根目录（不创建）。"""
    if workspace_id == _DEFAULT_WORKSPACE_ID:
        return resolve_user_root(user_id) / "default"
    return resolve_user_root(user_id) / _safe_segment(workspace_id, label="workspace_id")


def resolve_biz_domain_dir(user_id: str, domain: str) -> Path:
    """业务域根目录：{user_root}/biz_domain/<domain>/（不创建）。

    **与工作区同级、不带 workspace_id**：业务域数据跨工作区共享，
    同一用户的业务数据在任何工作区里都指向同一份原件。

    不含 record_id —— 由调用方按 ``inbox/<record_id>`` 或
    ``outbox/<record_id>`` 自行拼接。业务域的路径全部由业务代码计算，
    模型不参与决定落点，因此记录级隔离靠这一层拼接而非引擎边界。
    """
    return resolve_user_root(user_id) / "biz_domain" / _safe_segment(domain, label="domain")


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


def session_dir_name(session_id: str) -> str:
    """会话 ID → 磁盘目录名的**唯一权威规则**。

    session_id 是语义标识（形如 ``web:admin:default:s:abc123``，subagent 为
    ``parent#sub:uuid``），其中 ``:``/``#`` 不是合法路径字符，统一折叠为 ``-``。

    这是整个系统里唯一知道"语义 ID 如何落盘"的地方：``session_dir_in``、
    ``ensure_session_layout`` 以及 Web 文件路由的入参归一化都经由它，
    避免前端自行拼接导致两侧规则漂移（曾造成会话 inbox/outbox 恒 404）。
    """
    return _safe_segment(
        session_id.replace("#", "-").replace(":", "-"), label="session_id"
    )


def normalize_session_rel_path(rel_path: str) -> str:
    """把工作区相对路径中的 ``sessions/<原始 session_id>`` 段归一化为磁盘目录名。

    入参来自 HTTP 层（前端传语义 session_id），出参是可直接落盘的相对路径。
    幂等：已是目录名的路径原样返回。非 ``sessions/`` 开头的路径不受影响。

    只重写第一段为 ``sessions`` 时的第二段；更深层结构（``.work``/``outbox``
    等）保持不变，交给通用越界校验。
    """
    parts = rel_path.split("/")
    if len(parts) < 2 or parts[0] != "sessions" or not parts[1]:
        return rel_path
    return "/".join([parts[0], session_dir_name(parts[1]), *parts[2:]])


def session_dir_in(workspace_root: Path, session_id: str) -> Path:
    """从已知工作区根推导会话目录：{ws}/sessions/<dir>/。

    引擎侧入口（ToolContext.workspace_path 已是工作区根，无需再经
    user_id/workspace_id 解析）。目录名由 :func:`session_dir_name` 统一决定。
    """
    return workspace_root / "sessions" / session_dir_name(session_id)


#: 会话目录的完整子目录布局（与 workspace_paths 顶部文档一致）。
SESSION_SUBDIRS: tuple[str, ...] = (
    "inbox",
    "outbox",
    "context_blocks",
    "context_blocks/images",
    "tool_results",
    ".work/tmp",
    ".work/cache/ocr",
)


def ensure_session_layout(workspace_root: Path, session_id: str) -> Path:
    """幂等创建会话目录及全部标准子目录，返回会话根。

    会话私有目录按需写入时才创建会导致 ``ls inbox`` 等读取先于首次写入
    时看到“不存在”。统一在 run 启动时建好，读取方永远看到空目录。
    """
    root = session_dir_in(workspace_root, session_id)
    for sub in SESSION_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def resolve_session_inbox_dir(user_id: str, workspace_id: str, session_id: str) -> Path:
    """会话私有输入：{ws}/sessions/<sid>/inbox/。"""
    return resolve_session_dir(user_id, workspace_id, session_id) / "inbox"
