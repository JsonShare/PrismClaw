"""BwrapPolicy — 基于 bubblewrap 的进程级沙箱策略。

替代不可安装的 SrtPolicy，使用 Linux namespace + seccomp 内核级强制隔离。
per-user workspace 隔离：每个用户只看到自己的 /workspace。
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Literal

from prismclaw.schema.config.config import SandboxConfig
from prismclaw.schema.content.workspace import SandboxTier

logger = logging.getLogger(__name__)

# ── 硬编码安全底线：永不挂载到沙箱中的路径 ──────────────────────────────

_HARDCODED_DENY_BIND: tuple[str, ...] = (
    "/root",             # root 家目录
    "/home",             # 所有用户家目录（工作区通过 --bind 单独挂载）
    "/etc/ssh",          # SSH 配置
    "/etc/shadow",       # 密码文件
    "/etc/ssl/private",  # TLS 私钥
    "/var/lib/docker",   # Docker 数据
    "/var/lib/kubelet",  # K8s 数据
)


class BwrapBinaryNotFound(RuntimeError):
    """Raised when ``bwrap`` is required but missing on $PATH."""


class BwrapCommandTooLong(RuntimeError):
    """Raised when a bash command would exceed OS ARG_MAX after bwrap wrapping."""


class BwrapPolicy:
    """基于 bubblewrap 的进程级沙箱策略。

    生成 bwrap 命令行参数，在 Linux namespace + seccomp 内核级强制隔离中执行。

    - wrap_bash_command: 返回 (bwrap_path, [args..., /bin/sh, -c, cmd])
    - wrap_mcp_stdio:   包装 MCP stdio 进程
    - validate_path:    路径校验（原 srt_executor.SrtPolicy 能力合并）
    """

    backend: Literal["bwrap"] = "bwrap"

    def __init__(self, *, settings: SandboxConfig) -> None:
        self._settings = settings
        path = shutil.which("bwrap")
        if path is None:
            raise BwrapBinaryNotFound(
                "bwrap binary not found on $PATH; install via "
                "'apt install bubblewrap' (Debian/Ubuntu) or "
                "'yum install bubblewrap' (RHEL/CentOS)"
            )
        self._binary_path = path
        # 运行时只读根：项目内 agent 系统必需的源码/配置/插件目录。
        # 这些目录在 runtime path validation 中放行读取（写操作仍需 workspace_root）。
        self._runtime_readonly_roots = self._compute_runtime_readonly_roots()

    @property
    def binary_path(self) -> str:
        return self._binary_path

    def wrap_bash_command(
        self, cmd: str, ctx: Any
    ) -> tuple[str, list[str]]:
        """包装 bash 命令，返回 (bwrap_path, [args..., /bin/sh, -c, cmd])。

        ctx 需包含 workspace_root 和 sandbox_tier 属性。
        """
        workspace_root = getattr(ctx, "workspace_root", "/tmp")
        tier = getattr(ctx, "sandbox_tier", SandboxTier.STANDARD)
        args = self._build_bwrap_args(workspace_root, tier)

        logger.info(
            "sandbox_bash_exec",
            extra={
                "workspace_root": workspace_root,
                "tier": tier.value,
                "command": cmd[:200],
            },
        )

        return (
            self._binary_path,
            args + ["/bin/sh", "-c", cmd],
        )

    def wrap_mcp_stdio(
        self,
        params: Any,
        server_name: str,
        sandbox_config: Any,
    ) -> Any:
        """包装 MCP stdio 进程，在 bwrap 沙箱中启动。"""
        workspace_root = "/tmp"
        tier = SandboxTier.STANDARD

        if isinstance(sandbox_config, dict):
            workspace_root = sandbox_config.get("workspace_root", "/tmp")
            tier_val = sandbox_config.get("sandbox_tier")
            if tier_val is not None:
                tier = SandboxTier(tier_val) if isinstance(tier_val, str) else tier_val

        args = self._build_bwrap_args(workspace_root, tier)
        # bwrap --clearenv 会清掉全部环境，日志 env 需显式 setenv 透传进容器，
        # 否则 sandbox 启用的自建 MCP server 无法写 logs/mcp_server_*/
        params_env = getattr(params, "env", None) or {}
        for var in (
            "PRISMCLAW_MCP_SERVER_LOG_DIR",
            "PRISMCLAW_INFRA_PATH",
            "PRISMCLAW_WORKSPACES_BASE",
        ):
            val = params_env.get(var) or os.environ.get(var)
            if val:
                args.extend(["--setenv", var, val])
        original_command = params.command
        original_args = list(params.args or [])

        try:
            params.command = self._binary_path
            params.args = args + [original_command, *original_args]
        except (AttributeError, TypeError):
            from mcp import StdioServerParameters

            return StdioServerParameters(
                command=self._binary_path,
                args=args + [original_command, *original_args],
                env=getattr(params, "env", None),
            )
        return params

    # ── 运行时只读根（agent 系统必需的源码/插件/配置目录） ─────────────────

    @staticmethod
    def _compute_runtime_readonly_roots() -> tuple[Path, ...]:
        """所有运行时只读目录。

        与 RuntimeExecutor._compute_runtime_readonly_roots 同源 —— 这里
        是 bwrap 沙箱的 bind-read-only 入口，那边是 macOS 无 bwrap 的
        Python 层路径校验入口。两处必须返回同一集合。

        统一放行：
          - ~/.prismclaw/                     → skills + agents + plugins
                                               （启动时 sync_prismclaw_home
                                                 已把 backend/.prismclaw 拷到这里）
          - project_root/backend/configs/    → models.yaml, agent.yaml 等配置
          - project_root/backend/src/prismclaw/ → 源码（agent introspection）
        """
        roots: list[Path] = []
        # 1) 用户级 ~/.prismclaw（sync 目标）
        home_root = (Path.home() / ".prismclaw").resolve()
        if home_root.is_dir():
            roots.append(home_root)

        # 2) 项目内 repo 目录（configs + src）
        try:
            from prismclaw.infra.paths import resolve_project_root
            project_root = resolve_project_root()
            for sub in ("backend/configs", "backend/src/prismclaw"):
                p = (project_root / sub).resolve()
                if p.is_dir():
                    roots.append(p)
        except Exception:
            pass
        return tuple(roots)

    # ── 路径校验 ──────────────────────────────────────────────────────

    def validate_path(
        self, path: str, tier: SandboxTier, workspace_root: str
    ) -> str | None:
        """路径校验（原 srt_executor.SrtPolicy 能力合并）。

        返回 None=允许，str=拒绝原因。
        """
        try:
            resolved = Path(path).resolve()
            ws = Path(workspace_root).resolve()
        except Exception:
            return f"invalid_path: {path}"

        # 工作区内：允许
        try:
            resolved.relative_to(ws)
            return None
        except ValueError:
            pass

        # 运行时只读根：项目内 agent 系统需读取自己的定义文件、SKILL.md、配置、源码
        for root in self._runtime_readonly_roots:
            try:
                resolved.relative_to(root)
                return None
            except ValueError:
                continue

        # 系统只读路径：允许读
        if str(resolved).startswith(("/usr", "/lib", "/bin", "/sbin", "/etc")):
            return None

        # 其他：拒绝
        return f"path_outside_workspace: {path} outside {workspace_root}"

    def check_network_access(self, tier: SandboxTier) -> str | None:
        """检查网络访问权限。STRICT tier 禁止网络。"""
        if tier == SandboxTier.STRICT:
            return "strict_deny_network"
        return None

    # ── 核心：per-user bwrap 参数生成 ─────────────────────────────────

    def _build_bwrap_args(
        self, workspace_root: str, tier: SandboxTier
    ) -> list[str]:
        # 安全断言：workspace_root 不得落在敏感路径内
        try:
            resolved_ws = Path(workspace_root).resolve()
            for deny in _HARDCODED_DENY_BIND:
                try:
                    resolved_ws.relative_to(Path(deny).resolve())
                    raise ValueError(
                        f"workspace_root={workspace_root} falls inside denied path {deny}"
                    )
                except ValueError:
                    if "falls inside denied" in str(ValueError):
                        raise
                    # 不在 deny 路径内，继续检查
        except ValueError:
            raise
        except Exception:
            pass  # 路径解析失败时不阻塞，由 bwrap 自身报错

        args = [
            # ── 系统目录只读挂载 ──
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/sbin", "/sbin",

            # ── 必要的系统配置文件（只读）──
            "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", "/etc/ssl", "/etc/ssl",
            "--ro-bind", "/etc/passwd", "/etc/passwd",
            "--ro-bind", "/etc/group", "/etc/group",
            "--ro-bind", "/etc/hosts", "/etc/hosts",
            "--ro-bind", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
            "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
        ]

        # lib64（部分 Linux 发行版需要）
        if os.path.isdir("/lib64"):
            args.extend(["--ro-bind", "/lib64", "/lib64"])

        args.extend([
            # ── 用户工作区读写挂载 ──
            "--bind", workspace_root, "/workspace",

            # ── 隔离临时目录 ──
            "--tmpfs", "/tmp",
            "--tmpfs", "/run/user",

            # ── 虚拟文件系统 ──
            "--proc", "/proc",
            "--dev", "/dev",

            # ── 进程隔离 ──
            "--unshare-pid",
            "--die-with-parent",
            "--chdir", "/workspace",

            # ── 网络隔离：STRICT 断网，其余共享 ──
            *(["--unshare-net"] if tier == SandboxTier.STRICT else ["--share-net"]),

            # ── 环境变量清零 ──
            "--clearenv",
        ])

        # ── 白名单环境变量注入 ──
        args.extend(["--setenv", "PATH", "/usr/bin:/bin"])
        args.extend(["--setenv", "HOME", "/workspace"])
        args.extend(["--setenv", "USER", "nobody"])
        for var in self._settings.default_env_allowlist:
            val = os.environ.get(var)
            if val is not None:
                args.extend(["--setenv", var, val])

        # ── 网络代理注入（STANDARD/TIGHT） ──
        if tier != SandboxTier.STRICT:
            proxy_url = os.environ.get("PRISMCLAW_SANDBOX_PROXY")
            if proxy_url:
                args.extend(["--setenv", "https_proxy", proxy_url])
                args.extend(["--setenv", "http_proxy", proxy_url])

        return args
