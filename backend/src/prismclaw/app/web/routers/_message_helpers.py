"""Message display helpers extracted from routes.py.

Sanitization, content extraction, and display-message construction
utilities used by the session message endpoints.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session import SessionTree
from prismclaw.schema.shared.path_sanitizer import redact_server_paths
from prismclaw.schema.shared.reference_block import strip_reference_block
from prismclaw.schema.shared.tool_markup import strip_tool_markup

# OOXML 段落锚点前缀：document_reader 在每段前加 ``[w14:paraId] `` 供 docx
# 编辑工具按段落定位。这是内部编辑句柄，对用户无意义——display 渲染（含刷新重放）
# 时剥离，避免气泡里冒出 ``[4B0CD5D4]`` 这类十六进制 ID 污染正文。
_PARA_ID_PREFIX_RE = re.compile(r"^\[[0-9A-Fa-f]{8}\]\s*")


def _iso_to_ms(ts: str | None) -> int | None:
    """Parse an ISO-8601 timestamp into epoch milliseconds (UTC).

    Returns None on any parse failure so the caller can silently skip
    duration computation rather than crash the entire message list.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def build_raw_file_url(workspace_id: str, source_path: str) -> str:
    """构造后端附件的 HTTP 访问地址（存储无关的单一真相源）。

    返回 ``/api/files/raw?workspace_id=<wid>&path=<encoded source_path>``
    （不含 _token：浏览器 <img>/下载需鉴权时由前端按需补 _token 查询串，
    与 get_file_raw 的 _token fallback 一致）。

    云端部署下，source_path 只是逻辑相对路径；真正的文件落地/对象存储
    映射全部收口在后端 get_file_raw，前端只消费这个 HTTP 地址即可，
    无需感知 workspace_id 拼装规则或底层存储形态。
    """
    return (
        "/api/files/raw"
        f"?workspace_id={quote(workspace_id, safe='')}"
        f"&path={quote(source_path, safe='')}"
    )


def _message_has_visible_content(content: object) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for block in content:
        b_type = getattr(block, "type", None)
        if b_type == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                return True
        elif b_type == "image":
            return True
        elif b_type == "file":
            path = getattr(block, "path", None)
            if isinstance(path, str) and path.strip():
                return True
        elif isinstance(block, dict):
            t = block.get("type")
            if (
                t == "text"
                and isinstance(block.get("text"), str)
                and str(block.get("text")).strip()
            ):
                return True
            if t == "image":
                return True
            if (
                t == "file"
                and isinstance(block.get("path"), str)
                and str(block.get("path")).strip()
            ):
                return True
    return False


_THINKING_BLOCK = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)
_SYSTEM_REMINDER_BLOCK = re.compile(
    r"<system-reminder>.*?</system-reminder>", re.IGNORECASE | re.DOTALL
)


def _sanitize_text(text: str, *, strip_references: bool = False) -> str:
    out = text or ""
    out = _SYSTEM_REMINDER_BLOCK.sub("", out)
    out = _THINKING_BLOCK.sub("", out)
    # 端点缺 tool-call parser 时模型会把原生调用标记写进正文；历史落库的旧
    # 消息也可能带着它们，回放出口统一剔除（与实时链路 strip_noise 同源）。
    out = strip_tool_markup(out)
    # 仅 assistant 的来源块是内部协议；用户可能原样讨论这段语法，不能删其正文。
    if strip_references:
        out = strip_reference_block(out)
    out = redact_server_paths(out)
    return out.strip()


def _try_recover_repr_blocks(raw: str) -> list[dict[str, Any]] | None:
    """Best-effort recovery for ContentBlock repr strings.

    When event_logger.message_entry() used ``str(list[ContentBlock])`` it wrote
    pydantic-model reprs like ``[ImageBlock(type='image', data='', ...),
    TextBlock(type='text', text='...')]`` into USER_MESSAGE event.content.
    After persistence and reload those reprs become plain Python strings
    — this helper detects that signature and reconstructs typed block dicts.

    The format is our own pydantic model repr, stable across versions.
    """
    if not isinstance(raw, str) or not raw.startswith("["):
        return None
    if not any(tag in raw for tag in ("ImageBlock(", "TextBlock(", "FileBlock(")):
        return None

    # Split on pydantic model tags like ", ImageBlock(" and ", TextBlock("
    # — simple bracket-balanced scan avoids complex regex on repr internals.
    blocks: list[dict[str, Any]] = []
    # Detect all block-type occurrences first
    pattern = re.compile(r"(ImageBlock|TextBlock|FileBlock)\(([^)]*)\)")
    for match in pattern.finditer(raw):
        cls_name = match.group(1)
        body = match.group(2)
        block: dict[str, Any] = {"type": cls_name.replace("Block", "").lower()}
        # Parse key='value' pairs — values are repr of Python literals
        # (single-quoted strings, numbers, None). We only need fields we use.
        kv_pattern = re.compile(r"(\w+)=('[^']*'|\"[^\"]*\"|None|\d+(?:\.\d+)?)")
        for kv in kv_pattern.finditer(body):
            key = kv.group(1)
            val = kv.group(2)
            if val == "None":
                parsed: Any = None
            elif val.startswith("'") or val.startswith('"'):
                parsed = val[1:-1]
            else:
                try:
                    parsed = float(val) if "." in val else int(val)
                except ValueError:
                    parsed = val
            block[key] = parsed
        # 'type' is redundant since we derive it from class name
        block.pop("type", None)
        if "type" not in block:
            block["type"] = cls_name.replace("Block", "").lower()
        blocks.append(block)

    if not blocks:
        return None
    return blocks


def _sanitize_content(
    content: object,
    *,
    workspace_id: str | None = None,
    strip_references: bool = False,
) -> object:
    if isinstance(content, str):
        # Backward-compat: old sessions may have Python repr strings from
        # the buggy str(list[ContentBlock]) in event_logger.message_entry().
        recovered = _try_recover_repr_blocks(content)
        if recovered is not None:
            # Recurse into the list branch below for normal sanitization
            return _sanitize_content(
                recovered,
                workspace_id=workspace_id,
                strip_references=strip_references,
            )
        return _sanitize_text(content, strip_references=strip_references)
    if not isinstance(content, list):
        return content
    cleaned: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                # 去 OOXML 段落锚点前缀 [w14:paraId]，该内部编辑句柄对用户无意义
                text = _PARA_ID_PREFIX_RE.sub("", block["text"], count=1)
                text = _sanitize_text(text, strip_references=strip_references)
                if text:
                    cleaned.append({**block, "text": text})
                continue
            # 非文本块原样透传；为 image/file 块补 backend 生成的 file_url
            # （刷新重放时让前端直接拿到 HTTP 地址，存储无关、无需前端拼装）。
            btype = block.get("type")
            if workspace_id and btype in ("image", "image_url", "file"):
                src = block.get("source_path") or block.get("path")
                if isinstance(src, str) and src.strip():
                    block = {**block, "file_url": build_raw_file_url(workspace_id, src.strip())}
            cleaned.append(block)
            continue
        b_type = getattr(block, "type", None)
        if b_type == "text":
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                # 去 OOXML 段落锚点前缀 [w14:paraId]（同 dict 分支）
                sanitized = _PARA_ID_PREFIX_RE.sub("", block_text, count=1)
                sanitized = _sanitize_text(sanitized, strip_references=strip_references)
                if sanitized:
                    cleaned.append({"type": "text", "text": sanitized})
            continue
        if b_type == "image":
            img_payload: dict[str, Any] = {"type": "image"}
            sp = getattr(block, "source_path", None)
            if isinstance(sp, str) and sp.strip():
                sp = sp.strip()
                img_payload["source_path"] = sp
                if workspace_id:
                    img_payload["file_url"] = build_raw_file_url(workspace_id, sp)
            mt = getattr(block, "mime_type", None)
            if isinstance(mt, str) and mt.strip():
                img_payload["mime_type"] = mt.strip()
            cleaned.append(img_payload)
            continue
        if b_type == "file":
            path = getattr(block, "path", None)
            name = getattr(block, "name", None)
            mime_type = getattr(block, "mime_type", None)
            is_source = getattr(block, "is_source_document", False)
            if isinstance(path, str):
                path = path.strip()
                payload: dict[str, Any] = {"type": "file", "path": path}
                if isinstance(name, str):
                    payload["name"] = name
                if isinstance(mime_type, str):
                    payload["mime_type"] = mime_type
                if is_source:
                    payload["is_source_document"] = True
                if workspace_id:
                    payload["file_url"] = build_raw_file_url(workspace_id, path)
                cleaned.append(payload)
            continue
    return cleaned


def _content_to_text(content: object) -> str:
    """把 content block 投影为纯文本——两个调用方：

    1. assistant 正文拼接 → 后续走 _sanitize_text，那里统一过滤服务器路径
    2. tool_result_by_call_id（历史回放的 ExecutionTimeline 工具结果展示）
       → 不过滤，工具执行细节对用户理解"AI 在做什么"是有意义的

    本函数只做结构投影，不做内容审查。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "file" and isinstance(block.get("path"), str):
                name = block.get("name")
                path = block["path"].strip()
                if path:
                    display_name = (
                        name.strip() if isinstance(name, str) and name.strip() else Path(path).name
                    )
                    parts.append(f"[附件] {display_name} ({path})")
            continue
        b_type = getattr(block, "type", None)
        if b_type == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        elif b_type == "file":
            path = getattr(block, "path", None)
            if isinstance(path, str) and path.strip():
                name = getattr(block, "name", None)
                display_name = (
                    name.strip() if isinstance(name, str) and name.strip() else Path(path).name
                )
                parts.append(f"[附件] {display_name} ({path.strip()})")
    return "\n".join([p for p in parts if p is not None])


def _coerce_tool_arguments(args: object) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        raw = args.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _build_display_messages(
    tree: SessionTree,
    *,
    review_utils: dict[str, Callable[..., object]] | None = None,
    retain_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """把会话树投影为展示消息。

    ``retain_ids``：正文为空、无 tool_calls 也必须保留的 assistant entry
    （挂着 execution_timeline 等元数据）。不传则维持"不可见即丢弃"。
    """
    # 附件 URL 需要 workspace_id 才能拼出后端 /api/files/raw 地址；从 tree.header
    # 直接取（缺省 None 时不附加 file_url，向后兼容无 header 的测试桩）。
    workspace_id = getattr(getattr(tree, "header", None), "workspace_id", None)
    ordered = [tree.entries[eid] for eid in tree.order if eid in tree.entries]

    tool_result_by_call_id: dict[str, str] = {}
    for entry in ordered:
        if not isinstance(entry, MessageEntry):
            continue
        if entry.role != "tool":
            continue
        if not entry.tool_call_id:
            continue
        tool_result_by_call_id[entry.tool_call_id] = _content_to_text(entry.content)

    out: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []
    # 正文 = 完整转录（含中间迭代的自然语言过程说明）；
    # 思考/reasoning 与工具调用由前端分别渲染。
    # 列表每项是已拼接完成的一轮自然语言文本 str，所有迭代均计入正文。
    pending_assistant_text_parts: list[str] = []
    pending_assistant_last_raw_final: Any = None
    pending_assistant_id: str | None = None
    pending_assistant_ts: Any = None
    # 异常终止时补写的消息（metadata.interrupted）聚合后仍要给用户可见标记。
    pending_assistant_interrupted: bool = False
    # 展示消息聚合了多条 entry，增量对账需要"这组里最新的那次触碰"，
    # 因此与 tool_calls 同生命周期地追踪组内最大 seq，flush 时一并落盘。
    pending_assistant_seq: int = 0
    # 追踪上一条 user 消息的 epoch ms，供 flush_assistant 回填 durationMs
    # （与前端 useChatSocket 实时口径一致：assistant_ts − last_user_ts）。
    last_user_ts_ms: int | None = None

    def flush_assistant() -> None:
        nonlocal \
            pending_tool_calls, \
            pending_assistant_text_parts, \
            pending_assistant_last_raw_final, \
            pending_assistant_id, \
            pending_assistant_ts, \
            pending_assistant_interrupted, \
            pending_assistant_seq
        if (
            not pending_assistant_text_parts
            and not pending_tool_calls
            # 正文为空但挂着元数据（时间线/交付物）的回合必须出条，作为挂靠点。
            and not (retain_ids is not None and pending_assistant_id in retain_ids)
        ):
            pending_tool_calls = []
            pending_assistant_text_parts = []
            pending_assistant_last_raw_final = None
            pending_assistant_id = None
            pending_assistant_ts = None
            pending_assistant_interrupted = False
            pending_assistant_seq = 0
            return
        # 正文 = 最后一条非空的自然语言答复（单一真相源）。
        # 中间迭代的过语已由 timeline projector._segment_texts 收集并在灰框展示，
        # 不应同时拼进正文蓝框。终态轮次（运行结束时仍在进行的那一轮，
        # 由 complete_no_tool_turn 落 session tree assistant entry）是正文的唯一来源；
        # 前面的轮次 content 已在执行层置空，这里取最后一条非空 part 即为终态轮。
        all_texts = list(pending_assistant_text_parts)
        content = ""
        for part in reversed(all_texts):
            if part.strip():
                content = part
                break
        payload: dict[str, Any] = {
            "id": pending_assistant_id,
            "role": "assistant",
            "content": content,
            "timestamp": pending_assistant_ts,
            "seq": pending_assistant_seq,
        }
        # 回填执行时长：assistant 消息时间戳 − 触发它的 user 消息时间戳。
        # 与 useChatSocket.chat.done 口径一致；实时场景 metadata 由前端写入，
        # 刷新重放场景由后端在此兜底，保证 durationMs 不丢失。
        _asst_ms = _iso_to_ms(pending_assistant_ts)
        metadata: dict[str, Any] = {}
        if _asst_ms is not None and last_user_ts_ms is not None:
            metadata["durationMs"] = max(0, _asst_ms - last_user_ts_ms)
        # 异常终止补写的消息：刷新后仍需给用户可见的中断标记
        if pending_assistant_interrupted:
            metadata["interrupted"] = True
        if metadata:
            payload["metadata"] = metadata
        if pending_tool_calls:
            simplified: list[dict[str, Any]] = []
            for tc in pending_tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id")
                raw_fn = tc.get("function")
                fn: dict[str, Any] = raw_fn if isinstance(raw_fn, dict) else {}
                name = fn.get("name")
                args = fn.get("arguments")
                if isinstance(tc_id, str) and isinstance(name, str) and name:
                    coerced_args = _coerce_tool_arguments(args)
                    # 契约边界归一化：历史重放读取已持久化的 tool_calls.arguments，
                    # 其中 AskUserQuestion 的 questions 可能是 LLM 原始非规范输出
                    # （缺 description / 字符串选项 / value/name 别名）。在此一次性
                    # 归一成可信 JSON，与实时 clarify_request 共用同一真相源，
                    # 前端只做直读渲染。
                    if (
                        "ask_user" in name.lower()
                        and isinstance(coerced_args, dict)
                        and "questions" in coerced_args
                    ):
                        coerced_args = dict(coerced_args)
                        _ncq = (review_utils or {}).get("normalize_clarify_questions")
                        coerced_args["questions"] = (
                            _ncq(coerced_args.get("questions"))
                            if _ncq
                            else coerced_args.get("questions")
                        )
                    simplified.append(
                        {
                            "id": tc_id,
                            "name": name,
                            "arguments": coerced_args,
                            "result": tool_result_by_call_id.get(tc_id, ""),
                            "status": "done",
                        }
                    )
            payload["tool_calls"] = simplified
        out.append(payload)
        pending_tool_calls = []
        pending_assistant_text_parts = []
        pending_assistant_last_raw_final = None
        pending_assistant_id = None
        pending_assistant_ts = None
        pending_assistant_interrupted = False
        pending_assistant_seq = 0

    for entry in ordered:
        if not isinstance(entry, MessageEntry):
            continue
        if entry.role == "tool":
            pending_assistant_seq = max(pending_assistant_seq, entry.seq)
            # AskUserQuestion 是执行边界：提问气泡必须在工具结果处收束，
            # 否则用户作答后的续执行文本会被并入提问气泡（HITL 答案本身
            # 只作为该工具结果存在，不再额外落 user entry）。
            _tool_name = str(entry.tool_name or "").lower()
            _is_ask_result = "ask_user" in _tool_name or "askuser" in _tool_name
            if not _is_ask_result:
                for _tc in pending_tool_calls:
                    if _tc.get("id") != entry.tool_call_id:
                        continue
                    _fn = _tc.get("function")
                    _name = (
                        str((_fn or {}).get("name") or "").lower() if isinstance(_fn, dict) else ""
                    )
                    _is_ask_result = "ask_user" in _name or "askuser" in _name
                    break
            if _is_ask_result:
                flush_assistant()
            continue
        sanitized_content = _sanitize_content(
            entry.content,
            workspace_id=workspace_id,
            strip_references=entry.role == "assistant",
        )
        has_visible_content = _message_has_visible_content(sanitized_content)
        # assistant entry 携带 tool_calls 时本身即为可见内容（工具调用需进
        # timeline），即便无文本也不应被跳过；正文为空但挂了 timeline /
        # 交付物元数据的回合由 retain_ids 保住。
        has_tool_calls = entry.role == "assistant" and bool(entry.tool_calls)
        is_retained = (
            entry.role == "assistant" and retain_ids is not None and entry.id in retain_ids
        )
        if not has_visible_content and not has_tool_calls and not is_retained:
            continue

        if entry.role == "user":
            flush_assistant()
            last_user_ts_ms = _iso_to_ms(entry.timestamp)
            out.append(
                {
                    "id": entry.id,
                    "role": entry.role,
                    "content": sanitized_content,
                    "timestamp": entry.timestamp,
                    "seq": entry.seq,
                    # 客户端乐观气泡的标识：客户端据此把本地消息 re-key 为
                    # 服务端 entry（id + seq），避免同一提问出现两个气泡。
                    "client_message_id": entry.client_message_id,
                }
            )
            continue

        if entry.role == "assistant":
            # 记录元数据（末迭代覆盖中间迭代，id/ts 指向最后一个 assistant entry）
            pending_assistant_id = entry.id
            pending_assistant_ts = entry.timestamp
            # 异常终止补写标记（正文/工具为空时仍保住聚合输出）
            pending_assistant_interrupted = pending_assistant_interrupted or entry.interrupted
            pending_assistant_seq = max(pending_assistant_seq, entry.seq)
            # 正文 = 完整转录（含中间迭代的自然语言过程说明）；
            # 思考/reasoning 与工具调用由前端分别渲染。
            # 每一轮 assistant entry 的文字均计入正文，不再区分是否最终答案。
            if has_visible_content:
                pending_assistant_text_parts.append(_content_to_text(sanitized_content))
                pending_assistant_last_raw_final = sanitized_content
            if entry.tool_calls:
                pending_tool_calls.extend([tc for tc in entry.tool_calls if isinstance(tc, dict)])
            continue

    flush_assistant()
    return out
