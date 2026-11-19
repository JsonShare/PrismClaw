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
# 专用 agent 模式：设置后只接受该 workspace_id 的请求。
# 留空 = 共享 agent（一个 agent 服务 WORKSPACE_ROOT 下的多个工作区），
# 此时作用域由每个请求的 workspace_root 在 RuntimeExecutor 内收窄，
# 且请求根必须是 agent 自身根的严格子路径。
WORKSPACE_ID = os.getenv("PRISMCLAW_WORKSPACE_ID", "")
WORKSPACE_ROOT = os.getenv("PRISMCLAW_WORKSPACE_ROOT", "/workspace")
ALLOW_NET = os.getenv("PRISMCLAW_ALLOW_NET", "true").lower() == "true"

# ── FastAPI app ────────────────────────────────────────────────────────

app = FastAPI(title="PrismClaw Runtime-Agent", version="1.0.0")

# 沙箱策略：从环境变量或 app.state 获取
_SANDBOX_ENFORCE = os.getenv("PRISMCLAW_SANDBOX_ENFORCE", "true").lower() == "true"


def _resolve_sandbox_policy() -> BwrapPolicy | None:
    """解析沙箱策略：enforce=True 时必须拿到 BwrapPolicy，否则拒绝启动。

    与 app 侧 ``resolve_sandbox_state`` 保持同一语义（fail-closed）。此前这里
    捕获异常后返回 None，同一个 ``PRISMCLAW_SANDBOX_ENFORCE=true`` 在 app 侧
    是"起不来"、在 agent 侧是"静默无隔离"，两边含义相反。
    """
    if not _SANDBOX_ENFORCE:
        return None
    from prismclaw.engine.security.sandbox.state import resolve_sandbox_state
    from prismclaw.schema.config.config import SandboxConfig

    state = resolve_sandbox_state(SandboxConfig(enforce=True))
    if not isinstance(state.policy, BwrapPolicy):
        raise RuntimeError(
            f"PRISMCLAW_SANDBOX_ENFORCE=true 但未解析出 bwrap 策略（backend={state.backend}）。"
            "请确认镜像内已安装 bubblewrap，且容器具备运行它所需的权限。"
        )
    return state.policy


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

    # 专用 agent 才校验 workspace_id；共享 agent 靠 workspace_root 路径边界隔离。
    if WORKSPACE_ID and ws_id != WORKSPACE_ID:
        logger.warning("Workspace ID mismatch: got %s, expected %s", ws_id, WORKSPACE_ID)
        return JSONResponse(status_code=403, content={"error": "workspace_id_mismatch"})

    return await call_next(request)


# ── Endpoint: /bash ────────────────────────────────────────────────────


@app.post("/bash")
async def exec_bash(request: Request):
    body = await request.json()
    tier_str = body.get("tier", "standard")
    try:
        tier = SandboxTier(tier_str)
    except ValueError:
        tier = SandboxTier.STANDARD
    hard_timeout = body.get("hard_timeout")
    return await executor.bash(
        body.get("command", ""),
        tier=tier,
        soft_timeout=int(body.get("soft_timeout", body.get("timeout_seconds", 30))),
        hard_timeout=int(hard_timeout) if hard_timeout is not None else None,
        workspace_root=body.get("workspace_root"),
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
    root = body.get("workspace_root")
    if body.get("mode") == "bytes":
        return await executor.read_bytes(path, workspace_root=root)
    return await executor.read_text(
        path, encoding=body.get("encoding", "utf-8"), workspace_root=root
    )


# ── Endpoint: /write ───────────────────────────────────────────────────


@app.post("/write")
async def write_file(request: Request):
    body = await request.json()
    path = body.get("path", "")
    root = body.get("workspace_root")
    if body.get("mode") == "bytes":
        return await executor.write_bytes(
            path, base64.b64decode(body.get("content_b64", "")), workspace_root=root
        )
    return await executor.write_text(
        path,
        body.get("content", ""),
        encoding=body.get("encoding", "utf-8"),
        workspace_root=root,
    )


# ── Endpoint: /write_stream ────────────────────────────────────────────


@app.post("/write_stream")
async def write_stream(request: Request):
    body = await request.json()
    action = body.get("action", "")
    if action == "begin":
        return await executor.write_stream_begin(
            body.get("path", ""),
            body.get("mode", "text"),
            body.get("encoding", "utf-8"),
            workspace_root=body.get("workspace_root"),
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
    return await executor.fs_list(
        body.get("path", "."),
        recursive=body.get("recursive", False),
        ignore_hidden=body.get("ignore_hidden", True),
        workspace_root=body.get("workspace_root"),
    )


# ── Endpoint: /fs/glob ────────────────────────────────────────────────


@app.post("/fs/glob")
async def fs_glob(request: Request):
    body = await request.json()
    return await executor.fs_glob(
        body.get("pattern", "*"),
        body.get("path", "."),
        max_results=int(body.get("max_results", 200)),
        workspace_root=body.get("workspace_root"),
    )


# ── Endpoint: /fs/grep ────────────────────────────────────────────────


@app.post("/fs/grep")
async def fs_grep(request: Request):
    body = await request.json()
    return await executor.fs_grep(
        body.get("pattern", ""),
        body.get("path", "."),
        include=body.get("include", "*"),
        case_insensitive=body.get("case_insensitive", False),
        max_matches=int(body.get("max_matches", 200)),
        workspace_root=body.get("workspace_root"),
    )


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
