"""Web command adapter — intercepts slash-command messages and routes them
through the plugin command runtime before injecting into the chat session.

This module bridges the gap between the WebSocket delivery layer and the
engine-level PluginCommandRuntime, without requiring CommandContext or
dispatch_user_message callbacks.

Flow:
  1. _on_chat_send detects `/command_name args` pattern
  2. try_handle_slash_command() resolves the command via ExtensionManager
  3. Builds the invocation prompt via PluginCommandRuntime.build_invocation_prompt()
  4. Replaces the user message content with the invocation prompt
  5. Injects pending_command_identity into tool_context_extras
  6. Returns the modified ChatSendMessage for normal _run_chat processing
  7. Runner initialization reads pending_command_identity and projects it
     via project_active_skill_invocation()
"""
from __future__ import annotations

import logging
import re

from prismclaw.app.web.connection import ConnectionState
from prismclaw.app.web.protocol.messages import ChatSendMessage
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.ids import build_web_session_key
from prismclaw.schema.session.types import generate_entry_id

logger = logging.getLogger(__name__)

_event_logger = SessionEventLogger()

# Sentinel returned by try_handle_slash_command when a built-in command has
# fully handled the message locally (reply already delivered over the WS).
# Distinct from None (= "not a command, fall through to normal chat") so the
# websocket dispatch loop can swallow the original message.
COMMAND_HANDLED = object()

# Matches /command_name followed by optional arguments
_SLASH_COMMAND_RE = re.compile(r"^/([a-zA-Z0-9_:-]+)(?:\s+(.*))?$")


def _split_command(text: str) -> tuple[str, str] | None:
    """Split a slash command into (name, args). Returns None if not a command."""
    text = text.strip()
    m = _SLASH_COMMAND_RE.match(text)
    if m is None:
        return None
    return m.group(1), (m.group(2) or "").strip()


async def try_handle_slash_command(
    state: ConnectionState,
    msg: ChatSendMessage,
) -> ChatSendMessage | None:
    """Attempt to intercept a slash-command message.

    If the message matches a registered plugin command, builds the invocation
    prompt and returns a *modified* ChatSendMessage with:
      - content replaced by the invocation prompt
      - pending_command_identity stored in msg._command_identity (picked up by _run_chat)

    Returns None if the message is not a slash command or no matching command found.
    The caller should then process the original message as a normal chat message.
    """
    split = _split_command(msg.content)
    if split is None:
        return None

    command_name, args = split

    # Built-in /mcp command (list / restart <server>) — the runtime's
    # MCPServerDeadError messages direct users here, so it must actually work.
    # Handled locally: the reply is a system receipt sent straight back over
    # the websocket (never routed through the LLM), and the original message
    # is swallowed by the dispatch loop via the COMMAND_HANDLED sentinel.
    if command_name == "mcp":
        await _handle_builtin_mcp_command(state, msg, args)
        return COMMAND_HANDLED

    # Resolve command via ExtensionManager
    web_deps = state.ws.app.state.web_deps
    if web_deps is None:
        return None

    runner_deps = web_deps.runner_deps
    if runner_deps is None:
        return None

    extension_manager = getattr(runner_deps, "extension_manager", None)
    if extension_manager is None:
        return None

    runtime = extension_manager.get_plugin_command_runtime(command_name)
    if runtime is None:
        # Not a recognized plugin command — let it fall through to normal chat
        # (it might be a skill gesture like /pdf handled by _try_auto_activate_skill)
        return None

    # Build invocation prompt via PluginCommandRuntime
    invocation_prompt = runtime.build_invocation_prompt(args)

    # Store command identity for runner to project
    command_identity = {
        "skill_name": runtime.name,
        "plugin_name": runtime.plugin_name,
    }

    # Create modified message with invocation prompt as content
    modified_msg = ChatSendMessage(
        conversation_id=msg.conversation_id,
        workspace_id=msg.workspace_id,
        content=invocation_prompt,
        attachments=msg.attachments,
        confirmation_decision=msg.confirmation_decision,
        mode=msg.mode,
        trajectory=msg.trajectory,
        # 命令改写只替换执行内容；客户端气泡身份必须保留，否则 plugin 命令
        # 这一轮的 user 消息无法 re-key，刷新前后会出现两个提问气泡。
        client_message_id=msg.client_message_id,
    )
    # Attach command identity as private attribute for _run_chat to pick up
    modified_msg._command_identity = command_identity  # type: ignore[attr-defined]

    logger.debug(
        "command_adapter: intercepted /%s (plugin=%s), replacing user message",
        command_name,
        runtime.plugin_name,
    )
    return modified_msg


async def _persist_command_turn(
    state: ConnectionState,
    msg: ChatSendMessage,
    reply: str,
) -> str | None:
    """把内置命令的一问一答落树，返回 assistant entry id。

    ``chat.done`` 不再携带正文（Web 端只把它当"去服务端对账"的信号），命令
    回复若不落库，客户端 sync 到 idle 后这条回复就会消失。落库后 sync 的
    展示投影自然包含它，刷新后也不丢。返回 None 表示无法落库（调用方仍发
    done，至少不影响会话状态收敛）。
    """
    web_deps = getattr(state.ws.app.state, "web_deps", None)
    runner_deps = getattr(web_deps, "runner_deps", None) if web_deps is not None else None
    store = getattr(runner_deps, "session_store", None)
    if store is None:
        return None
    from prismclaw.app.web.workspace_ids import clean_workspace_id

    try:
        session_key = build_web_session_key(
            state.user_id, clean_workspace_id(getattr(msg, "workspace_id", None))
        )
    except Exception:
        logger.warning("command.persist.invalid_workspace", exc_info=True)
        return None
    # 与 _run_chat 的会话 id 归一化保持一致：带 web: 前缀的是完整树 id。
    session_id = (
        msg.conversation_id
        if msg.conversation_id.startswith("web:")
        else f"{session_key}:{msg.conversation_id}"
    )
    try:
        tree = await store.load(session_id)
    except Exception:
        logger.warning("command.persist.load_failed session=%s", session_id, exc_info=True)
        return None
    if tree is None:
        logger.warning("command.persist.session_missing session=%s", session_id)
        return None
    user_entry_id = generate_entry_id(tree.all_entry_ids())
    reply_entry_id = generate_entry_id(tree.all_entry_ids() | {user_entry_id})
    try:
        await store.commit_events([
            _event_logger.user_message(session_id, msg.content, user_entry_id),
            _event_logger.assistant_message(session_id, reply, reply_entry_id),
        ])
    except Exception:
        logger.warning("command.persist.commit_failed session=%s", session_id, exc_info=True)
        return None
    return reply_entry_id


async def _handle_builtin_mcp_command(
    state: ConnectionState,
    msg: ChatSendMessage,
    args: str,
) -> None:
    """Handle the built-in ``/mcp`` command (``/mcp list`` | ``/mcp restart <server>``).

    Sends the reply directly to the websocket as an assistant-visible event;
    the original message is swallowed (never enqueued to the LLM).
    """
    web_deps = state.ws.app.state.web_deps
    mcp_manager = getattr(web_deps, "mcp_manager", None) if web_deps is not None else None
    if mcp_manager is None:
        reply = "[mcp] MCP manager is not available in this deployment."
    else:
        parts = args.split()
        sub = parts[0] if parts else "list"
        if sub == "list":
            servers = mcp_manager.list_servers()
            lines = []
            for s in servers:
                lines.append(f"- {s.name}: {s.status}" + (f" ({s.reason})" if getattr(s, "reason", None) else ""))
            reply = "[mcp] Server status:\n" + "\n".join(lines) if lines else "[mcp] No MCP servers configured."
        elif sub == "restart" and len(parts) >= 2:
            server_name = parts[1]
            try:
                result = await mcp_manager.restart_server(server_name)
                if getattr(result, "ok", False):
                    reply = f"[mcp] Server '{server_name}' restarted ({getattr(result, 'tool_count', 0)} tools)."
                else:
                    reply = f"[mcp] Restart failed for '{server_name}': {getattr(result, 'reason', 'unknown')}"
            except Exception as exc:
                reply = f"[mcp] Restart failed for '{server_name}': {exc}"
        else:
            reply = "[mcp] Usage: /mcp list | /mcp restart <server>"

    # 先落库再发 done：done 后客户端立即 sync，正文与 message_id 都必须已就绪。
    message_id = await _persist_command_turn(state, msg, reply)

    try:
        from prismclaw.app.web.connection import send_event
        from prismclaw.app.web.protocol.protocol import SERVER_CHAT_DONE

        await send_event(
            state,
            SERVER_CHAT_DONE,
            msg.conversation_id,
            {
                # 非 Web 通道仍消费 final_message；Web 端正文来自 sync 投影。
                "final_message": reply,
                "message_id": message_id,
                "usage": None,
                "aborted": False,
            },
        )
    except Exception:
        logger.warning("mcp-command reply delivery failed", exc_info=True)
