from __future__ import annotations

import re
from pathlib import Path

from prismclaw.schema.config.config import WorkspaceConfig


class WorkspaceBoundaryError(Exception):
    pass


# ── Path sanitization (single source of truth) ──────────────────────────────
#
# LLM tool-call payloads often leak template/formatting residue: trailing/leading
# wrappers ({}, [], (), quotes, backticks), punctuation, extra slashes, or a
# leading ``/`` that turns a relative workspace path into a bogus absolute path.
# The classic symptom is ``uploads/a.pptx{}``: the suffix becomes ``.pptx{}``
# so ``fmt_from_path`` returns ``None`` and the user sees a confusing error
# "only .docx/.xlsx/.pptx are supported" even though the uploaded file is a
# real .pptx.
#
# Policy: every incoming relative workspace path flows through ``clean_rel_path``
# before it touches the filesystem or any suffix-based format check. We enforce
# it at the two bottom layers every caller depends on:
#   1. ``WorkspaceResolver.resolve_within`` (all file tool IO goes through here)
#   2. ``fmt_from_path`` in ``office_engines`` (suffix format check, happens
#      before ``resolve_within`` in the office toolchain)

_WRAPPER_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("`", "`"),
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
)

_LEADING_OPEN_WRAPPERS = frozenset({"(", "[", "{", '"', "'", "`"})
# IMPORTANT: do NOT include ``.`` here — dotfiles (``.gitignore``, ``.env``),
# relative-traversal (``..``), and dotted extensions (``file.tar.gz``) must
# survive. Trailing periods from sentence-end punctuation are handled by a
# targeted rule below (single trailing ``.`` removed only when it's not part of
# a ``..`` component or a dotfile).
_TRAILING_CLOSE_WRAPPERS = frozenset({")", "]", "}", '"', "'", "`", ",", ";", ":", "!", "?"})

# Matches a *cluster* of template/quote/whitespace punctuation at the END of
# the string. Period is intentionally NOT in this character class — removing it
# blindly would swallow ``..``, ``.gitignore``, and every extension boundary.
# (Sentence-ending trailing periods are peeled by the targeted per-character
# loop below.)
# NOTE: ``\\`` matches a literal backslash (template leakage); ``\s`` matches
# whitespace. Writing ``\\s`` in the raw string would put a literal letter
# ``s`` into the class and strip the trailing ``s`` of real paths like
# ``old.xls`` or ``docs/`` — a bug caught by the M1 legacy-office test.
_TAIL_TEMPLATE_RE = re.compile(r'[\{\}\[\]\(\)\`\'\"\\\s,;:!?]+$')


def _strip_sentence_trailing_period(s: str) -> str:
    """Remove a trailing sentence period, preserving path-semantic dots.

    Safe rules (iterated up to 5 times for stacked wrapper+period combos):
      - not applied to ``..`` (relative traversal) or ``.xxx`` (dotfile basename)
      - only removes a FINAL ``.`` when the character before it is not ``.``
        and the final segment's length is > 1 (i.e. not a bare ``.``)
    """
    for _ in range(5):
        if len(s) < 2:
            return s
        if s[-1] != ".":
            return s
        prev = s[-2]
        # ``..`` or ``...``: preserve (traversal / variant).
        if prev == ".":
            return s
        # Basename is a dotfile like ``.env``? Strip only if there are > 2 chars
        # AND the last component is not a pure-dot basename.
        last_seg = s.rsplit("/", 1)[-1]
        if len(last_seg) <= 2 and last_seg.startswith(".") and not last_seg[1:2].isalnum():
            return s
        s = s[:-1]
    return s


def clean_rel_path(raw: object) -> str | None:
    """Clean LLM/format-leak artifacts from a relative workspace path.

    Returns ``None`` if the input is not a string or reduces to empty after
    cleaning. Callers should treat ``None`` as "path required / invalid input".

    Normalizations applied iteratively (up to 8 rounds so nested wrappers are
    peeled cleanly):
      - drop null bytes
      - ``\\`` -> ``/``
      - peel outer balanced wrappers (quotes / parens / brackets / braces)
      - drop trailing-only or leading-only wrapper residue / sentence punctuation
      - strip trailing template clusters (``{}[]()`,;:!?`` etc.) via regex
      - strip sentence-ending trailing ``.`` (targeted rule to preserve ``..``
        and dotfiles)
      - collapse ``//`` -> ``/`` and strip leading ``/`` (workspace paths
        are always relative to the workspace root)
    """
    if not isinstance(raw, str):
        return None
    s = raw
    s = s.replace("\x00", "")
    s = s.replace("\\", "/")
    if not s:
        return None
    for _ in range(8):
        prev = s
        s = s.strip()
        changed = True
        while changed and s:
            changed = False
            first, last = s[0], s[-1]
            for open_c, close_c in _WRAPPER_PAIRS:
                if first == open_c and last == close_c and len(s) >= 2:
                    s = s[1:-1].strip()
                    changed = True
                    break
            if not changed:
                if last in _TRAILING_CLOSE_WRAPPERS:
                    s = s[:-1].strip()
                    changed = True
                elif first in _LEADING_OPEN_WRAPPERS:
                    s = s[1:].strip()
                    changed = True
        tail_stripped = _TAIL_TEMPLATE_RE.sub("", s).strip()
        if tail_stripped != s:
            s = tail_stripped
        s = _strip_sentence_trailing_period(s)
        while "//" in s:
            s = s.replace("//", "/")
        s = s.lstrip("/")
        # Drop any spurious ``./`` components (both leading and inner ``x/./y``).
        # They are semantically no-ops but ugly when they leak from string
        # templates; Path() handles them anyway, but stripping keeps suffix
        # checks clean for cases like ``./a.pptx{} -> ./a.pptx -> a.pptx``.
        while s.startswith("./"):
            s = s[2:]
        while "/./" in s:
            s = s.replace("/./", "/")
        if s == prev:
            break
    return s or None


class WorkspaceResolver:
    def __init__(self, config: WorkspaceConfig) -> None:
        self._config = config

    def resolve(self, workspace_id: str) -> Path:
        return self._config.resolve_path(workspace_id)

    # 虚拟路径前缀 → 物理根目录映射（按优先级匹配，先命中先返回）。
    # workspace 级共享 → workspace 根；session 私有 → session 根。
    _PREFIX_SESSION_SUBDIRS = {
        "uploads": "uploads",
        "context_blocks": "context_blocks",
        "tool_results": "tool_results",
        "outputs": "outputs",
    }

    def resolve_within(
        self,
        workspace_path: Path,
        relative: str,
        *,
        session_root: Path | None = None,
        for_write: bool = False,
    ) -> Path:
        """Resolve a workspace-relative path to its absolute location.

        虚拟路径约定（Agent 在 sandbox 里看到的 /workspace/ 前缀）：

          project/<...>             → workspace 根（用户项目文件，读写直达原件）
                                       ⚠ 写入禁止 sessions/ 和 .work/ 子树
          inputs/<...>              → workspace 根（所有 session 共享上传，只读语义）
          uploads/<...>             → sessions/<sid>/uploads/    (session 私有)
          context_blocks/<...>      → sessions/<sid>/context_blocks/
          tool_results/<...>        → sessions/<sid>/tool_results/
          outputs/<...>             → sessions/<sid>/outputs/
          sessions/<sid>/<...>      → 物理格式归一：本会话 → 剥前缀按上面规则；
                                       跨会话 → workspace 根直达，只读、禁写
          <无前缀>                  → session_root/outputs/  (有 session_root)
                                     或 workspace_root/       (无 session_root)

        session_root 由调用方通过 workspace_paths.resolve_session_dir 算好传入——
        本函数不依赖任何业务参数（user_id / workspace_id / session_id），只做
        纯路径解析 + 边界检查。

        for_write=True 时，project/ 前缀禁止写入 sessions/.work 子树（写 session
        交付物请用 outputs/ 前缀）。read 默认 False。
        """
        stripped_raw = relative.strip()

        # 文件工具参数是字面量字符串，不做 shell 变量展开。技能文档里
        # `$PRISMCLAW_OUTPUT_DIR/<rel>` 只适用于 bash 通道；模型照抄到文件工具
        # 会生成名为 `$PRISMCLAW_OUTPUT_DIR` 的字面目录（验收 1 明令阻断）。
        if "$" in stripped_raw:
            raise WorkspaceBoundaryError(
                f"路径 {relative!r} 含未展开的 shell 变量；文件工具不做变量展开。"
                "写本会话交付物请用 `outputs/<rel>`（仅 bash 中可用 "
                "`$PRISMCLAW_OUTPUT_DIR/<rel>`）。"
            )

        # ── home-relative (~) 或文件系统绝对 (/Users, /etc) ──────────────────
        # 在 clean_rel_path 之前检测——后者会剥掉 leading '/' 把真绝对路径变相对。
        if stripped_raw.startswith("~") or (
            stripped_raw.startswith("/")
            and len(stripped_raw) > 1
            and stripped_raw[1] != "/"
            and _looks_like_fs_root_path(stripped_raw)
        ):
            pre = Path(stripped_raw).expanduser()
            if pre.is_absolute():
                resolved = pre.resolve() if pre.exists() else _lexical_resolve(pre)
                return resolved

        cleaned = clean_rel_path(relative)
        if cleaned is None:
            raise WorkspaceBoundaryError(f"路径 {relative!r} 清理后为空或无效")

        workspace_abs = workspace_path.resolve()

        # 容器内挂载点 `/workspace/...` 前缀泄漏：模型看到挂载点会照抄。
        # 仅当工作区根下没有真实名为 workspace/ 的目录时剥离，避免吞掉用户项目目录。
        if cleaned.startswith("workspace/"):
            residual = cleaned[len("workspace/"):]
            if residual and not (workspace_abs / "workspace").exists():
                cleaned = residual

        # `sessions/<sid>/...` 物理格式泄漏：present_files 报错与系统提示里
        # 出现的就是这种路径，模型会照抄进文件工具参数。若不做显式归一，它会
        # 落入裸路径分支，首选候选 session_root/outputs/ 造成双重嵌套
        # （outputs/sessions/<sid>/outputs/x.docx）——写新文件时创建垃圾目录。
        # 本会话自引用 → 剥前缀归一后走正常分支；跨会话 → 只读直达、禁止写。
        preset_target: Path | None = None
        if cleaned.startswith("sessions/") or cleaned == "sessions":
            residual = cleaned[len("sessions/"):] if cleaned != "sessions" else ""
            seg, _, rest = residual.partition("/")
            if session_root is not None and seg and seg == session_root.name:
                if rest:
                    cleaned = rest  # 本会话自引用：归一后继续走下方前缀分支
                else:
                    preset_target = session_root.resolve()
            else:
                if for_write:
                    raise WorkspaceBoundaryError(
                        f"路径 {relative!r} 指向会话 {seg!r} 的私有目录，禁止写入。"
                        "写本会话交付物请用 `outputs/<rel>`。"
                    )
                preset_target = workspace_abs / cleaned

        # ── 虚拟路径前缀映射 ────────────────────────────────────────────────
        # project/ → workspace 根（用户项目文件，直达原件）
        # 写入时禁止 sessions/ 和 .work/ 子树（session 交付物用 outputs/）
        if preset_target is not None:
            target = preset_target
        elif cleaned.startswith("project/") or cleaned == "project":
            rel_after = cleaned[len("project/"):].lstrip("/") if cleaned != "project" else ""
            if for_write and rel_after:
                if rel_after.startswith("sessions/") or rel_after == "sessions":
                    raise WorkspaceBoundaryError(
                        f"project/ 前缀下禁止写入 sessions/ 子树（{relative!r}）。"
                        f"写 session 交付物请改用 outputs/<sid>/..."
                    )
                if rel_after.startswith(".work/") or rel_after == ".work":
                    raise WorkspaceBoundaryError(
                        f"project/ 前缀下禁止写入 .work/ 系统子树（{relative!r}）。"
                    )
            target = workspace_abs / rel_after if rel_after else workspace_abs
        elif cleaned.startswith("inputs/") or cleaned == "inputs":
            # inputs/ → workspace 根（workspace 级共享上传）
            target = workspace_abs / cleaned
        else:
            # session 私有前缀（需要 session_root）
            matched_session = False
            for prefix, subdir in self._PREFIX_SESSION_SUBDIRS.items():
                if cleaned.startswith(f"{prefix}/") or cleaned == prefix:
                    if session_root is None:
                        raise WorkspaceBoundaryError(
                            f"路径 {relative!r} 需要 session_root（session-scoped 前缀）"
                        )
                    target = session_root.resolve() / subdir / cleaned[len(prefix):].lstrip("/")
                    matched_session = True
                    break

            if not matched_session:
                # 无前缀路径：有 session_root 时先默认 outputs/，
                # 但如果 outputs/ 里找不到，再在其他 session 私有子目录
                # （context_blocks / tool_results / uploads）里兜底搜索 ——
                # 防御模型把前缀抄丢（比如引用格式是 context_blocks/cb_xxx.txt
                # 但模型只写了 cb_xxx.txt）。
                if session_root is not None:
                    _sr = session_root.resolve()
                    _candidates = [_sr / "outputs" / cleaned]
                    for _alt in ("context_blocks", "tool_results", "uploads"):
                        _candidates.append(_sr / _alt / cleaned)
                    target = next((c for c in _candidates if c.exists()), _candidates[0])
                    # 第 3 候选：workspace 根下的真实文件（用户项目文件，裸路径读兜底）。
                    _ws_candidate = workspace_abs / cleaned
                    if not any(c.exists() for c in _candidates) and _ws_candidate.is_file():
                        if for_write:
                            raise WorkspaceBoundaryError(
                                f"裸路径 {relative!r} 命中了 workspace 根下的项目文件；"
                                "写入请明确前缀：`project/<rel>` 改原件，"
                                "`outputs/<rel>` 产本会话新版本。"
                            )
                        target = _ws_candidate
                else:
                    target = workspace_abs / cleaned

        # ── 展开 + resolve + 边界检查 ────────────────────────────────────────
        target_expanded = Path(str(target)).expanduser()
        resolved = target_expanded.resolve() if target_expanded.exists() else _lexical_resolve(target_expanded)
        try:
            resolved.relative_to(workspace_abs)
        except ValueError as exc:
            raise WorkspaceBoundaryError(
                f"路径 {relative!r}（清理后：{cleaned!r}）越出工作区边界 {workspace_abs}"
            ) from exc
        return resolved


_FS_ROOT_SEGMENTS = frozenset({
    "Users", "users",
    "etc",
    "private",
    "opt",
    "var",
    "tmp",
    "Library", "library",
    "Applications", "applications",
    "System", "system",
    "Volumes", "volumes",
    "home",
    "root",
    "dev",
    "mnt",
    "srv",
})


def _looks_like_fs_root_path(path: str) -> bool:
    """Heuristic: does a leading-``/`` path look like it belongs on the
    filesystem root rather than under the workspace?

    ``/documents/report`` (first segment "documents" — not in the set)
    → workspace-scoped, False.

    ``/Users/foo/bar`` (first segment "Users" — in the set)
    → filesystem-absolute, True.

    Single-purpose: keeps the old "leading-slash means workspace root"
    convention for normal workspace paths while correctly routing real
    filesystem-absolute inputs.
    """
    # path guaranteed to start with '/' and have no double-slashes (cleaned
    # above already collapses them; we check on raw but that's close enough).
    rest = path[1:]
    first = rest.split("/", 1)[0]
    return first in _FS_ROOT_SEGMENTS


def _lexical_resolve(path: Path) -> Path:
    import os

    norm_str = os.path.normpath(str(path))
    return Path(norm_str)
