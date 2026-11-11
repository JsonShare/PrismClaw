"""P1-RUN-04 + P4-TOOL-01：Runtime-Agent — FastAPI 服务端。

服务端只做「传输 + 校验」，所有执行逻辑委托给 :class:`RuntimeExecutor`，
保证与开发/测试使用的进程内传输**逐字节一致**的执行语义。

  POST /bash                — 执行 bash（软超时内同步；超时 detach 为后台任务）
  POST /bash/jobs          — 列出后台任务
  GET  /bash/jobs/{pid}    — 后台任务状态
  POST /bash/jobs/{pid}/kill — 杀掉后台任务
  POST /read               — 读取文件
  POST /write              — 写入文件
  POST /write_stream       — 分片流式写入文件
  POST /http_get           — 通过代理获取 HTTP 资源
  POST /mcp/spawn          — 启动 MCP 服务器
  POST /mcp/call           — 调用 MCP 工具
  POST /fs/list              — 结构化目录列表
  POST /fs/glob              — 结构化文件查找
  POST /fs/grep              — 结构化内容搜索
  POST /http_post            — HTTP POST 请求
  GET  /health             — 健康检查

安全：
  - 入口层 HMAC 校验 + workspace_id 匹配（容器外不接受请求）；
  - 执行层 RuleGate + BwrapPolicy 在 RuntimeExecutor 内再次校验。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from prismclaw.engine.runtime.agent.executor import RuntimeExecutor
from prismclaw.engine.security.sandbox.bwrap import BwrapPolicy
from prismclaw.schema.content.workspace import SandboxTier

logger = logging.getLogger(__name__)

# ── 配置 ────────────────────────────────────────────────────────────────

HMAC_SECRET = os.getenv("PRISMCLAW_RUNTIME_HMAC_SECRET", "dev-secret")
HMAC_KEY_VERSION = os.getenv("PRISMCLAW_RUNTIME_HMAC_KEY_VERSION", "1")
WORKSPACE_ID = os.getenv("PRISMCLAW_WORKSPACE_ID", "unknown")
WORKSPACE_ROOT = os.getenv("PRISMCLAW_WORKSPACE_ROOT", "/workspace")
ALLOW_NET = os.getenv("PRISMCLAW_ALLOW_NET", "true").lower() == "true"

# ── FastAPI app ────────────────────────────────────────────────────────

app = FastAPI(title="PrismClaw Runtime-Agent", version="1.0.0")

# 沙箱策略：从环境变量或 app.state 获取
_SANDBOX_ENFORCE = os.getenv("PRISMCLAW_SANDBOX_ENFORCE", "true").lower() == "true"


def _resolve_sandbox_policy() -> BwrapPolicy | None:
    """解析沙箱策略：enforce=True 时必须 BwrapPolicy，否则 None。"""
    if not _SANDBOX_ENFORCE:
        return None
    try:
        from prismclaw.engine.security.sandbox.state import resolve_sandbox_state
        from prismclaw.schema.config.config import SandboxConfig
        state = resolve_sandbox_state(SandboxConfig(enforce=True))
        if isinstance(state.policy, BwrapPolicy):
            return state.policy
        return None
    except Exception:
        logger.warning("bwrap sandbox policy unavailable, running without process isolation")
        return None


sandbox_policy = _resolve_sandbox_policy()

# 唯一执行核心（每个工作区容器一个实例）
executor = RuntimeExecutor(
    workspace_root=WORKSPACE_ROOT,
    allow_net=ALLOW_NET,
    workspace_id=WORKSPACE_ID,
    sandbox_policy=sandbox_policy,
)


# ── HMAC 验证中间件 ────────────────────────────────────────────────────


def _verify_hmac(body: bytes, signature: str) -> bool:
    if not signature:
        return False
    try:
        parts = signature.split(":", 1)
        if len(parts) != 2 or not parts[0].startswith("v"):
            return False
        expected = f"v{HMAC_KEY_VERSION}:{hmac.new(HMAC_SECRET.encode(), body, hashlib.sha256).hexdigest()}"
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


@app.middleware("http")
async def verify_hmac_middleware(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)

    body = await request.body()
    signature = request.headers.get("X-HMAC-Signature", "")
    ws_id = request.headers.get("X-Workspace-ID", "")

    if not _verify_hmac(body, signature):
        logger.warning("HMAC verification failed for %s", request.url.path)
        return JSONResponse(status_code=401, content={"error": "hmac_verification_failed"})

    if ws_id != WORKSPACE_ID:
        logger.warning("Workspace ID mismatch: got %s, expected %s", ws_id, WORKSPACE_ID)
        return JSONResponse(status_code=403, content={"error": "workspace_id_mismatch"})

    return await call_next(request)


# ── 安全校验工具 ──────────────────────────────────────────────────────


def _validate_path_within_workspace(path: str, *, base: str = WORKSPACE_ROOT) -> str | None:
    """校验路径是否在 workspace 边界内。返回校验后的绝对路径，或 None 表示越界。

    放行规则（按优先级）：
      1. workspace_root 内 → 放行（用户数据区）
      2. 运行时只读根（.work/、backend/configs/、backend/src/prismclaw/、~/.prismclaw/）→ 放行（agent 读自己的定义）
      3. 系统只读路径 → 放行（/usr /lib /bin /sbin /etc）
      4. 其他 → 拦截

    路径解析顺序很关键：**先 expanduser 再判断绝对/相对**。
    ``os.path.join(base, "~/.prismclaw/x")`` 会产生 ``base/~/.prismclaw/x``
    （字面 ``~`` 组件，不被后续 ``expanduser`` 展开），导致 home-relative
    路径永远匹配不到。
    """
    from pathlib import Path as _P

    try:
        raw = path.strip()
        expanded = _P(raw).expanduser()
        if expanded.is_absolute():
            resolved = expanded.resolve()
        else:
            resolved = _P(base).expanduser().resolve() / expanded
        ws_root = _P(base).resolve()
    except Exception:
        return None

    # 1. workspace 内
    try:
        resolved.relative_to(ws_root)
        return str(resolved)
    except ValueError:
        pass

    # 2. 运行时只读根（RuntimeExecutor._compute_runtime_readonly_roots 同源）
    try:
        from prismclaw.engine.runtime.agent.executor import RuntimeExecutor
        for ro in RuntimeExecutor._compute_runtime_readonly_roots():
            ro_expanded = _P(ro).expanduser()
            try:
                ro_resolved = ro_expanded.resolve() if ro_expanded.is_absolute() else (ws_root / ro_expanded).resolve()
                resolved.relative_to(ro_resolved)
                return str(resolved)
            except ValueError:
                continue
    except Exception:
        pass

    # 3. 系统只读路径
    if str(resolved).startswith(("/usr", "/lib", "/bin", "/sbin", "/etc")):
        return str(resolved)

    # 4. 越界
    return None


# ── Endpoint: /bash ────────────────────────────────────────────────────


@app.post("/bash")
async def exec_bash(request: Request):
    body = await request.json()
    command = body.get("command", "")
    tier_str = body.get("tier", "standard")
    try:
        tier = SandboxTier(tier_str)
    except ValueError:
        tier = SandboxTier.STANDARD
    soft_timeout = int(body.get("soft_timeout", body.get("timeout_seconds", 30)))
    hard_timeout = body.get("hard_timeout")
    workspace_root = body.get("workspace_root")
    # 安全校验：workspace_root 必须是 WORKSPACE_ROOT 或其子路径
    if workspace_root is not None:
        validated = _validate_path_within_workspace(workspace_root, base=WORKSPACE_ROOT)
        if validated is None:
            logger.warning(
                "workspace_root=%r rejected: outside WORKSPACE_ROOT=%r",
                workspace_root, WORKSPACE_ROOT,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "workspace_root_outside_boundary", "detail": f"workspace_root must be within {WORKSPACE_ROOT}"},
            )
        workspace_root = validated
    return await executor.bash(
        command,
        tier=tier,
        soft_timeout=soft_timeout,
        hard_timeout=int(hard_timeout) if hard_timeout is not None else None,
        workspace_root=workspace_root,
    )


@app.get("/bash/jobs")
async def list_jobs():
    return {"jobs": executor.list_jobs()}


@app.get("/bash/jobs/{pid}")
async def job_status(pid: int):
    status = executor.job_status(pid)
    if status is None:
        return JSONResponse(status_code=404, content={"error": "no such job", "pid": pid})
    return status


@app.post("/bash/jobs/{pid}/kill")
async def job_kill(pid: int):
    ok = executor.job_kill(pid)
    return {"killed": ok, "pid": pid}


# ── Endpoint: /read ────────────────────────────────────────────────────


@app.post("/read")
async def read_file(request: Request):
    body = await request.json()
    path = body.get("path", "")
    mode = body.get("mode", "text")
    if mode == "bytes":
        return await executor.read_bytes(path)
    return await executor.read_text(path, encoding=body.get("encoding", "utf-8"))


# ── Endpoint: /write ───────────────────────────────────────────────────


@app.post("/write")
async def write_file(request: Request):
    body = await request.json()
    path = body.get("path", "")
    mode = body.get("mode", "text")
    if mode == "bytes":
        return await executor.write_bytes(path, base64.b64decode(body.get("content_b64", "")))
    return await executor.write_text(path, body.get("content", ""), encoding=body.get("encoding", "utf-8"))


# ── Endpoint: /write_stream ────────────────────────────────────────────


@app.post("/write_stream")
async def write_stream(request: Request):
    body = await request.json()
    action = body.get("action", "")
    if action == "begin":
        return await executor.write_stream_begin(
            body.get("path", ""), body.get("mode", "text"), body.get("encoding", "utf-8")
        )
    if action == "chunk":
        return await executor.write_stream_chunk(body.get("session_id", ""), body.get("data", ""))
    if action == "finish":
        return await executor.write_stream_finish(body.get("session_id", ""))
    return {"success": False, "error": f"unknown action: {action}"}


# ── Endpoint: /http_get ────────────────────────────────────────────────


@app.post("/http_get")
async def http_get(request: Request):
    body = await request.json()
    url = body.get("url", "")
    tier_str = body.get("tier", "standard")
    try:
        tier = SandboxTier(tier_str)
    except ValueError:
        tier = SandboxTier.STANDARD
    return await executor.http_get(url, tier=tier)


# ── Endpoint: /mcp/spawn ─────────────────────────────────────────────


@app.post("/mcp/spawn")
async def mcp_spawn(request: Request):
    body = await request.json()
    return await executor.mcp_spawn(
        body.get("server_name", ""),
        transport=body.get("transport", "stdio"),
        args=body.get("args"),
        env=body.get("env"),
    )


# ── Endpoint: /mcp/call ──────────────────────────────────────────────


@app.post("/mcp/call")
async def mcp_call(request: Request):
    body = await request.json()
    return await executor.mcp_call(
        body.get("server_name", ""), body.get("tool_name", ""), body.get("arguments")
    )


# ── Endpoint: /fs/list ────────────────────────────────────────────────


@app.post("/fs/list")
async def fs_list(request: Request):
    body = await request.json()
    path = body.get("path", ".")
    recursive = body.get("recursive", False)
    ignore_hidden = body.get("ignore_hidden", True)
    abs_path = _validate_path_within_workspace(path)
    if abs_path is None:
        return JSONResponse(status_code=403, content={"error": "path_outside_workspace"})
    entries = []
    if recursive:
        for root, dirs, files in os.walk(abs_path):
            if ignore_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                files = [f for f in files if not f.startswith(".")]
            for name in files:
                full = os.path.join(root, name)
                try:
                    st = os.stat(full)
                    entries.append({"name": name, "path": os.path.relpath(full, abs_path), "is_dir": False, "size": st.st_size, "modified_epoch": st.st_mtime})
                except OSError:
                    pass
            for name in dirs:
                full = os.path.join(root, name)
                try:
                    st = os.stat(full)
                    entries.append({"name": name, "path": os.path.relpath(full, abs_path), "is_dir": True, "size": 0, "modified_epoch": st.st_mtime})
                except OSError:
                    pass
    else:
        try:
            for entry in os.scandir(abs_path):
                if ignore_hidden and entry.name.startswith("."):
                    continue
                try:
                    st = entry.stat()
                    entries.append({"name": entry.name, "path": entry.name, "is_dir": entry.is_dir(), "size": st.st_size if not entry.is_dir() else 0, "modified_epoch": st.st_mtime})
                except OSError:
                    pass
        except OSError:
            pass
    return {"entries": entries}


# ── Endpoint: /fs/glob ────────────────────────────────────────────────


@app.post("/fs/glob")
async def fs_glob(request: Request):
    body = await request.json()
    pattern = body.get("pattern", "*")
    path = body.get("path", ".")
    max_results = int(body.get("max_results", 200))
    abs_path = _validate_path_within_workspace(path)
    if abs_path is None:
        return JSONResponse(status_code=403, content={"error": "path_outside_workspace"})
    entries = []
    try:
        from pathlib import Path as _P
        for p in sorted(_P(abs_path).glob(pattern), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            if not p.exists():
                continue
            try:
                st = p.stat()
                entries.append({"path": str(p.relative_to(abs_path)), "size": st.st_size, "modified_epoch": st.st_mtime})
            except OSError:
                continue
            if len(entries) >= max_results:
                break
    except Exception:
        pass
    truncated = len(entries) >= max_results
    return {"entries": entries, "truncated": truncated}


# ── Endpoint: /fs/grep ────────────────────────────────────────────────


@app.post("/fs/grep")
async def fs_grep(request: Request):
    import re as _re
    body = await request.json()
    pattern = body.get("pattern", "")
    path = body.get("path", ".")
    include = body.get("include", "*")
    case_insensitive = body.get("case_insensitive", False)
    max_matches = int(body.get("max_matches", 200))
    abs_path = _validate_path_within_workspace(path)
    if abs_path is None:
        return JSONResponse(status_code=403, content={"error": "path_outside_workspace"})
    flags = _re.IGNORECASE if case_insensitive else 0
    matches = []
    try:
        from pathlib import Path as _P
        for p in _P(abs_path).rglob(include):
            if not p.is_file():
                continue
            try:
                raw = p.read_bytes()[:8192]
                if b"\x00" in raw:
                    continue
                with open(p, "r", errors="replace") as f:
                    for line_no, line in enumerate(f, 1):
                        if _re.search(pattern, line, flags):
                            matches.append({"path": str(p.relative_to(abs_path)), "line_number": line_no, "line": line.rstrip("\n")})
                            if len(matches) >= max_matches:
                                break
            except OSError:
                continue
            if len(matches) >= max_matches:
                break
    except Exception:
        pass
    truncated = len(matches) >= max_matches
    return {"matches": matches, "truncated": truncated}


# ── Endpoint: /http_post ───────────────────────────────────────────────


@app.post("/http_post")
async def http_post(request: Request):
    body = await request.json()
    url = body.get("url", "")
    req_body = body.get("body")
    headers = body.get("headers")
    content_type = body.get("content_type", "application/json")
    timeout_seconds = int(body.get("timeout_seconds", 15))
    if not ALLOW_NET:
        return {"success": False, "error": "network_access_disabled", "http_status_code": 0, "http_headers": {}}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            req_headers = headers or {}
            if content_type and "content-type" not in {k.lower() for k in req_headers}:
                req_headers["content-type"] = content_type
            resp = await client.post(url, json=req_body if isinstance(req_body, dict) else None, content=req_body if isinstance(req_body, str) else None, headers=req_headers)
            return {"success": 200 <= resp.status_code < 300, "content": resp.text, "error": None if 200 <= resp.status_code < 300 else resp.reason_phrase, "http_status_code": resp.status_code, "http_headers": dict(resp.headers)}
    except Exception as exc:
        return {"success": False, "content": None, "error": str(exc), "http_status_code": 0, "http_headers": {}}


# ── Endpoint: /archive ─────────────────────────────────────────────────


@app.post("/archive/create")
async def archive_create(request: Request):
    body = await request.json()
    name = body.get("name", "snapshot")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    tag = f"prismclaw/archive:{safe_name}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "commit", os.environ.get("PRISMCLAW_CONTAINER_ID", os.environ.get("HOSTNAME", "")), tag,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            return {"archive_id": tag, "size_mb": 0, "error": stderr.decode()[:500]}
        size_mb = 0
        try:
            inspect_proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", tag, "--format", "{{.Size}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(inspect_proc.communicate(), timeout=10)
            if inspect_proc.returncode == 0:
                size_mb = int(out.decode().strip()) // (1024 * 1024)
        except Exception:
            pass
        return {"archive_id": tag, "size_mb": size_mb}
    except Exception as exc:
        return {"archive_id": tag, "size_mb": 0, "error": str(exc)[:500]}


@app.post("/archive/restore")
async def archive_restore(request: Request):
    return {"restored": False, "error": "restore requires external orchestration"}


@app.get("/archive/list")
async def archive_list():
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "prismclaw/archive", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        archives = []
        for line in stdout.decode().strip().splitlines():
            parts = line.split("\t", 1)
            archives.append({"archive_id": parts[0], "size": parts[1] if len(parts) > 1 else ""})
        return {"archives": archives}
    except Exception as exc:
        return {"archives": [], "error": str(exc)[:500]}


# ── Endpoint: /health ──────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "workspace_id": WORKSPACE_ID}
