"""P1-SEC-02：RuleGate — L0 静态规则门禁。

RuleGate 是 SafetyGate 的第一层：在 RiskRouter 做风险分级之前，
先做快速静态规则检查。命中 deny 规则的工具调用直接拒绝，
不进入后续的 RiskRouter / SafetyGate 流程。

5 条规则：
  1. 路径逃逸：路径含 .. 或以 / 开头且不在 workspace 内 → deny
  2. 敏感路径：/etc/passwd, ~/.ssh, /proc 等 → deny
  3. SSRF：URL 指向 169.254.169.254 / 127.0.0.1 / ::1 等 → deny
  4. workspace_tag：allow_external_write=False + EXTERNAL_WRITE → deny；bash/python → 统一 TIGHT
  5. mcp_meta：未注册 MCP → fail-closed 最严分类
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse
from typing import Any

from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.engine.security.gate.routing.risk_router import ToolSafetyMeta, SideEffectScope
from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.engine.security.gate.routing._extension_registry import get_domain_sensitive_extensions


@dataclass(frozen=True)
class RuleGateResult:
    """RuleGate 检查结果。"""
    allowed: bool
    reason: str = ""
    tier_override: SandboxTier | None = None  # workspace_tag 规则可能升级 tier


# ── 敏感路径列表 ────────────────────────────────────────────────────────

_SENSITIVE_PATHS = frozenset({
    "/etc/passwd", "/etc/shadow", "/etc/ssh",
    "/proc", "/sys", "/dev",
    "~/.ssh", "~/.gnupg", "~/.aws",
})

_SENSITIVE_EXTENSIONS = frozenset({
    # Cryptographic keys & signatures (domain-agnostic, security-critical)
    ".pem", ".key", ".p12", ".pfx", ".sig", ".p7s", ".psig",
    # Digitally signed documents (domain-agnostic)
    ".odt-cert", ".docx-sign",
})

_SENSITIVE_PREFIXES = ("/etc/", "/proc/", "/sys/", "/dev/")

# SSRF 目标
_SSRF_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_SSRF_IP_PREFIXES = ("127.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                      "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                      "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                      "172.30.", "172.31.", "192.168.")


class RuleGate:
    """L0 静态规则门禁。"""

    def check(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        meta: ToolSafetyMeta,
        workspace_tag: WorkspaceTag | None = None,
        workspace_root: str | None = None,
    ) -> RuleGateResult:
        """执行 5 条静态规则检查。返回 RuleGateResult。"""

        # Rule 1: 路径逃逸
        path_result = self._check_path_escape(arguments, workspace_root)
        if not path_result.allowed:
            return path_result

        # Rule 2: 敏感路径
        sensitive_result = self._check_sensitive_path(arguments)
        if not sensitive_result.allowed:
            return sensitive_result

        # Rule 3: SSRF
        ssrf_result = self._check_ssrf(arguments)
        if not ssrf_result.allowed:
            return ssrf_result

        # Rule 4: workspace_tag 交叉校验
        tag_result = self._check_workspace_tag(tool_name, meta, workspace_tag)
        if not tag_result.allowed:
            return tag_result

        # Rule 5: MCP 未注册 → fail-closed（由 get_tool_safety_meta 处理，
        # meta 已经是 _FAIL_CLOSED_META，这里不再重复检查）

        # 返回最终结果——如果有 tier_override，传递给上层
        return RuleGateResult(
            allowed=True,
            tier_override=tag_result.tier_override,
        )

    # ── Rule 1: 路径逃逸 ─────────────────────────────────────────────

    @staticmethod
    def _check_path_escape(
        arguments: dict[str, Any],
        workspace_root: str | None,
    ) -> RuleGateResult:
        path = arguments.get("path") or arguments.get("file_path") or arguments.get("destination")
        if not isinstance(path, str):
            return RuleGateResult(allowed=True)
        try:
            p = PurePosixPath(path)
            if ".." in p.parts:
                return RuleGateResult(allowed=False, reason="path_escape：路径中包含 '..'")
            if p.is_absolute() and workspace_root:
                if not path.startswith(workspace_root.rstrip("/") + "/") and path != workspace_root.rstrip("/"):
                    return RuleGateResult(allowed=False, reason="path_escape：绝对路径位于工作区之外")
        except Exception:
            pass
        return RuleGateResult(allowed=True)

    # ── Rule 2: 敏感路径 ─────────────────────────────────────────────

    @staticmethod
    def _check_sensitive_path(arguments: dict[str, Any]) -> RuleGateResult:
        path = arguments.get("path") or arguments.get("file_path") or arguments.get("destination")
        if not isinstance(path, str):
            return RuleGateResult(allowed=True)
        # 精确匹配
        if path in _SENSITIVE_PATHS:
            return RuleGateResult(allowed=False, reason=f"sensitive_path: {path}")
        # 前缀匹配
        for prefix in _SENSITIVE_PREFIXES:
            if path.startswith(prefix):
                return RuleGateResult(allowed=False, reason=f"sensitive_path: {path} under {prefix}")
        # ~/.ssh 等
        if path.startswith("~/") and any(path.startswith(f"~/{sp}") for sp in (".ssh/", ".gnupg/", ".aws/")):
            return RuleGateResult(allowed=False, reason=f"sensitive_path: {path}")
        # 敏感扩展名匹配（核心通用 + 域注册）
        lower_path = path.lower()
        all_extensions = _SENSITIVE_EXTENSIONS | get_domain_sensitive_extensions()
        for ext in all_extensions:
            if lower_path.endswith(ext):
                return RuleGateResult(allowed=False, reason=f"sensitive_extension: {path} (extension {ext})")
        return RuleGateResult(allowed=True)

    # ── Rule 3: SSRF ──────────────────────────────────────────────────

    @staticmethod
    def _check_ssrf(arguments: dict[str, Any]) -> RuleGateResult:
        url = arguments.get("url") or arguments.get("endpoint")
        if not isinstance(url, str):
            return RuleGateResult(allowed=True)
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if host in _SSRF_HOSTS:
                return RuleGateResult(allowed=False, reason=f"ssrf: {host}")
            for prefix in _SSRF_IP_PREFIXES:
                if host.startswith(prefix) or host == prefix.rstrip("."):
                    return RuleGateResult(allowed=False, reason=f"ssrf: private IP {host}")
            if host in ("localhost", "127.0.0.1", "::1"):
                return RuleGateResult(allowed=False, reason=f"ssrf: loopback {host}")
        except Exception:
            pass
        return RuleGateResult(allowed=True)

    # ── Rule 4: workspace_tag 交叉校验 ────────────────────────────────

    @staticmethod
    def _check_workspace_tag(
        tool_name: str,
        meta: ToolSafetyMeta,
        workspace_tag: WorkspaceTag | None,
    ) -> RuleGateResult:
        if workspace_tag is None:
            return RuleGateResult(allowed=True)

        # allow_external_write=False + EXTERNAL_WRITE → deny
        if not workspace_tag.allow_external_write:
            if meta.side_effect_scope in (SideEffectScope.EXTERNAL_WRITE, SideEffectScope.EXTERNAL_IRREVERSIBLE):
                return RuleGateResult(
                    allowed=False,
                    reason=f"external_write_deny：{tool_name} 在 allow_external_write=False 的工作区中具有 {meta.side_effect_scope.value} 副作用",
                )

        # bash/python → 统一升级为 TIGHT（不再仅限 PHI 工作区）
        if tool_name in ("bash", "python") and meta.side_effect_scope == SideEffectScope.LOCAL_WORKSPACE:
            return RuleGateResult(allowed=True, tier_override=SandboxTier.TIGHT)

        return RuleGateResult(allowed=True)
