from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root, sha256hex
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext


def session_root(ctx: ToolContext) -> Path | None:
    """从 ToolContext 算 session 根目录。参数不全时返回 None（降级到 workspace 根）。"""
    sid = getattr(ctx, "session_id", "")
    uid = getattr(ctx, "user_id", None)
    wid = getattr(ctx, "workspace_id", None)
    if not sid or not uid or not wid:
        return None
    from prismclaw.infra.workspace_paths import resolve_session_dir
    return resolve_session_dir(uid, wid, sid)

# ─── Constants ────────────────────────────────────────────────────────

BASH_DEFAULT_TIMEOUT_SECONDS = 120.0
READ_WRITE_DEFAULT_TIMEOUT_SECONDS = 30.0
BASH_ABORT_GRACE_SECONDS = 2.0

# Bash 输出分级阈值（SDD R2′.1 落盘 + 按需读取）：
#   ≤ READBACK_MAX_CHARS  —— 全文读回，交由四级落盘决策（keep→persist→
#                            tmpdir→truncate）统一处理，tool_results 落盘
#                            的是完整原文；
#   >  READBACK_MAX_CHARS  —— 直接返回 head/tail + 输出文件路径引用，
#                            模型可 read 分页读回全文。
_BASH_READBACK_MAX_CHARS = 200_000
_BASH_STREAM_HEAD_CHARS = 4_000
_BASH_STREAM_TAIL_CHARS = 4_000

# SDD R2′.2：ReadTool 文本分页默认页大小（模型可显式传 limit 覆盖）。
_READ_DEFAULT_PAGE_LINES = 2000

# 二进制/Office 文件不能用文本 write/edit 生成：真 .docx/.xlsx/.pptx 是 zip 二进制、
# .pdf 是二进制流；用 utf-8 文本写入只会产出「假文件」（内容是纯文本、后缀却是 .docx），
# 前端 Office 预览按 OOXML(zip) 解析时必然失败（JSZip: "Can't find end of central
# directory"）。硬拦截，把 Agent 导向正规路径：
#   - 新建 .docx/.xlsx/.pptx → 使用已安装的 office 文件技能，或通过 bash 编程生成
#   - 修改已存在的 office 文件 → read 工具读取 + bash 脚本编程修改
_TEXT_WRITE_BLOCKED_EXTENSIONS = (".docx", ".xlsx", ".pptx", ".pdf", ".doc", ".xls", ".ppt")


# ─── Binary write guard ──────────────────────────────────────────────

def _blocked_binary_extension(path: str) -> str | None:
    """返回被拦截的二进制/Office 后缀（小写，含点），否则 None。"""
    lowered = path.lower()
    for ext in _TEXT_WRITE_BLOCKED_EXTENSIONS:
        if lowered.endswith(ext):
            return ext
    return None


def _binary_write_guidance(verb: str, ext: str) -> str:
    """统一的拦截错误文案，指向办公技能或通用编程方式。"""
    return (
        f"{verb}：拒绝把纯文本写入 '{ext}' 文件。真正的 "
        f"{ext} 是二进制文档（Office 文件采用 zip/OOXML 结构），在此写入 "
        f"UTF-8 文本会生成损坏的文件，既无法打开也无法预览。正确做法：\n"
        f"  • 新建 .docx/.xlsx/.pptx：使用已安装的办公文件技能"
        f"（如 docx、pptx、excel-analysis），或通过 bash 工具以编程方式生成。\n"
        f"  • 修改已有的 .docx/.xlsx/.pptx：先用 read 工具读取，"
        f"再通过 bash 或 Python 脚本以编程方式编辑。\n"
        f"  • 若只需要纯文本/Markdown 交付物，请改用文本后缀（.md/.txt）写入。"
    )


# ─── Aliases ──────────────────────────────────────────────────────────

_ws_root = ws_root
_ws_kind = ws_kind
_require_runtime = require_runtime
_sha256hex = sha256hex


# ─── File change review (文件变更审阅) ──────────────────────────────
# EditTool / WriteTool 在成功写入后计算 旧→新 的行级 diff，构造与前端
# frontend/src/types.ts 中 FileChange 一致的结构，并挂到 ToolResult.ui_artifact。
# 该 UI artifact 不会进入 LLM 上下文，只经 chat.py 聚合后通过
# event:file_changes 下发给前端的审阅面板，做到「编辑可见、可审阅」。

_KIND_BY_EXT: dict[str, str] = {
    # code
    ".py": "code", ".js": "code", ".ts": "code", ".tsx": "code", ".jsx": "code",
    ".java": "code", ".go": "code", ".rs": "code", ".c": "code", ".cpp": "code",
    ".h": "code", ".hpp": "code", ".cs": "code", ".rb": "code", ".php": "code",
    ".sh": "code", ".sql": "code", ".html": "code", ".css": "code", ".scss": "code",
    ".less": "code", ".vue": "code", ".swift": "code", ".kt": "code", ".m": "code",
    ".r": "code", ".lua": "code", ".pl": "code", ".scala": "code", ".dart": "code",
    # config
    ".json": "config", ".yaml": "config", ".yml": "config", ".toml": "config",
    ".ini": "config", ".cfg": "config", ".conf": "config", ".env": "config",
    ".xml": "config", ".properties": "config", ".lock": "config",
    # text
    ".md": "text", ".txt": "text", ".log": "text", ".csv": "text", ".rst": "text",
    ".tex": "text", ".adoc": "text",
}


def _infer_file_change_kind(path: str) -> str:
    return _KIND_BY_EXT.get(Path(path).suffix.lower(), "text")


_MAX_HUNKS = 200          # 超过此数截断 equal hunks
_MAX_EQUAL_LINES = 3       # 截断时每个 equal hunk 保留的上下文行数
_MAX_TOTAL_LINES = 5000    # 超过此总行数强制截断


def build_file_change(
    path: str,
    old_text: str,
    new_text: str,
    *,
    before_ref: str = "",
    before_sha: str = "",
) -> dict[str, Any]:
    """Construct a FileChange payload matching the frontend FileChange model.

    `old_text` / `new_text` are the file content before/after the write. For a
    newly created file pass old_text="". Returns a dict with `hunks` using the
    same `{type: 'equal'|'insert'|'delete', lines}` shape the frontend renders.

    `before_ref` is the blob path (relative under .work/edits/blobs/) for
    the before-content backup — enables frontend Reject All → API revert.
    `before_sha` is the SHA-256 hex digest of before-content for integrity checks.
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    raw_hunks: list[dict[str, Any]] = []
    insertions = 0
    deletions = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            raw_hunks.append({"type": "equal", "lines": old_lines[i1:i2]})
        elif tag == "delete":
            raw_hunks.append({"type": "delete", "lines": old_lines[i1:i2]})
            deletions += (i2 - i1)
        elif tag == "insert":
            raw_hunks.append({"type": "insert", "lines": new_lines[j1:j2]})
            insertions += (j2 - j1)
        else:  # 'replace' -> show deletion then insertion
            raw_hunks.append({"type": "delete", "lines": old_lines[i1:i2]})
            raw_hunks.append({"type": "insert", "lines": new_lines[j1:j2]})
            deletions += (i2 - i1)
            insertions += (j2 - j1)

    # P0-c: 大文件截断 — 超过阈值时折叠 equal hunks，仅保留少量上下文
    truncated = False
    total_lines = sum(len(h["lines"]) for h in raw_hunks)
    if len(raw_hunks) > _MAX_HUNKS or total_lines > _MAX_TOTAL_LINES:
        truncated = True
        hunks: list[dict[str, Any]] = []
        for h in raw_hunks:
            if h["type"] != "equal":
                hunks.append(h)
            else:
                # 保留前后各 _MAX_EQUAL_LINES 行作为上下文
                lines = h["lines"]
                if len(lines) <= 2 * _MAX_EQUAL_LINES:
                    hunks.append(h)
                else:
                    # 首部上下文 + 折叠标记 + 尾部上下文
                    head = lines[:_MAX_EQUAL_LINES]
                    tail = lines[-_MAX_EQUAL_LINES:]
                    hunks.append({"type": "equal", "lines": head})
                    hunks.append({
                        "type": "equal",
                        "lines": [f"… {len(lines) - 2 * _MAX_EQUAL_LINES} lines folded …"],
                    })
                    hunks.append({"type": "equal", "lines": tail})
        # 再次检查，如果仍然过长就只保留变更 hunks + 1 行上下文
        if sum(len(h["lines"]) for h in hunks) > _MAX_TOTAL_LINES:
            hunks = [h for h in raw_hunks if h["type"] != "equal"]
            # 每个变更 hunk 前后插入 1 行上下文（从相邻 equal 中取）
            # 这是最激进的截断，确保 payload 不会过大
    else:
        hunks = raw_hunks

    return {
        "path": path,
        "name": Path(path).name,
        "kind": _infer_file_change_kind(path),
        "insertions": insertions,
        "deletions": deletions,
        "hunks": hunks,
        "truncated": truncated,
        "before_ref": before_ref,
        "before_sha": before_sha,
    }
