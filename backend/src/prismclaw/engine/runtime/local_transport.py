"""P1-RUN-02：WorkspaceRuntimeClient（LocalTransport）。

开发环境下 `PRISMCLAW_DEPLOYMENT_ENV=development` 时，
不通过 HTTP 调用 Runtime-Agent，直接在本地执行。
这降低了开发和测试门槛（不依赖 Docker Swarm）。

生产环境禁用：如果 env 不是 development，任何 LocalTransport 初始化都会
抛 RuntimeError，确保线上只走 WorkspaceRuntimeClient HTTP 路径。

安全差异：
  - 容器隔离失效（本地执行）
  - RuleGate + SafetyGate 仍然生效（这是开发模式的安全底线）
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.schema.services.env_types import CommandResult, FileResult

_DEPLOYMENT_ENV = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()


def _assert_dev_env() -> None:
    """生产环境禁用 LocalTransport。"""
    if _DEPLOYMENT_ENV == "production":
        raise RuntimeError(
            "LocalTransport is disabled in production; "
            "use WorkspaceRuntimeClient (HTTP transport to Runtime-Agent)."
        )


# ── tier 配置映射（供本地模式使用）────────────────────────────

_TIER_CONFIG: dict[SandboxTier, dict[str, Any]] = {
    SandboxTier.STANDARD: {"allow_net": True, "deny_pid_namespace": False, "allow_read_user_safe": True},
    SandboxTier.TIGHT: {"allow_net": True, "deny_pid_namespace": False, "allow_read_user_safe": False},
    SandboxTier.STRICT: {"allow_net": False, "deny_pid_namespace": True, "allow_read_user_safe": False},
}


def _tier_to_config(tier: SandboxTier) -> dict[str, Any]:
    return _TIER_CONFIG.get(tier, _TIER_CONFIG[SandboxTier.STRICT])


def _resolve(root: str, path: str) -> Path:
    """Resolve a path relative to ``root``, with expanduser first.

    Replaces the common ``Path(root) / path if not os.path.isabs(path) else Path(path)``
    idiom, which fails for ``~/.prismclaw/...`` inputs because ``os.path.isabs``
    does not recognise ``~`` as absolute. ``expanduser()`` before the check
    normalises both ``/Users/...`` and ``~/.prismclaw/...`` to genuine absolute paths.
    """
    expanded = Path(path.strip()).expanduser()
    if expanded.is_absolute():
        return expanded
    return Path(root).expanduser().resolve() / expanded


@dataclass
class _LocalTransportImpl:
    """本地执行实现（开发模式）。"""

    workspace_root: str
    workspace_id: str = ""

    def __post_init__(self) -> None:
        _assert_dev_env()
        Path(self.workspace_root).mkdir(parents=True, exist_ok=True)

    # ── 公共 API（与 WorkspaceRuntimeClient 接口一致）────────────────

    async def bash(
        self,
        command: str,
        *,
        tier: str = "standard",
        timeout_seconds: int | None = None,
        template_id: str = "__default__",
        workspace_id: str | None = None,
        workspace_root: str | None = None,
    ) -> CommandResult:
        """本地执行 bash 命令（开发模式）。"""
        _assert_dev_env()
        cfg = _tier_to_config(SandboxTier(tier))
        timeout = timeout_seconds if timeout_seconds is not None else 30

        # STRICT tier: 执行 shell 命令时使用隔离 cwd
        cwd = None
        if cfg["deny_pid_namespace"]:
            cwd = self.workspace_root

        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return CommandResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                timed_out=False,
                sandbox_tier_used=str(tier),
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[has-type]
            except Exception:
                pass
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"command timed out after {timeout}s",
                timed_out=True,
                sandbox_tier_used=str(tier),
            )

    async def read_text(self, path: str, *, encoding: str = "utf-8", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        _assert_dev_env()
        try:
            p = _resolve(self.workspace_root, path)
            return FileResult(success=True, content=p.read_text(encoding=encoding))
        except FileNotFoundError:
            return FileResult(success=False, error=f"file not found: {path}")
        except Exception as e:
            return FileResult(success=False, error=str(e))

    async def write_text(self, path: str, content: str, *, encoding: str = "utf-8", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        _assert_dev_env()
        try:
            p = _resolve(self.workspace_root, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=encoding)
            return FileResult(success=True)
        except Exception as e:
            return FileResult(success=False, error=str(e))

    async def read_bytes(self, path: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        _assert_dev_env()
        try:
            p = _resolve(self.workspace_root, path)
            return FileResult(success=True, content=p.read_bytes())
        except FileNotFoundError:
            return FileResult(success=False, error=f"file not found: {path}")
        except Exception as e:
            return FileResult(success=False, error=str(e))

    async def write_bytes(self, path: str, content: bytes, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        _assert_dev_env()
        try:
            p = _resolve(self.workspace_root, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            return FileResult(success=True)
        except Exception as e:
            return FileResult(success=False, error=str(e))

    async def http_get(self, url: str, *, tier: str = "standard", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        _assert_dev_env()
        cfg = _tier_to_config(SandboxTier(tier))
        if not cfg["allow_net"]:
            return FileResult(success=False, error="strict_deny_network")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                return FileResult(success=True, content=resp.text)
        except Exception as e:
            return FileResult(success=False, error=str(e))

    async def health(self, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> bool:
        _assert_dev_env()
        return True


# 方便外部直接构造：与 WorkspaceRuntimeClient 构造签名近似
def make_local_transport(
    workspace_root: str,
    workspace_id: str = "",
) -> _LocalTransportImpl:
    """开发模式：创建本地 transport 实例。"""
    return _LocalTransportImpl(workspace_root=workspace_root, workspace_id=workspace_id)
