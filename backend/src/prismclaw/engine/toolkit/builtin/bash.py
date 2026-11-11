from __future__ import annotations

from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.engine.toolkit.builtin.file_helpers import (
    BASH_DEFAULT_TIMEOUT_SECONDS,
    _ws_root,
    _ws_kind,
    _require_runtime,
)
from prismclaw.schema.services.env_types import CommandResult
from prismclaw.schema.content.workspace import SandboxTier


# ─── BashTool ─────────────────────────────────────────────────────────

class BashTool:
    name = "bash"
    description = "在工作区中执行 shell 命令，返回 stdout 与 stderr。注意：这是持久会话，环境变量与后台进程会跨调用保留。"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "description": {
                "type": "string",
                "description": "简要说明该命令的作用（供人工审阅）",
            },
            "blocking_budget_ms": {
                "type": "number",
                "description": "同步等待的时长（最大 15000ms）。超过后任务会转入后台。",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "后台任务的硬超时，单位秒（默认 120）",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    side_effect = True
    tool_class = "write"
    effect_class = "unknown_effect"
    is_concurrency_safe = False
    timeout_seconds = 120.0

    # Global state to maintain persistent PTY sessions per workspace
    # Format: {workspace_id: {"process": Popen, "queue": Queue}}
    _sessions: dict[str, Any] = {}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        call_id = args.get("_call_id", "")
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return error_result(call_id, "bash：缺少必填参数 'command'")

        if context.abort.is_set():
            return error_result(call_id, "bash：启动前已中止")

        # Blocking Budget implementation
        # Maximum time LLM reasoning loop will block waiting for this command.
        blocking_budget_ms = float(args.get("blocking_budget_ms", 15000))
        blocking_budget_s = min(blocking_budget_ms / 1000.0, 15.0) # Hard cap at 15s
        
        # Total time before process is actually killed
        hard_timeout = float(args.get("timeout_seconds") or BASH_DEFAULT_TIMEOUT_SECONDS)

        # REPL Implementation (Simulated for this refactor)
        # To truly support a persistent PTY we would use `pexpect` or a custom daemon.
        # Since we are evolving the architecture, we'll wrap the execution in a way
        # that allows for state retention if implemented, but falls back to stateful wrappers.
        
        # Inject workspace-scoped dirs dynamically via shell, so they follow $PWD
        # regardless of which directory the task is working in.
        # 交付物目录：<workspace>/sessions/<sid>/outputs/。
        # 引擎层通过 resolve_within(session_root=...) 自动把无前缀路径落到 session outputs。
        # 技能/脚本可以按 $PRISMCLAW_OUTPUT_DIR 环境变量或直接相对路径写产物。
        #   全局共享 PRISMCLAW_RUNTIME_ROOT    ← ~/.prismclaw/runtime（所有 workspace 共用，
        #                                          node_modules/pip --target 等重资产装这里）
        #   workspace-local PRISMCLAW_SKILL_CACHE ← $PRISMCLAW_WORKSPACE/.work/cache/skills/<skill>
        #                                          （docx-p...-20261020-xxx 项目目录落这里）
        # 每种 runtime 发现机制各有不同，引擎层统一注入 PATH 扩展：
        #   Python: pip install --target <dir>/site-packages
        #           → PYTHONPATH += <dir>/site-packages（进程自动 import 发现）
        #   Node:   npm install --prefix <dir>  → node_modules/ 自动创建
        #           → NODE_PATH += <dir>/node_modules（require() 自动发现）
        # 两种环境互不干扰（site-packages ≠ node_modules），可共存同一 <dir>。
        import re as _re
        from prismclaw.runtime.runtime_root import resolve_runtime_root as _resolve_runtime_root
        from prismclaw.engine.toolkit.builtin.file_helpers import session_root as _session_root
        _raw_skill = getattr(context, "active_skill_name", None) or "_default"
        _skill_safe = _re.sub(r"[^\w.\-]", "_", _raw_skill)[:64] or "_default"
        _global_runtime = _resolve_runtime_root()
        _skill_cache = f'"$PRISMCLAW_WORKSPACE/.work/cache/skills/{_skill_safe}"'
        _skill_runtime = f'"{_global_runtime}/skills/{_skill_safe}"'
        _py_site = f'"{_global_runtime}/skills/{_skill_safe}/site-packages"'
        _node_modules = f'"{_global_runtime}/skills/{_skill_safe}/node_modules"'
        # session 级路径 — 从 ToolContext 算，降级时用 workspace 根
        _sr = _session_root(context)
        if _sr is not None:
            # bash cd 到 session 根后，$PWD 即 session 根，
            # 所以 env var 直接用 $PWD/<subdir> 即可。
            _output_dir = '"$PWD/outputs"'
            _uploads_dir = '"$PWD/uploads"'
            _cb_dir = '"$PWD/context_blocks"'
            _tr_dir = '"$PWD/tool_results"'
            _mkdir_dirs = (
                f'"$PRISMCLAW_WORKSPACE/.work/tmp" '
                f'"$PRISMCLAW_WORKSPACE/sessions/{_sr.name}/uploads" '
                f'"$PRISMCLAW_WORKSPACE/sessions/{_sr.name}/outputs" '
                f'"$PRISMCLAW_WORKSPACE/sessions/{_sr.name}/context_blocks" '
                f'"$PRISMCLAW_WORKSPACE/sessions/{_sr.name}/tool_results" '
                f'"$PRISMCLAW_WORKSPACE/sessions/{_sr.name}/.work/tmp" '
                f'{_skill_cache} {_skill_runtime} {_py_site} {_node_modules}'
            )
        else:
            # 降级（无 session_id 的罕见路径）：没有 session 级隔离概念，
            # 所有引擎产出统一落到 workspace 系统区 `.work/tmp`——
            # 避免 env 指向 workspace 根裸 outputs/uploads 污染用户项目目录。
            _safe = '"$PWD/.work/tmp"'
            _output_dir = _safe
            _uploads_dir = _safe
            _cb_dir = _safe
            _tr_dir = _safe
            _mkdir_dirs = (
                f'"$PWD/.work/tmp" "$PWD/.work/bash_state" '
                f'{_skill_cache} {_skill_runtime} {_py_site} {_node_modules}'
            )
        # bash cwd 策略：workspace 根 → session 根。
        # 模型写 `./outputs/xxx.md` 这类相对路径时自然落到 session 级，
        # 不再绕过 resolve_within 直接 mkdir workspace 根旧目录。
        # workspace 根资源通过 $PRISMCLAW_WORKSPACE 绝对路径访问。
        if _sr is not None:
            # sessions/<sid> 可能尚未由上层创建（首次 bash 调用早于任何文件工具），
            # 先 mkdir 再 cd，避免整条 && 链在 cd 处中断。
            _cd_cmd = (
                f'mkdir -p "$PWD/sessions/{_sr.name}" '
                f'&& cd "$PWD/sessions/{_sr.name}" '
                f'&& mkdir -p .work/tmp .work/bash_state && '
            )
        else:
            _cd_cmd = ''
        _env_prefix = (
            f'export PRISMCLAW_WORKSPACE="$PWD" && '
            f'{_cd_cmd}'
            f'mkdir -p {_mkdir_dirs} && '
            f'export PRISMCLAW_TMP_DIR="$PWD/.work/tmp" && '
            f'export PRISMCLAW_OUTPUT_DIR={_output_dir} && '
            f'export PRISMCLAW_UPLOADS_DIR={_uploads_dir} && '
            f'export PRISMCLAW_CONTEXT_BLOCKS_DIR={_cb_dir} && '
            f'export PRISMCLAW_TOOL_RESULTS_DIR={_tr_dir} && '
            f'export PRISMCLAW_SKILL_CACHE={_skill_cache} && '
            f'export PRISMCLAW_RUNTIME_ROOT="{_global_runtime}" && '
            f'export PRISMCLAW_SKILL_RUNTIME={_skill_runtime} && '
            # ── 运行时发现机制 ──
            # Python: 让 python -c / script.py 能直接 import 全局 site-packages
            f'export PYTHONPATH={_py_site}${{PYTHONPATH:+:$PYTHONPATH}} && '
            # Node:   让 require() 能找到全局 node_modules
            f'export NODE_PATH={_node_modules}${{NODE_PATH:+:$NODE_PATH}} && '
        )
        # 单行 && 链：避免换行拆分导致第二行在 cd 失败的错误 cwd 上执行，
        # 也避免 call_id 为空/含空格时把 `.work/bash_state/` 当裸重定向目标。
        # bash_state 目录写入是 best-effort（用于持久会话 cwd 恢复的未来钩子），
        # 失败不影响用户命令退出码。
        wrapped_command = (
            f"{_env_prefix}{command} && "
            f'mkdir -p .work/bash_state 2>/dev/null; '
            f'printf "%s\n" "$PWD" >> .work/bash_state/_last_pwd 2>/dev/null || true'
        )
        # TODO GC 策略：全局 runtime 目录 ~/.prismclaw/runtime/ 会无限增长
        # （旧版本 node_modules、卸载技能残留），目前无清理机制。建议：
        #   1. 技能注册时登记安装的依赖清单（pkg-lock 或 node_modules/ 下的目录列表）
        #   2. 技能卸载时删除 runtime/skills/<skill>/ 整个目录
        #   3. 定期 TTL 清理（最近 30 天没被访问的 runtime 子目录）

        # 统一执行路径（无降级）：始终走 context.runtime，复用同一份
        # RuntimeExecutor 执行核心（生产=HTTP 容器 / 开发测试=进程内传输）。
        # 命令以 workspace_root 为 cwd 执行（由 RuntimeExecutor 保证），因此
        # 生产与本机的执行语义逐字节一致，不存在分叉的本地 subprocess 分支。
        runtime = _require_runtime(context)
        _tier = getattr(context, "current_sandbox_tier", SandboxTier.STANDARD)
        _ws = _ws_kind(context)
        _root = _ws_root(context)
        try:
            result = await runtime.bash(
                wrapped_command,
                tier=_tier,
                soft_timeout=int(blocking_budget_s),
                hard_timeout=int(hard_timeout),
                template_id=_ws,
                workspace_id=context.workspace_id,
                workspace_root=_root,
            )
        except Exception as exc:
            return error_result(call_id, f"bash：运行时执行失败：{exc}")

        # 软超时 detach：进程不杀，由 runtime 注册硬超时杀手，返回后台任务。
        if isinstance(result, dict) and result.get("detached"):
            pid = result.get("pid") or result.get("job_id")
            return text_result(
                call_id,
                f"[进程已转入后台运行]\n命令 `{command[:50]}...` "
                f"执行时间超过 {blocking_budget_s}s，已移至后台继续运行。\n"
                f"PID: {pid}\n如需停止，请使用 `cancel_task`。",
            )

        # 同步完成：直接格式化输出（output_bound 处理截断）。
        assert isinstance(result, CommandResult)
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.exit_code
        parts = []
        if stdout:
            parts.append(f"[stdout]\n{stdout}")
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        parts.append(f"[exit_code={exit_code}]")
        body = "\n".join(parts)
        if exit_code != 0:
            return error_result(call_id, body)
        return text_result(call_id, body)
