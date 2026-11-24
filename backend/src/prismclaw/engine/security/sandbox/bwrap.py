"""BwrapPolicy — 基于 bubblewrap 的进程级沙箱策略。

替代不可安装的 SrtPolicy，使用 Linux namespace + seccomp 内核级强制隔离。
per-user workspace 隔离：每个用户只看到自己的 /workspace。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from prismclaw.engine.security.sandbox.policy import SandboxBackend
from prismclaw.engine.security.sandbox.roots import runtime_readonly_roots
from prismclaw.schema.config.config import SandboxConfig
from prismclaw.schema.content.workspace import SandboxTier

if TYPE_CHECKING:
    from mcp import StdioServerParameters

logger = logging.getLogger(__name__)

# ── 硬编码安全底线：永不挂载到沙箱中的路径 ──────────────────────────────

_HARDCODED_DENY_BIND: tuple[str, ...] = (
    "/root",  # root 家目录
    "/home",  # 所有用户家目录（工作区通过 --bind 单独挂载）
    "/etc/ssh",  # SSH 配置
    "/etc/shadow",  # 密码文件
    "/etc/ssl/private",  # TLS 私钥
    "/var/lib/docker",  # Docker 数据
    "/var/lib/kubelet",  # K8s 数据
)


class BwrapBinaryNotFoundError(RuntimeError):
    """Raised when ``bwrap`` is required but missing on $PATH."""


def _resolve_runtime_root() -> Path | None:
    """解析 runtime 卷根；不可用时返回 None（沙箱仍可运行，只是没有技能包）。"""
    try:
        from prismclaw.infra.runtime_root import resolve_runtime_root

        return resolve_runtime_root()
    except Exception:
        logger.debug("runtime root 解析失败，bwrap 不挂载 runtime 卷", exc_info=True)
        return None


@lru_cache(maxsize=1)
def _proc_mount_supported() -> bool:
    """探测当前环境是否允许在 sandbox 内挂载 /proc。

    Docker（尤其 Desktop 的 LinuxKit）即使给了 SYS_ADMIN + seccomp/apparmor
    unconfined，仍可能返回 "Can't mount proc on /newroot/proc: Operation not
    permitted"。这个探测只做一次，不支持就不加 ``--proc``，退回容器自身的
    只读 /proc 视图——可见性不变，但沙箱里每条命令都能正常执行。
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    try:
        probe = subprocess.run(
            [bwrap, "--unshare-all", "--ro-bind", "/", "/", "--proc", "/proc", "true"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("bwrap proc 挂载探测失败", exc_info=True)
        return False
    return probe.returncode == 0


class BwrapCommandTooLongError(RuntimeError):
    """Raised when a bash command would exceed OS ARG_MAX after bwrap wrapping."""


class BwrapPolicy:
    """基于 bubblewrap 的进程级沙箱策略。

    生成 bwrap 命令行参数，在 Linux namespace + seccomp 内核级强制隔离中执行。

    - wrap_bash_command: 返回 (bwrap_path, [args..., /bin/sh, -c, cmd])
    - wrap_mcp_stdio:   包装 MCP stdio 进程
    - validate_path:    路径校验（原 srt_executor.SrtPolicy 能力合并）
    """

    backend: SandboxBackend = "bwrap"

    def __init__(self, *, settings: SandboxConfig) -> None:
        self._settings = settings
        path = shutil.which("bwrap")
        if path is None:
            raise BwrapBinaryNotFoundError(
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

    def wrap_bash_command(self, cmd: str, ctx: object) -> tuple[str, list[str]]:
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
        params: StdioServerParameters,
        server_name: str,
        sandbox_config: Mapping[str, object],
    ) -> StdioServerParameters:
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

        与 ``RuntimeExecutor`` 共用 ``security/sandbox/roots.py`` 的单一定义：
        只放行 ``~/.prismclaw/assets/``（静态能力），以及项目内 ``backend/configs``
        与引擎源码（agent 自省需要）。

        **不放行整个 ``~/.prismclaw``**：同层还有 ``workspaces/``（其它用户数据）
        与 ``data/`` / ``plugins-data/``（凭据与插件持久数据）。
        """
        roots: list[Path] = list(runtime_readonly_roots())
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

    def validate_path(self, path: str, tier: SandboxTier, workspace_root: str) -> str | None:
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

    def _build_bwrap_args(self, workspace_root: str, tier: SandboxTier) -> list[str]:
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

        # 运行时卷与静态能力的只读挂载点。二者都不在 /usr 体系内：
        # 技能安装的包在 runtime 卷，skills/plugins/agents 在 assets 下。
        runtime_root = _resolve_runtime_root()
        runtime_binds: list[str] = []
        if runtime_root is not None:
            runtime_binds.extend(["--ro-bind", str(runtime_root), str(runtime_root)])
        for root in self._runtime_readonly_roots:
            runtime_binds.extend(["--ro-bind", str(root), str(root)])
            if root.name == "assets":
                # 沙箱内 HOME=/workspace；把 assets 同时映射过去，让文档与技能
                # 脚本里 `~/.prismclaw/assets/...` 的写法在隔离环境中仍可解析。
                runtime_binds.extend(["--ro-bind", str(root), "/workspace/.prismclaw/assets"])

        args = [
            # ── 系统目录只读挂载 ──
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/sbin",
            "/sbin",
            # ── 必要的系统配置文件（只读）──
            "--ro-bind",
            "/etc/resolv.conf",
            "/etc/resolv.conf",
            "--ro-bind",
            "/etc/ssl",
            "/etc/ssl",
            "--ro-bind",
            "/etc/passwd",
            "/etc/passwd",
            "--ro-bind",
            "/etc/group",
            "/etc/group",
            "--ro-bind",
            "/etc/hosts",
            "/etc/hosts",
            "--ro-bind",
            "/etc/nsswitch.conf",
            "/etc/nsswitch.conf",
            "--ro-bind",
            "/etc/ld.so.cache",
            "/etc/ld.so.cache",
        ]

        # lib64（部分 Linux 发行版需要）
        if os.path.isdir("/lib64"):
            args.extend(["--ro-bind", "/lib64", "/lib64"])

        args.extend(
            [
                # ── 用户工作区读写挂载 ──
                "--bind",
                workspace_root,
                "/workspace",
                # ── 运行时卷与静态能力（只读，挂在 workspace 之后以覆盖嵌套路径）──
                *runtime_binds,
                # ── 隔离临时目录 ──
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run/user",
                # ── 虚拟文件系统 ──
                # Docker（含 Desktop 的 LinuxKit）可能拒绝在 sandbox 内 mount proc，
                # 实测报 "Can't mount proc on /newroot/proc: Operation not permitted"。
                # 不支持时退回容器自身的只读 /proc 视图：可见性不变，但不会让每条
                # 命令都失败。
                *(["--proc", "/proc"] if _proc_mount_supported() else []),
                "--dev",
                "/dev",
                # ── 进程隔离 ──
                "--unshare-pid",
                "--die-with-parent",
                "--chdir",
                "/workspace",
                # ── 网络隔离：STRICT 断网，其余共享 ──
                *(["--unshare-net"] if tier == SandboxTier.STRICT else ["--share-net"]),
                # ── 环境变量清零 ──
                "--clearenv",
            ]
        )

        # ── 白名单环境变量注入 ──
        args.extend(["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"])
        args.extend(["--setenv", "HOME", "/workspace"])
        args.extend(["--setenv", "USER", "nobody"])
        # --clearenv 之后必须重新注入，否则沙箱内 import 不到技能安装的包。
        if runtime_root is not None:
            skill_runtime = runtime_root / "skills" / "_default"
            args.extend(["--setenv", "PRISMCLAW_RUNTIME_ROOT", str(runtime_root)])
            args.extend(["--setenv", "PRISMCLAW_SKILL_RUNTIME", str(skill_runtime)])
            site_packages = skill_runtime / "site-packages"
            if site_packages.is_dir():
                args.extend(["--setenv", "PYTHONPATH", str(site_packages)])
            node_modules = skill_runtime / "node_modules"
            if node_modules.is_dir():
                args.extend(["--setenv", "NODE_PATH", str(node_modules)])
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
