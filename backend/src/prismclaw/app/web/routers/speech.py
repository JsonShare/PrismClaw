"""Speech-to-text (ASR) streaming routes for voice input in the chat box.

只保留 WebSocket 流式接口：前端浏览器以 binary PCM16LE/16kHz/mono 持续送入，
本路由做协议转换后转发给后端 ASR 服务（JSON + base64 audio_chunk 协议）。

整段转写（HTTP /v1/audio/transcriptions 兼容）路径已移除。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from prismclaw.app.auth.auth import verify_jwt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

speech_router = APIRouter(prefix="/speech", tags=["speech"])


def _get_speech_settings(request: Request) -> Any:
    """从 app.state 或全局 settings 获取语音配置。"""
    web_deps = getattr(request.app.state, "web_deps", None)
    settings_full = getattr(web_deps, "settings", None) if web_deps is not None else None
    if settings_full is not None:
        return getattr(settings_full, "speech", None)
    # 回退：直接从全局 settings 读取
    try:
        from prismclaw.infra.settings import load_settings

        return load_settings().speech
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Streaming ASR (WebSocket) — 协议转换代理
# ---------------------------------------------------------------------------
#
# 后端 ASR 服务（FunASR/SenseVoice 风格）的 WS 协议：
#
#   Client                              Server
#     |-- WS connect /ws/asr ---------->|  [accept]
#     |-- {"type":"start","language":"Chinese"} -->|
#     |                                  |  (silent accept)
#     |-- {"type":"audio_start"} ------>|
#     |                                  |-- {"type":"audio_start_ack","code":200,...}
#     |-- {"type":"audio_chunk","data":"<b64 PCM16LE>"} (repeat) -->|
#     |                                  |-- {"type":"asr_partial","data":{"text":"..."}}
#     |                                  |-- {"type":"asr_final","data":{"text":"..."}}
#     |-- {"type":"audio_end"} --------->|  (optional, forces flush → asr_final)
#
# 服务端 **不接受 binary WS frame**，所有音频必须 JSON + base64 包装。
#
# 浏览器侧仍然发 binary PCM16LE/16kHz/mono，由本路由做协议翻译，
# 保持前端 simple（AudioWorklet → binary → 后端转 base64）。
#
# 本路由到浏览器的消息类型：
#   {"type":"ready"}    —— ASR 就绪，可以开始收音
#   {"type":"partial","text":"..."}  —— 增量结果
#   {"type":"final","text":"..."}    —— 最终结果
#   {"type":"error",...}  —— 出错
#
# 关闭码：4001 identify 超时 / 4002 首帧非 identify / 4003 token 无效 /
#        4100 流式 ASR 未配置 / 4101 上游连接失败 / 4102 上游握手失败。

_WS_IDENTIFY_TIMEOUT_SECONDS = 30
_UPSTREAM_HANDSHAKE_TIMEOUT_SECONDS = 15


def _get_web_settings(websocket: WebSocket) -> Any:
    web_deps = getattr(websocket.app.state, "web_deps", None)
    return getattr(web_deps, "web_settings", None)


async def _safe_close(websocket: WebSocket, code: int, reason: str) -> None:
    """客户端可能已断开，close 失败不应再抛异常。"""
    try:
        await websocket.close(code, reason)
    except (RuntimeError, WebSocketDisconnect):
        pass


async def _upstream_recv_json(upstream: Any) -> dict | None:
    """从 upstream 读一条文本消息并解析为 dict；返回 None 表示连接已关。"""
    try:
        msg = await upstream.recv()
    except websockets.exceptions.ConnectionClosed:
        return None
    except Exception as exc:
        logger.warning("speech upstream recv error: %s", exc)
        return None
    if isinstance(msg, (bytes, bytearray)):
        # upstream 偶尔发 binary frame（通常是 close code），忽略
        return None
    if not msg:
        return None
    try:
        return json.loads(msg)
    except json.JSONDecodeError:
        logger.debug("speech upstream non-JSON text: %r", msg[:200])
        return None


async def _upstream_handshake(upstream: Any, language: str) -> None:
    """
    执行 ASR 协议握手：start → audio_start → 等待 audio_start_ack。

    成功返回即代表可以开始送音频；失败抛异常由调用方处理。
    """
    await upstream.send(json.dumps({"type": "start", "language": language}))
    await upstream.send(json.dumps({"type": "audio_start"}))

    deadline = asyncio.get_running_loop().time() + _UPSTREAM_HANDSHAKE_TIMEOUT_SECONDS
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("upstream handshake timeout: no audio_start_ack")
        msg = await asyncio.wait_for(upstream.recv(), timeout=remaining)
        if isinstance(msg, (bytes, bytearray)):
            raise RuntimeError("upstream sent binary frame during handshake")
        if not msg:
            continue
        try:
            parsed = json.loads(msg)
        except json.JSONDecodeError:
            continue
        if parsed.get("type") == "audio_start_ack":
            # 服务端可能带 code != 200 的 ack，也算握手失败
            code = parsed.get("code")
            if isinstance(code, int) and code != 200:
                raise RuntimeError(f"upstream audio_start_ack code={code}")
            return
        # 其他消息类型忽略（可能是延迟的旧 final 等）


@speech_router.websocket("/stream")
async def stream_transcribe(websocket: WebSocket) -> None:
    """实时语音转写：鉴权后把浏览器 binary PCM 转成上游 ASR 的 JSON+base64 协议。"""
    await websocket.accept()

    # ---- 握手：首帧必须 identify（与 /api/ws 协议一致）----
    try:
        raw = await asyncio.wait_for(
            websocket.receive_json(), timeout=_WS_IDENTIFY_TIMEOUT_SECONDS
        )
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, TypeError, RuntimeError):
        await _safe_close(websocket, 4001, "Identify timeout")
        return

    if not isinstance(raw, dict) or raw.get("type") != "identify":
        await _safe_close(websocket, 4002, "First message must be identify")
        return

    web_settings = _get_web_settings(websocket)
    jwt_secret = getattr(web_settings, "jwt_secret", None) if web_settings else None
    user_id = verify_jwt(str(raw.get("token") or ""), jwt_secret) if jwt_secret else None
    if user_id is None:
        await _safe_close(websocket, 4003, "Invalid token")
        return

    # ---- 读取 ASR 服务配置 ----
    speech_cfg = _get_speech_settings(websocket)
    stream_url = (getattr(speech_cfg, "stream_url", "") or "").strip() if speech_cfg else ""
    if not stream_url:
        try:
            await websocket.send_json(
                {"type": "error", "code": "not_configured", "message": "流式语音识别未配置"}
            )
        except RuntimeError:
            pass
        await _safe_close(websocket, 4100, "Stream ASR not configured")
        return

    language = (getattr(speech_cfg, "language", "Chinese") or "Chinese") if speech_cfg else "Chinese"
    connect_timeout = int(getattr(speech_cfg, "stream_connect_timeout", 10) or 10)

    # ---- 连接 ASR 服务 ----
    try:
        upstream = await asyncio.wait_for(
            websockets.connect(stream_url, max_size=8 * 1024 * 1024, ping_interval=20),
            timeout=connect_timeout,
        )
    except Exception as exc:
        logger.warning("speech upstream connect failed: %s", exc)
        try:
            await websocket.send_json(
                {"type": "error", "code": "upstream_unavailable", "message": f"流式识别服务不可达: {exc}"}
            )
        except RuntimeError:
            pass
        await _safe_close(websocket, 4101, "Upstream unavailable")
        return

    try:
        # ---- 协议握手 ----
        try:
            await _upstream_handshake(upstream, language)
        except (TimeoutError, RuntimeError) as exc:
            logger.warning("speech upstream handshake failed: %s", exc)
            try:
                await websocket.send_json(
                    {"type": "error", "code": "upstream_handshake_failed", "message": str(exc)}
                )
            except RuntimeError:
                pass
            await _safe_close(websocket, 4102, "Upstream handshake failed")
            return

        # 握手成功 → 通知浏览器可以开始收音
        try:
            await websocket.send_json({"type": "ready"})
        except RuntimeError:
            # 浏览器在 ready 前就断了，没关系
            return

        # ---- 双向 relay（带协议转换） ----
        await _relay_with_protocol_translate(websocket, upstream, user_id)
    finally:
        try:
            await upstream.close()
        except Exception:
            pass


async def _relay_with_protocol_translate(
    websocket: WebSocket, upstream: Any, user_id: str
) -> None:
    """
    在浏览器（binary PCM16LE）和 ASR 服务（JSON+base64 audio_chunk）之间做翻译。

    浏览器 → 后端：
      binary frame  ──▶ base64 包装成 {"type":"audio_chunk","data":"..."} 发给 upstream
      {"type":"finish"} ──▶ {"type":"audio_end"} 发给 upstream，然后等待 asr_final

    后端 → 浏览器：
      asr_partial ──▶ {"type":"partial", "text": "..."}
      asr_final   ──▶ {"type":"final",   "text": "..."}
      其他        ──▶ 原样透传（debug）
    """

    # 用于协调「finish → audio_end → 等 asr_final」的状态
    finishing_event = asyncio.Event()
    finish_sent_event = asyncio.Event()
    final_received_event = asyncio.Event()

    async def browser_to_upstream() -> None:
        """把浏览器 binary chunk 转成 base64 JSON 发给 upstream。"""
        try:
            while True:
                message = await websocket.receive()
                msg_type = message.get("type")

                if msg_type == "websocket.disconnect":
                    return

                # binary frame → audio_chunk
                chunk = message.get("bytes")
                if chunk is not None:
                    if finishing_event.is_set():
                        # 已 finish 的尾音，仍然发出去保证完整
                        pass
                    b64 = base64.b64encode(bytes(chunk)).decode("ascii")
                    await upstream.send(json.dumps({"type": "audio_chunk", "data": b64}))
                    continue

                # text frame —— 解析后处理控制消息
                text = message.get("text")
                if text:
                    try:
                        ctrl = json.loads(text)
                    except json.JSONDecodeError:
                        # 非 JSON 的 text 控制消息也透传
                        await upstream.send(text)
                        continue

                    if isinstance(ctrl, dict) and ctrl.get("type") == "finish":
                        finishing_event.set()
                        # 通知 upstream_to_browser 不要在收到 final 之前退出
                        await upstream.send(json.dumps({"type": "audio_end"}))
                        finish_sent_event.set()
                        # 发完 audio_end 就退出本方向，等待 upstream 回 final
                        return

                    # 其他控制消息原样透传
                    await upstream.send(text)
        except WebSocketDisconnect:
            logger.debug("speech browser disconnected user=%s", user_id)
        except Exception as exc:
            logger.warning("speech browser_to_upstream error user=%s: %s", user_id, exc)

    async def upstream_to_browser() -> None:
        """把 upstream ASR 消息翻译成浏览器侧的 partial/final 协议。"""
        try:
            while True:
                parsed = await _upstream_recv_json(upstream)
                if parsed is None:
                    # upstream 断开了
                    break

                mtype = parsed.get("type")

                if mtype == "audio_start_ack":
                    # 握手阶段已经处理过，不再转发
                    continue

                if mtype == "asr_partial":
                    data = parsed.get("data") or {}
                    text = data.get("text") if isinstance(data, dict) else None
                    if text is None:
                        continue
                    try:
                        await websocket.send_json({"type": "partial", "text": str(text)})
                    except RuntimeError:
                        break
                    continue

                if mtype == "asr_final":
                    data = parsed.get("data") or {}
                    text = data.get("text") if isinstance(data, dict) else None
                    if text is None:
                        text = ""
                    try:
                        await websocket.send_json({"type": "final", "text": str(text)})
                    except RuntimeError:
                        pass
                    final_received_event.set()
                    break  # final 到了就退出本方向

                # 其他未知类型：原样透传（方便前端 debug）
                raw = json.dumps(parsed, ensure_ascii=False)
                try:
                    await websocket.send_text(raw)
                except RuntimeError:
                    break
        except Exception as exc:
            logger.warning("speech upstream_to_browser error user=%s: %s", user_id, exc)

    # 两个方向并行跑
    task_b2u = asyncio.create_task(browser_to_upstream())
    task_u2b = asyncio.create_task(upstream_to_browser())

    done, pending = await asyncio.wait(
        [task_b2u, task_u2b], return_when=asyncio.FIRST_COMPLETED
    )

    # 如果浏览器先断/先 finish，upstream 方向还在等 asr_final，
    # 给它一个短暂窗口让 final 自然回来。
    if task_u2b in pending:
        if finish_sent_event.is_set() and not final_received_event.is_set():
            try:
                await asyncio.wait_for(final_received_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.debug("speech asr_final not arrived within 3s after audio_end")

    # 收尾
    for t in [task_b2u, task_u2b]:
        if not t.done():
            t.cancel()
    await asyncio.gather(task_b2u, task_u2b, return_exceptions=True)

    # 记录任一 task 的异常（非正常结束时）
    for t in done:
        exc = t.exception()
        if exc is not None and not isinstance(exc, WebSocketDisconnect):
            logger.debug("speech relay task user=%s ended with: %s", user_id, exc)
