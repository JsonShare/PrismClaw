"""Message display helpers extracted from routes.py.

Sanitization, content extraction, and display-message construction
utilities used by the session message endpoints.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session import SessionTree


# OOXML 段落锚点前缀：document_image_extract 在每段前加 ``[w14:paraId] `` 供 docx
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


def _message_has_visible_content(content: Any) -> bool:
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
            if t == "text" and isinstance(block.get("text"), str) and str(block.get("text")).strip():
                return True
            if t == "image":
                return True
            if t == "file" and isinstance(block.get("path"), str) and str(block.get("path")).strip():
                return True
    return False


_THINKING_BLOCK = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)
_SYSTEM_REMINDER_BLOCK = re.compile(r"<system-reminder>.*?</system-reminder>", re.IGNORECASE | re.DOTALL)
_INTERNAL_ASSISTANT_NOISE_BLOCKS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"You have already used `web_search` for multiple consecutive turns\..*",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"已使用 \d+ 次，达到单次运行上限.*",
        re.DOTALL,
    ),
    re.compile(
        r"As you answer the user's questions, you can use the following context:.*",
        re.IGNORECASE | re.DOTALL,
    ),
)


def _sanitize_text(text: str) -> str:
    out = text or ""
    out = _SYSTEM_REMINDER_BLOCK.sub("", out)
    out = _THINKING_BLOCK.sub("", out)
    for pat in _INTERNAL_ASSISTANT_NOISE_BLOCKS:
        out = pat.sub("", out)
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


def _sanitize_content(content: Any, *, workspace_id: str | None = None) -> Any:
    if isinstance(content, str):
        # Backward-compat: old sessions may have Python repr strings from
        # the buggy str(list[ContentBlock]) in event_logger.message_entry().
        recovered = _try_recover_repr_blocks(content)
        if recovered is not None:
            # Recurse into the list branch below for normal sanitization
            return _sanitize_content(recovered, workspace_id=workspace_id)
        return _sanitize_text(content)
    if not isinstance(content, list):
        return content
    cleaned: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                # 去 OOXML 段落锚点前缀 [w14:paraId]，该内部编辑句柄对用户无意义
                text = _PARA_ID_PREFIX_RE.sub("", block["text"], count=1)
                text = _sanitize_text(text)
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
            text = getattr(block, "text", None)
            if isinstance(text, str):
                # 去 OOXML 段落锚点前缀 [w14:paraId]（同 dict 分支）
                sanitized = _PARA_ID_PREFIX_RE.sub("", text, count=1)
                sanitized = _sanitize_text(sanitized)
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


def _content_to_text(content: Any) -> str:
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
                    display_name = name.strip() if isinstance(name, str) and name.strip() else Path(path).name
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
                display_name = name.strip() if isinstance(name, str) and name.strip() else Path(path).name
                parts.append(f"[附件] {display_name} ({path.strip()})")
    return "\n".join([p for p in parts if p is not None])


def _coerce_tool_arguments(args: Any) -> dict[str, Any]:
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


def _build_display_messages(tree: SessionTree, *, review_utils: Any = None) -> list[dict[str, Any]]:
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
    # 异常终止 run 的兜底 partial 消息可能正文为空、无 tool_calls，仅作为
    # execution_timeline 的挂靠点存在。聚合丢弃它会让刷新后执行过程永久
    # 丢失（timeline 挂在无人引用的 id 上）。
    pending_assistant_partial: bool = False
    # 追踪上一条 user 消息的 epoch ms，供 flush_assistant 回填 durationMs
    # （与前端 useChatSocket 实时口径一致：assistant_ts − last_user_ts）。
    last_user_ts_ms: int | None = None

    def flush_assistant() -> None:
        nonlocal pending_tool_calls, pending_assistant_text_parts, pending_assistant_last_raw_final, pending_assistant_id, pending_assistant_ts, pending_assistant_partial
        if (
            not pending_assistant_text_parts
            and not pending_tool_calls
            and not pending_assistant_partial
        ):
            pending_tool_calls = []
            pending_assistant_text_parts = []
            pending_assistant_last_raw_final = None
            pending_assistant_id = None
            pending_assistant_ts = None
            pending_assistant_partial = False
            return
        # 正文 = 完整转录：聚合所有迭代的自然语言过程说明，
        # 以 "\n\n" 连接，与实时流式提交的最终消息保持一致（invariant I3）。
        all_texts = list(pending_assistant_text_parts)
        if len(all_texts) == 1 and pending_assistant_last_raw_final is not None:
            content = pending_assistant_last_raw_final
        elif len(all_texts) >= 1:
            content = "\n\n".join(all_texts)
        else:
            content = (
                pending_assistant_last_raw_final
                if pending_assistant_last_raw_final is not None
                else ""
            )
        payload: dict[str, Any] = {
            "id": pending_assistant_id,
            "role": "assistant",
            "content": content,
            "timestamp": pending_assistant_ts,
        }
        # 回填执行时长：assistant 消息时间戳 − 触发它的 user 消息时间戳。
        # 与 useChatSocket.chat.done 口径一致；实时场景 metadata 由前端写入，
        # 刷新重放场景由后端在此兜底，保证 durationMs 不丢失。
        _asst_ms = _iso_to_ms(pending_assistant_ts)
        metadata: dict[str, Any] = {}
        if _asst_ms is not None and last_user_ts_ms is not None:
            metadata["durationMs"] = max(0, _asst_ms - last_user_ts_ms)
        # partial 表示该轮 assistant 未正常收尾（中止/异常），刷新后仍需给用户可见标记
        if pending_assistant_partial:
            metadata["interrupted"] = True
        if metadata:
            payload["metadata"] = metadata
        if pending_tool_calls:
            simplified: list[dict[str, Any]] = []
            for tc in pending_tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id")
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name")
                args = fn.get("arguments")
                if isinstance(tc_id, str) and isinstance(name, str) and name:
                    coerced_args = _coerce_tool_arguments(args)
                    # 契约边界归一化：历史重放读取已持久化的 tool_calls.arguments，
                    # 其中 AskUserQuestion 的 questions 可能是 LLM 原始非规范输出
                    # （缺 description / 字符串选项 / value/name 别名）。在此一次性
                    # 归一成可信 JSON，与实时 clarify_request 共用同一真相源，
                    # 前端只做直读渲染。
                    if "ask_user" in name.lower() and isinstance(coerced_args, dict) and "questions" in coerced_args:
                        coerced_args = dict(coerced_args)
                        _ncq = (review_utils or {}).get("normalize_clarify_questions")
                        coerced_args["questions"] = _ncq(coerced_args.get("questions")) if _ncq else coerced_args.get("questions")
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
        pending_assistant_partial = False

    for entry in ordered:
        if not isinstance(entry, MessageEntry):
            continue
        if entry.role == "tool":
            continue
        sanitized_content = _sanitize_content(entry.content, workspace_id=workspace_id)
        has_visible_content = _message_has_visible_content(sanitized_content)
        # assistant entry 携带 tool_calls 时本身即为可见内容（工具调用需进
        # timeline），即便无文本也不应被跳过。partial 同理：异常终止 run 的
        # 兜底 partial 消息可能正文为空但挂着 execution_timeline——被过滤
        # 会让刷新后执行过程永久丢失（timeline 挂在无人引用的 id 上）。
        has_tool_calls = entry.role == "assistant" and bool(entry.tool_calls)
        is_partial_assistant = entry.role == "assistant" and entry.partial
        if not has_visible_content and not has_tool_calls and not is_partial_assistant:
            continue

        if entry.role == "user":
            # 人机交互澄清追问答案：已被标记 metadata.kind=="clarification_answer"。
            # 答案已作为「人机交互」步骤内嵌在提问气泡的执行时间线（ask_user 工具行
            # 的 question+answer），此处不再单独成气泡，否则会与上传记录割裂为两条
            # user 记录。落树仍保留该独立 user entry（模型上下文需要）。
            # 仅更新 last_user_ts_ms 维持后续 assistant 时长计算口径，不 emit 气泡。
            _entry_meta = getattr(entry, "metadata", None)
            if isinstance(_entry_meta, dict) and _entry_meta.get("kind") == "clarification_answer":
                # 与正常 user 记录同效：先 flush 前置的 assistant 提问气泡（含
                # ask_user 工具调用 / 执行时间线）。否则该提问气泡会因 pending 被
                # 后续 assistant 覆盖而丢失、或并入下一轮回复（历史回放里整条
                # 「人机交互」问答消失）。答案已内嵌在提问步骤的时间线，此处不
                # emit 独立气泡，仅更新 last_user_ts_ms 维持后续 assistant 时长口径。
                flush_assistant()
                last_user_ts_ms = _iso_to_ms(entry.timestamp)
                continue
            flush_assistant()
            last_user_ts_ms = _iso_to_ms(entry.timestamp)
            out.append(
                {
                    "id": entry.id,
                    "role": entry.role,
                    "content": sanitized_content,
                    "timestamp": entry.timestamp,
                }
            )
            continue

        if entry.role == "assistant":
            # 记录元数据（末迭代覆盖中间迭代，id/ts 指向最后一个 assistant entry）
            pending_assistant_id = entry.id
            pending_assistant_ts = entry.timestamp
            # 异常终止兜底 partial 标记（正文/工具均空时仍保住聚合输出）
            pending_assistant_partial = pending_assistant_partial or bool(entry.partial)
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
