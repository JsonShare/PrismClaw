"""P1-RUN-04 + P4-TOOL-01：RuntimeExecutor — 唯一执行核心。

本模块是 Runtime-Agent 的**执行核心**，被两处复用，保证生产/开发/测试
使用**逐字节一致**的执行逻辑：

  1. HTTP 服务端（``server.py``）：每个工作区容器里跑一个，委托本类执行；
  2. 进程内传输（``InProcessRuntimeClient``）：开发/测试无 Docker 时，
     直接调用本类，避免引入任何分叉的降级实现。

执行层策略：
  - RuleGate 入口校验（HMAC 由传输层处理，这里做静态规则 + SSRF）；
  - BwrapPolicy / SandboxPolicy 执行层校验（路径/网络/进程隔离）；
  - 路径解析以 ``workspace_root`` 为基准，杜绝相对路径误解析到进程 cwd。

后台任务：``bash(background=True)`` 返回 job_id（=OS pid），由调用方注册；
``job_status`` / ``job_kill`` / ``list_jobs`` 提供与 ``jobs`` 工具一致的接口。
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.engine.security.gate.routing.rule_gate import RuleGate
from prismclaw.engine.security.gate.routing.risk_router import (
    DataClassification,
    Reversibility,
    SideEffectScope,
    ToolSafetyMeta,
    get_tool_safety_meta,
)
from prismclaw.engine.security.sandbox.bwrap import BwrapPolicy
from prismclaw.engine.security.sandbox.roots import runtime_readonly_roots

logger = logging.getLogger(__name__)

# 落盘引用（tr_/mc_/cb_）的文件名形如 ``<前缀>_<id>_<sha256[:12]>.txt``，其中 id 与
# 哈希都可能被模型抄错。id（tool_use_id）在引用文本的 ``tool_use_id:`` 字段里另有一份，
# 短、有位置语义，实测抄得住；哈希只是一段不可推导的十六进制 —— 实测模型把 12 位哈希
# ``99b546c97239`` 写成了另一个 9 位十六进制 ``4591cc389``（会话
# web:admin:s:72a5d95bef94006d，2026-10-20），而文件与注入的引用都是正确的，读工具
# 按精确路径解析、零容错，于是 ``read: file not found``。
# 因此让哈希段不再承重：精确路径未命中时，若同一目录下 ``<前缀>_<id>_*.txt`` 存在且
# **唯一**，就按它读取。只在唯一命中时回退 —— 宁可报 not found，也不猜歧义结果。
_PERSISTED_REFERENCE_PREFIXES = ("tr_", "mc_", "cb_")
_HEX_DIGITS = frozenset("0123456789abcdef")


def _resolve_persisted_reference_alias(path: Path) -> Path | None:
    """把「内容哈希被抄错」的落盘引用路径映射回真实文件；无法唯一确定时返回 ``None``。

    哈希段按**最后一个** ``_`` 切分，因此不会把 id 里自带的 ``_``（如 ``call_962e97…``）
    误当分隔符。
    """
    if path.suffix != ".txt":
        return None
    stem, sep, tail = path.stem.rpartition("_")
    if not sep or len(tail) < 6 or not _HEX_DIGITS.issuperset(tail):
        return None
    if not stem.startswith(_PERSISTED_REFERENCE_PREFIXES):
        return None
    try:
        matches = [c for c in path.parent.glob(f"{stem}_*.txt") if c.is_file()]
    except OSError:
        return None
    return matches[0] if len(matches) == 1 else None


@dataclass
class _JobEntry:
    """后台任务记录。"""

    pid: int
    command: str
    proc: asyncio.subprocess.Process
    job_dir: Path
    stdout_path: Path
    stderr_path: Path
    started_at: float = field(default_factory=time.time)
    # 流式写入任务（把 proc 的输出落盘）
    pump_tasks: list[asyncio.Task] = field(default_factory=list)


class RuntimeExecutor:
    """工作区执行核心：bash / 文件 IO / http / mcp。

    生产（容器内 HTTP 服务端）与开发/测试（进程内传输）共用此类，
    因此两者的执行语义（RuleGate / BwrapPolicy / 路径 / 后台任务）完全一致。
    """

    def __init__(
        self,
        workspace_root: str,
        *,
        allow_net: bool = True,
        workspace_id: str = "unknown",
        sandbox_policy: BwrapPolicy | None = None,
    ) -> None:
        self._workspace_root = str(workspace_root)
        self._allow_net = allow_net
        self._workspace_id = workspace_id
        self._sandbox_policy = sandbox_policy
        # 静态能力根（只读）：唯一来源见 security/sandbox/roots.py
        self._readonly_roots: tuple[Path, ...] = runtime_readonly_roots()
        self._rule_gate = RuleGate()
        self._jobs: dict[int, _JobEntry] = {}
        self._mcp_processes: dict[str, asyncio.subprocess.Process] = {}
        self._mcp_request_counter = itertools.count(1)

    # ── 请求根与路径授权 ──────────────────────────────────────────────

    def _request_root(self, workspace_root: str | None) -> Path:
        """解析请求级 workspace 根；必须收窄在 executor 自身根内。

        一个 agent 进程可以服务其根下的多个工作区（共享 agent 模式），
        因此请求可以**收窄**作用域，但绝不能扩大：请求根若不在实例根内，
        等于把单个请求提权到宿主任意目录。
        """
        base = Path(self._workspace_root).expanduser().resolve()
        if not workspace_root:
            return base
        try:
            candidate = Path(workspace_root).expanduser().resolve()
        except OSError as exc:
            raise ValueError(f"invalid workspace_root: {workspace_root!r}") from exc
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                f"workspace_root {candidate} is not within {base}"
            ) from exc
        return candidate

    def _bound_root(self, workspace_root: str | None) -> tuple[Path | None, str | None]:
        """``_request_root`` 的非抛异常包装，供文件方法统一返回错误。"""
        try:
            return self._request_root(workspace_root), None
        except ValueError as exc:
            return None, str(exc)

    def _resolve_path(self, path: str, root: Path) -> Path:
        """把请求路径解析为 ``root`` 内的绝对路径（解析符号链接）。

        相对路径 join 到 root；绝对路径保持原样，由 :meth:`_authorize` 校验边界。
        统一 ``.resolve()``，避免 macOS ``/var``→``/private/var`` 之类的符号链接
        让校验与落盘路径不一致。

        **expanduser first**: ``Path("~/.prismclaw/x").is_absolute()`` 返回
        ``False``（``~`` 不是 pathlib 的根组件），若不先展开就会拼成
        ``<root>~/.prismclaw/x`` 这种含字面 ``~`` 的路径。
        """
        p = Path(path.strip()).expanduser()
        if not p.is_absolute():
            p = root / p
        return p.resolve()

    def _authorize(
        self,
        path: str,
        *,
        root: Path,
        write: bool,
    ) -> tuple[Path | None, str | None]:
        """解析并授权一个路径，返回 ``(resolved, error)``。

        - 读：允许 ``root`` 内 + 静态能力根（assets/，只读）；
        - 写：只允许 ``root`` 内 —— 只读根对写操作不生效。
        """
        try:
            resolved = self._resolve_path(path, root)
        except OSError as exc:
            return None, f"invalid_path: {path}: {exc}"
        try:
            resolved.relative_to(root)
            return resolved, None
        except ValueError:
            pass
        if not write:
            for ro in self._readonly_roots:
                try:
                    resolved.relative_to(ro)
                    return resolved, None
                except ValueError:
                    continue
        return None, f"path_outside_workspace: {path} outside {root}"

    # ── bash ──────────────────────────────────────────────────────────

    async def bash(
        self,
        command: str,
        *,
        tier: SandboxTier = SandboxTier.STANDARD,
        soft_timeout: int = 30,
        hard_timeout: int | None = None,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """执行 bash 命令。

        行为（与原 host 端 BashTool 一致）：
          - 软超时（``soft_timeout``）内完成 → 同步返回结果；
          - 超过软超时 → **不杀进程**，detach 为后台任务并返回 job_id（pid）；
          - 硬超时（``hard_timeout``）到达 → 杀掉后台任务。

        返回：
          - 同步：``{"exit_code", "stdout", "stderr", "timed_out", "tier_used"}``
          - 后台：``{"detached": True, "job_id": pid, "pid": pid}``
        """
        try:
            root = self._request_root(workspace_root)
        except ValueError as exc:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"workspace_root rejected: {exc}",
                "timed_out": False,
                "tier_used": tier.value,
            }
        effective_workspace = str(root)

        # RuleGate: 入口静态规则校验
        meta = get_tool_safety_meta("bash")
        gate_result = self._rule_gate.check(
            tool_name="bash",
            arguments={"command": command},
            meta=meta,
            workspace_root=effective_workspace,
        )
        if not gate_result.allowed:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"RuleGate deny: {gate_result.reason}",
                "timed_out": False,
                "tier_used": tier.value,
            }

        # 网络检查（STRICT tier）
        if self._sandbox_policy is not None:
            net_check = self._sandbox_policy.check_network_access(tier)
            if net_check:
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"SandboxPolicy deny: {net_check}",
                    "timed_out": False,
                    "tier_used": tier.value,
                }

        # 构建沙箱上下文
        sandbox_ctx = SimpleNamespace(
            workspace_root=effective_workspace,
            sandbox_tier=tier,
        )

        try:
            if self._sandbox_policy is not None:
                # BwrapPolicy: 内核级隔离
                executable, args = self._sandbox_policy.wrap_bash_command(
                    command, sandbox_ctx
                )
                proc = await asyncio.create_subprocess_exec(
                    executable, *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                # 无沙箱策略（PassthroughPolicy）: 直接执行
                proc = await asyncio.create_subprocess_exec(
                    "/bin/sh", "-c", command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=effective_workspace,
                )
        except OSError as exc:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"spawn failed: {exc}",
                "timed_out": False,
                "tier_used": tier.value,
            }

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=soft_timeout)
            return {
                "exit_code": proc.returncode or 0,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "timed_out": False,
                "tier_used": tier.value,
            }
        except asyncio.TimeoutError:
            # 软超时：detach 为后台任务（不杀进程），注册硬超时杀手
            from prismclaw.infra.workspace_paths import work_dir

            job_dir = work_dir(effective_workspace, "run", "jobs") / str(proc.pid)
            job_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = job_dir / "stdout"
            stderr_path = job_dir / "stderr"
            entry = _JobEntry(
                pid=proc.pid,
                command=command,
                proc=proc,
                job_dir=job_dir,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            entry.pump_tasks = [
                asyncio.create_task(self._pump(proc.stdout, stdout_path)),
                asyncio.create_task(self._pump(proc.stderr, stderr_path)),
            ]
            self._jobs[proc.pid] = entry
            if hard_timeout is not None and hard_timeout > soft_timeout:
                asyncio.create_task(
                    self._hard_timeout_killer(proc, hard_timeout - soft_timeout, proc.pid)
                )
            return {"detached": True, "job_id": proc.pid, "pid": proc.pid}

    async def _hard_timeout_killer(
        self, proc: asyncio.subprocess.Process, delay: float, pid: int
    ) -> None:
        try:
            await asyncio.sleep(delay)
            if proc.returncode is None:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        finally:
            self._retire_job(pid)

    @staticmethod
    async def _pump(stream: asyncio.StreamReader | None, path: Path) -> None:
        if stream is None:
            return
        try:
            with path.open("wb") as f:
                async for chunk in stream:
                    f.write(chunk)
        except Exception:
            pass

    # ── 后台任务管理 ──────────────────────────────────────────────────

    def _retire_job(self, pid: int) -> None:
        """任务结束后回收内存记录与 ``run/jobs/<pid>`` 磁盘目录。

        stdout/stderr 只在任务存活期间有意义（``job_status`` 会读它们）；
        完成后条目不对外可见，文件不该继续占盘。
        """
        entry = self._jobs.pop(pid, None)
        if entry is None:
            return
        for task in entry.pump_tasks:
            task.cancel()
        shutil.rmtree(entry.job_dir, ignore_errors=True)

    def job_status(self, pid: int) -> dict[str, Any] | None:
        entry = self._jobs.get(pid)
        if entry is None:
            return None
        done = entry.proc.returncode is not None
        status = "Completed" if done else "Running"
        extra: dict[str, Any] = {}
        if done:
            extra["exit_code"] = entry.proc.returncode
        try:
            extra["stdout_tail"] = entry.stdout_path.read_text(errors="replace")[-2000:] \
                if entry.stdout_path.exists() else ""
            extra["stderr_tail"] = entry.stderr_path.read_text(errors="replace")[-2000:] \
                if entry.stderr_path.exists() else ""
            extra["stdout_file"] = str(entry.stdout_path)
            extra["stderr_file"] = str(entry.stderr_path)
        except Exception:
            pass
        return {
            "pid": pid,
            "command": entry.command,
            "status": status,
            **extra,
        }

    def job_kill(self, pid: int) -> bool:
        entry = self._jobs.get(pid)
        if entry is None or entry.proc.returncode is not None:
            return False
        try:
            entry.proc.kill()
        except OSError:
            return False
        return True

    def list_jobs(self) -> list[dict[str, Any]]:
        out = []
        # 快照后再遍历：_retire_job 会从 _jobs 中删除条目。
        for pid, entry in list(self._jobs.items()):
            done = entry.proc.returncode is not None
            # 已完成的清理
            if done:
                self._retire_job(pid)
                continue
            out.append({"pid": pid, "command": entry.command, "status": "Running"})
        return out

    # ── 文件读取 ──────────────────────────────────────────────────────

    async def read_text(
        self, path: str, *, encoding: str = "utf-8", workspace_root: str | None = None
    ) -> dict[str, Any]:
        root, err = self._bound_root(workspace_root)
        if err:
            return {"success": False, "error": err}
        resolved, err = self._authorize(path, root=root, write=False)
        if err:
            return {"success": False, "error": err}
        try:
            p = resolved
            if not p.exists():
                # 落盘引用的内容哈希被抄错时，按 <前缀>_<id>_*.txt 唯一回退（见模块顶部注释）
                p = _resolve_persisted_reference_alias(p) or p
            if not p.exists():
                return {"success": False, "error": f"file not found: {path}"}
            return {"success": True, "content": p.read_text(encoding=encoding)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_bytes(
        self, path: str, *, workspace_root: str | None = None
    ) -> dict[str, Any]:
        root, err = self._bound_root(workspace_root)
        if err:
            return {"success": False, "error": err}
        resolved, err = self._authorize(path, root=root, write=False)
        if err:
            return {"success": False, "error": err}
        try:
            p = resolved
            if not p.exists():
                # 同上：哈希段不承重
                p = _resolve_persisted_reference_alias(p) or p
            if not p.exists():
                return {"success": False, "error": f"file not found: {path}"}
            return {"success": True, "content_b64": base64.b64encode(p.read_bytes()).decode()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 文件写入 ──────────────────────────────────────────────────────

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        root, err = self._bound_root(workspace_root)
        if err:
            return {"success": False, "error": err}
        p, err = self._authorize(path, root=root, write=True)
        if err:
            return {"success": False, "error": err}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=encoding)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def write_bytes(
        self, path: str, content: bytes, *, workspace_root: str | None = None
    ) -> dict[str, Any]:
        root, err = self._bound_root(workspace_root)
        if err:
            return {"success": False, "error": err}
        p, err = self._authorize(path, root=root, write=True)
        if err:
            return {"success": False, "error": err}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 分片流式写入 ──────────────────────────────────────────────────

    async def write_stream_begin(
        self,
        path: str,
        mode: str,
        encoding: str = "utf-8",
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        if not path:
            return {"success": False, "error": "path is required"}
        if mode not in ("text", "bytes"):
            return {"success": False, "error": f"invalid mode: {mode}"}
        root, err = self._bound_root(workspace_root)
        if err:
            return {"success": False, "error": err}
        p, err = self._authorize(path, root=root, write=True)
        if err:
            return {"success": False, "error": err}
        session_id = f"ws-{secrets.token_urlsafe(8)}"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            return {"success": False, "error": f"mkdir failed: {e}"}
        try:
            f = open(p, "wb") if mode == "bytes" else open(p, "w", encoding=encoding)
        except Exception as e:
            return {"success": False, "error": f"open failed: {e.__class__.__name__}: {e}"}
        self._write_sessions[session_id] = {
            "path": str(p),
            "mode": mode,
            "encoding": encoding,
            "file": f,
            "total_bytes": 0,
        }
        return {"session_id": session_id, "success": True}

    async def write_stream_chunk(self, session_id: str, data: str) -> dict[str, Any]:
        sess = self._write_sessions.get(session_id)
        if sess is None:
            return {"success": False, "error": f"invalid session: {session_id}"}
        try:
            if sess["mode"] == "bytes":
                raw = base64.b64decode(data)
                sess["file"].write(raw)
                sess["total_bytes"] += len(raw)
            else:
                sess["file"].write(data)
                sess["total_bytes"] += len(data.encode(sess["encoding"]))
            return {"success": True, "bytes_written": sess["total_bytes"]}
        except Exception as e:
            self._write_sessions.pop(session_id, None)
            try:
                sess["file"].close()
            except Exception:
                pass
            return {"success": False, "error": str(e)}

    async def write_stream_finish(self, session_id: str) -> dict[str, Any]:
        sess = self._write_sessions.pop(session_id, None)
        if sess is None:
            return {"success": False, "error": f"invalid session: {session_id}"}
        try:
            sess["file"].close()
            return {"success": True, "total_bytes": sess["total_bytes"]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── http_get ──────────────────────────────────────────────────────

    async def http_get(self, url: str, *, tier: SandboxTier = SandboxTier.STANDARD) -> dict[str, Any]:
        if not self._allow_net:
            return {"success": False, "error": "strict_deny_network"}
        if self._sandbox_policy is not None:
            net_check = self._sandbox_policy.check_network_access(tier)
            if net_check:
                return {"success": False, "error": f"SandboxPolicy deny: {net_check}"}
        # http_get 语义即公网只读（INTERNAL_READ/PUBLIC/REVERSIBLE），
        # 直接声明，不锚定任何具体 MCP 服务名。
        meta = ToolSafetyMeta(
            SideEffectScope.INTERNAL_READ, DataClassification.PUBLIC, Reversibility.REVERSIBLE
        )
        gate_result = self._rule_gate.check(
            tool_name="http_get",
            arguments={"url": url},
            meta=meta,
        )
        if not gate_result.allowed:
            return {"success": False, "error": f"RuleGate deny: {gate_result.reason}"}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                return {
                    "success": True,
                    "content": resp.text,
                    "status_code": resp.status_code,
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── mcp ───────────────────────────────────────────────────────────

    async def mcp_spawn(
        self, server_name: str, *, transport: str = "stdio", args: list[str] | None = None, env: dict[str, str] | None = None
    ) -> dict[str, Any]:
        if not server_name:
            return {"success": False, "error": "server_name required"}
        try:
            cmd = args if args else [server_name]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env} if env else None,
            )
            self._mcp_processes[server_name] = proc
            return {"success": True, "pid": proc.pid, "server_name": server_name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def mcp_call(self, server_name: str, tool_name: str, arguments: dict | None = None) -> dict[str, Any]:
        if not server_name or not tool_name:
            return {"success": False, "error": "server_name and tool_name required"}
        proc = self._mcp_processes.get(server_name)
        if proc is None or proc.returncode is not None:
            return {"success": False, "error": f"MCP server {server_name} not running"}
        try:
            request_id = next(self._mcp_request_counter)
            rpc_request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            }
            proc.stdin.write((json.dumps(rpc_request) + "\n").encode())
            await proc.stdin.drain()
            response_line = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
            rpc_response = json.loads(response_line)
            return {"success": True, "result": rpc_response.get("result", {})}
        except asyncio.TimeoutError:
            return {"success": False, "error": "MCP call timed out (30s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 结构化文件系统查询（HTTP 与进程内共用本实现）───────────────

    async def fs_list(
        self,
        path: str = ".",
        *,
        recursive: bool = False,
        ignore_hidden: bool = True,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        root, err = self._bound_root(workspace_root)
        if err:
            return {"entries": [], "error": err}
        base, err = self._authorize(path, root=root, write=False)
        if err:
            return {"entries": [], "error": err}
        if not base.is_dir():
            return {"entries": [], "error": f"directory not found: {path}"}
        entries: list[dict[str, Any]] = []
        try:
            if recursive:
                for p in sorted(base.rglob("*")):
                    rel = p.relative_to(base)
                    if ignore_hidden and any(part.startswith(".") for part in rel.parts):
                        continue
                    st = p.stat()
                    entries.append({
                        "name": p.name,
                        "path": str(rel),
                        "is_dir": p.is_dir(),
                        "size": 0 if p.is_dir() else st.st_size,
                        "modified_epoch": st.st_mtime,
                    })
            else:
                for p in base.iterdir():
                    if ignore_hidden and p.name.startswith("."):
                        continue
                    st = p.stat()
                    entries.append({
                        "name": p.name,
                        "path": p.name,
                        "is_dir": p.is_dir(),
                        "size": 0 if p.is_dir() else st.st_size,
                        "modified_epoch": st.st_mtime,
                    })
        except OSError as e:
            return {"entries": entries, "error": str(e)}
        return {"entries": entries}

    async def fs_glob(
        self,
        pattern: str = "*",
        path: str = ".",
        *,
        max_results: int = 200,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        root, err = self._bound_root(workspace_root)
        if err:
            return {"entries": [], "error": err}
        base, err = self._authorize(path, root=root, write=False)
        if err:
            return {"entries": [], "error": err}
        entries: list[dict[str, Any]] = []
        for p in sorted(
            base.glob(pattern),
            key=lambda x: x.stat().st_mtime if x.exists() else 0,
            reverse=True,
        ):
            if not p.exists():
                continue
            st = p.stat()
            entries.append({
                "path": str(p.relative_to(base)),
                "size": st.st_size,
                "modified_epoch": st.st_mtime,
            })
            if len(entries) >= max_results:
                break
        return {"entries": entries, "truncated": len(entries) >= max_results}

    async def fs_grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        include: str = "*",
        case_insensitive: bool = False,
        max_matches: int = 200,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        import re as _re

        root, err = self._bound_root(workspace_root)
        if err:
            return {"matches": [], "error": err}
        base, err = self._authorize(path, root=root, write=False)
        if err:
            return {"matches": [], "error": err}
        flags = _re.IGNORECASE if case_insensitive else 0
        matches: list[dict[str, Any]] = []
        for p in base.rglob(include):
            if not p.is_file():
                continue
            try:
                if b"\x00" in p.read_bytes()[:8192]:
                    continue
                with open(p, "r", errors="replace") as f:
                    for line_no, line in enumerate(f, 1):
                        if _re.search(pattern, line, flags):
                            matches.append({
                                "path": str(p.relative_to(base)),
                                "line_number": line_no,
                                "line": line.rstrip("\n"),
                            })
                            if len(matches) >= max_matches:
                                break
            except OSError:
                continue
            if len(matches) >= max_matches:
                break
        return {"matches": matches, "truncated": len(matches) >= max_matches}

    # 分片写入会话（实例级，区别于模块全局）
    _write_sessions: dict[str, dict[str, Any]] = {}
