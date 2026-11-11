from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CommandResult:
    """bash 命令执行结果。

    frozen dataclass，无外部依赖，可以放在 contracts 层。

    sandbox_tier_used 为 str 类型（如 "standard", "strict"），
    对应 SandboxTier 枚举的 .value。实现类构造时直接赋值 str。
    """
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    sandbox_tier_used: str = "standard"


@dataclass(frozen=True)
class FileResult:
    """文件操作结果。

    frozen dataclass，无外部依赖，可以放在 contracts 层。
    """
    success: bool
    content: str | bytes | None = None
    error: str | None = None
    http_status_code: int | None = None
    http_headers: dict[str, str] | None = None


@runtime_checkable
class ExecutionEnv(Protocol):
    """工具执行环境的统一契约。

    直接对齐 WorkspaceRuntimeClient / InProcessRuntimeClient 的公共接口。
    返回类型按实际实现声明（部分方法返回 dict 而非 FileResult）。
    实现类可带有额外参数（带默认值），Protocol 只声明消费者必需的最小签名。

    工具代码零改动：属性访问 (.stdout, .success, dict["key"]) 不受影响。
    """

    # ---- 命令执行 ----
    async def bash(
        self, command: str, *, tier: str = "standard",
        timeout_seconds: int | None = None,
        soft_timeout: int | None = None,
        hard_timeout: int | None = None,
        template_id: str = "__default__",
        workspace_id: str | None = None,
        workspace_root: str | None = None,
    ) -> CommandResult | dict[str, Any]: ...

    # ---- 文件操作 ----
    async def read_text(self, path: str, *, encoding: str = "utf-8",
                        template_id: str = "__default__",
                        workspace_id: str | None = None,
                        workspace_root: str | None = None) -> FileResult: ...

    async def write_text(self, path: str, content: str, *, encoding: str = "utf-8",
                         template_id: str = "__default__",
                         workspace_id: str | None = None,
                         workspace_root: str | None = None) -> FileResult: ...

    async def read_bytes(self, path: str, *,
                         template_id: str = "__default__",
                         workspace_id: str | None = None,
                         workspace_root: str | None = None) -> FileResult: ...

    async def write_bytes(self, path: str, content: bytes, *,
                          template_id: str = "__default__",
                          workspace_id: str | None = None,
                          workspace_root: str | None = None) -> FileResult: ...

    async def write_stream(self, path: str, content: Any, *,
                           template_id: str = "__default__",
                           workspace_id: str | None = None,
                           workspace_root: str | None = None) -> FileResult: ...

    # ---- HTTP 代理 ----
    async def http_get(self, url: str, *,
                       template_id: str = "__default__",
                       workspace_id: str | None = None,
                       workspace_root: str | None = None) -> FileResult: ...

    async def http_post(self, url: str, *,
                        template_id: str = "__default__",
                        workspace_id: str | None = None,
                        workspace_root: str | None = None) -> FileResult: ...

    # ---- 文件系统 ----
    async def fs_list(self, path: str = ".", *,
                      template_id: str = "__default__",
                      workspace_id: str | None = None,
                      workspace_root: str | None = None) -> dict[str, Any]: ...

    async def fs_glob(self, pattern: str = "*", *,
                      template_id: str = "__default__",
                      workspace_id: str | None = None,
                      workspace_root: str | None = None) -> dict[str, Any]: ...

    async def fs_grep(self, pattern: str, *,
                      template_id: str = "__default__",
                      workspace_id: str | None = None,
                      workspace_root: str | None = None) -> dict[str, Any]: ...

    # ---- 沙箱/健康 ----
    async def health(self, *,
                     template_id: str = "__default__",
                     workspace_id: str | None = None,
                     workspace_root: str | None = None) -> bool: ...

    # ---- MCP ----
    async def mcp_spawn(self, server_name: str, *,
                        template_id: str = "__default__",
                        workspace_id: str | None = None,
                        workspace_root: str | None = None) -> dict[str, Any]: ...

    async def mcp_call(self, server_name: str, tool_name: str, arguments: dict | None = None, *,
                       template_id: str = "__default__",
                       workspace_id: str | None = None,
                       workspace_root: str | None = None) -> dict[str, Any]: ...

    # ---- 归档 ----
    async def archive_create(self, name: str, *,
                             template_id: str = "__default__",
                             workspace_id: str | None = None,
                             workspace_root: str | None = None) -> dict[str, Any]: ...

    async def archive_restore(self, archive_id: str, *,
                              template_id: str = "__default__",
                              workspace_id: str | None = None,
                              workspace_root: str | None = None) -> dict[str, Any]: ...

    async def archive_list(self, *,
                           template_id: str = "__default__",
                           workspace_id: str | None = None,
                           workspace_root: str | None = None) -> dict[str, Any]: ...

    # ---- 后台任务 ----
    async def list_jobs(self, *,
                        template_id: str = "__default__",
                        workspace_id: str | None = None,
                        workspace_root: str | None = None) -> list[dict[str, Any]]: ...

    async def job_status(self, pid: int, *,
                         template_id: str = "__default__",
                         workspace_id: str | None = None,
                         workspace_root: str | None = None) -> dict[str, Any] | None: ...

    async def job_kill(self, pid: int, *,
                       template_id: str = "__default__",
                       workspace_id: str | None = None,
                       workspace_root: str | None = None) -> bool: ...

    # ---- 生命周期 ----
    async def close(self) -> None: ...
    # WorkspaceRuntimeClient.close() 是 async def（关闭 HTTP 连接池）。
    # InProcessRuntimeClient.close() 也声明为 async def（进程内无需关闭，空实现）。


__all__ = ["CommandResult", "FileResult", "ExecutionEnv"]
