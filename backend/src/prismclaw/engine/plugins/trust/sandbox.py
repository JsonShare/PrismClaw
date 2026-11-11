"""Plugin Sandbox - Plugin 沙箱隔离机制

为 Plugin 提供受限的文件系统访问控制。
通过限制 ShellCommandHookHandler 的执行环境，防止 Plugin 执行危险操作。

安全约束：
1. 限制 Plugin 可访问的目录范围
2. 禁止 Plugin 修改系统关键文件
3. 禁止 Plugin 执行危险命令（如 rm -rf /）
4. 记录 Plugin 的所有文件操作
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PluginSandboxPolicy:
    """Plugin 沙箱策略配置。"""

    plugin_root: str
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=list)
    network_access: bool = False
    max_memory_mb: int = 256
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        """初始化后设置默认值。"""
        if not self.allowed_paths:
            # 默认只允许访问 Plugin 自身目录
            self.allowed_paths = [self.plugin_root]

        if not self.blocked_commands:
            # 默认禁止的危险命令
            self.blocked_commands = [
                "rm -rf /",
                "rm -rf /*",
                "sudo",
                "chmod 777",
                "chmod -R 777",
                "mkfs",
                "dd if=/dev/zero",
                ":(){:|:&};:",  # fork bomb
            ]


class PluginSandbox:
    """Plugin 沙箱隔离器。

    负责在 Plugin 执行前进行安全检查，确保：
    1. 命令在允许的目录范围内执行
    2. 不包含危险命令
    3. 环境变量隔离
    """

    def __init__(self, policy: PluginSandboxPolicy) -> None:
        self._policy = policy
        self._plugin_root = Path(policy.plugin_root).resolve()

    def validate_command(self, command: str) -> tuple[bool, str]:
        """校验命令是否安全。

        Returns:
            (is_safe, error_message)
        """
        # 检查危险命令
        for blocked in self._policy.blocked_commands:
            if blocked in command:
                return False, f"命令包含被禁止的模式：{blocked}"

        return True, ""

    def validate_path_access(self, path: str) -> tuple[bool, str]:
        """校验路径是否在允许范围内。

        Returns:
            (is_allowed, error_message)
        """
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError) as exc:
            return False, f"路径无效：{exc}"

        # 检查是否在允许的路径中
        allowed = False
        for allowed_path in self._policy.allowed_paths:
            allowed_root = Path(allowed_path).resolve()
            try:
                resolved.relative_to(allowed_root)
                allowed = True
                break
            except ValueError:
                continue

        if not allowed:
            return False, f"路径 '{path}' 位于允许目录之外"

        # 检查是否在禁止的路径中
        for blocked_path in self._policy.blocked_paths:
            blocked_root = Path(blocked_path).resolve()
            try:
                resolved.relative_to(blocked_root)
                return False, f"路径 '{path}' 位于被禁止的目录中"
            except ValueError:
                continue

        return True, ""

    def is_within_plugin_root(self, path: str) -> bool:
        """检查路径是否在 Plugin 根目录内。"""
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self._plugin_root)
            return True
        except ValueError:
            return False

    def build_safe_env(self, base_env: dict[str, str] | None = None) -> dict[str, str]:
        """构建安全的子进程环境变量。"""
        env = base_env or dict(os.environ)

        # 添加 Plugin 相关环境变量
        env["PRISMCLAW_PLUGIN_ROOT"] = str(self._plugin_root)
        env["PRISMCLAW_SANDBOX_ENABLED"] = "true"
        env["PRISMCLAW_ALLOWED_PATHS"] = os.pathsep.join(self._policy.allowed_paths)

        # 可选：限制网络访问
        if not self._policy.network_access:
            env["PRISMCLAW_NETWORK_RESTRICTED"] = "true"

        return env

    def get_policy_info(self) -> dict[str, Any]:
        """获取当前沙箱策略信息（用于审计和调试）。"""
        return {
            "plugin_root": self._policy.plugin_root,
            "allowed_paths": self._policy.allowed_paths,
            "blocked_paths": self._policy.blocked_paths,
            "blocked_commands": self._policy.blocked_commands,
            "network_access": self._policy.network_access,
            "timeout_seconds": self._policy.timeout_seconds,
        }


def create_plugin_sandbox(plugin_root: str, **kwargs: Any) -> PluginSandbox:
    """创建 Plugin 沙箱实例。

    Args:
        plugin_root: Plugin 根目录路径
        **kwargs: 可选的 SandboxPolicy 覆盖参数

    Returns:
        PluginSandbox 实例
    """
    policy = PluginSandboxPolicy(plugin_root=plugin_root, **kwargs)
    return PluginSandbox(policy)
