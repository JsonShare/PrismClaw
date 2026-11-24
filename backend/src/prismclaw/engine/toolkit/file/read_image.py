"""read 的图片分支 — 经视觉模型读取图片并返回文字描述。

图片读取已并入 `read` 工具（file_tools.py 的 ReadTool 按扩展名分发到本模块）。
本模块只保留视觉识别机制：读取图片字节、压缩、调用内部 vLLM 视觉端点。

Supports: PNG, JPEG, GIF, WebP, BMP, SVG, ICO, TIFF.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from prismclaw.engine.toolkit.builtin.file_helpers import session_root
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root
from prismclaw.engine.toolkit.file.ocr_cache import read_ocr_cache, write_ocr_cache
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.engine.toolkit.misc.image_preprocess import (
    ImageTooLargeError,
    compress_for_vision_bytes,
)
from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
    ToolResult,
    error_result,
    text_result,
)
from prismclaw.schema.config.config import VisionConfig

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset(
    (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico", ".tiff", ".tif")
)

DEFAULT_QUESTION = (
    "请对这张图片进行客观、实事求是的分析，严格按以下结构输出：\n\n"
    "【文字内容】逐字转录图片中**实际可见**的文字（标题、正文、标注、表格、图例等），"
    "保持原有排版。禁止推测、脑补或添加图片中不存在的内容（如常见模板语句、"
    "水印文字、印章内文字等若图片中不可辨认则不得编造）。"
    "若图片中完全没有文字，注明「无文字」。\n"
    "【图片描述】描述图片的类型（文档截图/照片/示意图/流程图/数据图表/UI界面等）、"
    "整体布局、主要视觉元素。若为照片或实物图，描述所见场景与物体。\n"
    "【关键数据】若图片含表格、图表、流程图等结构化信息，提取核心数据或步骤；"
    "若无则省略本节。"
)


_vision_config: VisionConfig | None = None

# 进程级视觉并发配额。文档 OCR 与附件预览共用：单请求内多线程 × 多个并发
# read 会叠加出远超端点承载的线程数（实测端点 8 路以后吞吐不再增长）。
OCR_CONCURRENCY = 8
_OCR_SEMAPHORE = threading.Semaphore(OCR_CONCURRENCY)

# 扫描页转录提示词：只要文字，不要分析，避免多生成 1.8–2.7 倍 token。
_TRANSCRIBE_PROMPT = (
    "请逐字转录这张图片中所有可见文字，保留原有段落结构与项目符号。\n"
    "只输出转录内容，不要加解释、不要推测、不要补充图片中不存在的内容。"
)

# 单次视觉请求的输出上限。密集页在 1024 下会被截断（实测 3119/4929 字符）。
_MAX_TOKENS = 4096


def set_vision_config(config: VisionConfig) -> None:
    global _vision_config
    _vision_config = config


def _get_vision_config() -> tuple[str, str, str, int]:
    """从 vision 配置返回 (endpoint, model, api_key, timeout)。"""
    v = _vision_config or VisionConfig()
    base_url = v.base_url.rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    return endpoint, v.model, v.api_key, v.timeout_seconds


def _post_vision(
    endpoint: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    timeout: float,
    label: str,
) -> dict[str, Any] | tuple[None, str]:
    """发一次视觉请求，返回响应 dict 或 (None, 错误串)。

    错误分支由 :func:`_vision_chat` 统一翻译成用户可读文案。
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    try:
        with _OCR_SEMAPHORE, urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed: object = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_msg = json.loads(err_body).get("error", {}).get("message", err_body)
        except Exception:  # noqa: BLE001 - 错误体不是 JSON
            err_msg = str(exc)
        logger.warning("vLLM vision failed for %s: %s", label, err_msg)
        return None, f"HTTP {exc.code}: {err_msg}"
    except Exception as exc:  # noqa: BLE001 - 网络/超时统一转错误串
        logger.warning("vLLM vision failed for %s: %s", label, exc)
        return None, str(exc)

    if not isinstance(parsed, dict):
        return None, "视觉响应不是 JSON 对象"
    return {str(key): value for key, value in parsed.items()}


def _vision_chat(
    prompt: str,
    img_data: bytes,
    *,
    ext: str,
    timeout: float,
    label: str,
) -> tuple[str, str | None]:
    """一次视觉请求：压缩 → base64 → HTTP。返回 (text, error)。

    唯一持有 ``_OCR_SEMAPHORE`` 的位置：所有视觉调用（图片 read、文档扫描页、
    附件预览）共用同一配额，避免并发叠加击穿端点。

    ``finish_reason == "length"`` 视为失败并返回错误，不落缓存：截断的文本
    当作完整页使用会静默丢内容。
    """
    try:
        compressed, mime = compress_for_vision_bytes(img_data, ext=ext)
    except ImageTooLargeError as exc:
        return "", str(exc)
    except Exception as exc:  # noqa: BLE001 - 图片本身损坏
        return "", f"image processing failed: {exc}"

    b64 = base64.b64encode(compressed).decode("ascii")
    endpoint, model, api_key, _cfg_timeout = _get_vision_config()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ],
        "max_tokens": _MAX_TOKENS,
        # OCR 必须确定性解码：vLLM 默认 temperature=1.0 会在水印/重复文字
        # （如页眉「XX医院」）上退化为重复循环，吞掉全部 max_tokens。
        # temperature=0 贪心解码 + repetition_penalty 抑制水印重复。
        "temperature": 0.0,
        "top_p": 1.0,
        "repetition_penalty": 1.1,
    }

    result = _post_vision(endpoint, payload, api_key, timeout=timeout, label=label)
    if isinstance(result, tuple):
        return "", result[1]
    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        return "", "OCR 输出达到 max_tokens 上限被截断"
    return str(choice["message"]["content"]), None


def ocr_image(jpeg: bytes, *, deadline: float) -> tuple[str, str | None]:
    """转录一页扫描件图像。返回 (text, error)。

    ``deadline`` 是 ``time.monotonic()`` 时间戳；单次请求超时取配置超时与剩余
    预算的较小值。不持任何锁，可在并发池里直接调用（并发由 ``_OCR_SEMAPHORE``
    在传输层收敛）。
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return "", "OCR 超时预算已用尽"
    _, _, _, cfg_timeout = _get_vision_config()
    timeout = max(1.0, min(float(cfg_timeout), remaining))
    return _vision_chat(_TRANSCRIBE_PROMPT, jpeg, ext="jpg", timeout=timeout, label="<page>")


def recognize_image(
    path: str, img_data: bytes, question: str, workspace_path: str | None = None
) -> tuple[str, str | None]:
    """压缩 → base64 → 调视觉端点。返回 (text, error)，二者必有其一。

    仅当使用默认提问时读写 OCR 缓存：自定义提问的回答不可复用。
    """
    # 查缓存：仅缓存默认 question
    if question == DEFAULT_QUESTION and workspace_path:
        cached_text = read_ocr_cache(workspace_path, path)
        if cached_text:
            return cached_text, None

    _, model, _, timeout = _get_vision_config()
    ext = Path(path).suffix.lower().lstrip(".")
    text, error = _vision_chat(question, img_data, ext=ext, timeout=float(timeout), label=path)
    if error:
        return "", error
    if question == DEFAULT_QUESTION and workspace_path:
        write_ocr_cache(workspace_path, path, text, model)
    return text, None


async def read_image_branch(
    *,
    call_id: str,
    raw_path: str,
    question: str | None,
    context: ToolContext,
    resolver: WorkspaceResolver,
) -> ToolResult:
    """read 工具的图片分支：视觉模型识别并返回文字描述。"""
    ext = raw_path.lower().rsplit(".", 1)[-1] if "." in raw_path else ""
    ext_with_dot = f".{ext}" if ext else ""

    if ext_with_dot not in IMAGE_EXTENSIONS:
        return error_result(
            call_id,
            f"read：不支持的图片格式 '{ext}'。支持的格式：{', '.join(sorted(IMAGE_EXTENSIONS))}",
        )

    try:
        full_path = resolver.resolve_within(
            context.workspace_path,
            raw_path,
            session_root=session_root(context),
        )
    except WorkspaceBoundaryError as exc:
        return error_result(call_id, str(exc))

    runtime = require_runtime(context)
    r_result = await runtime.read_bytes(
        str(full_path),
        template_id=ws_kind(context),
        workspace_id=context.workspace_id,
        workspace_root=ws_root(context),
    )
    if not r_result.success:
        return error_result(call_id, f"read：读取 {raw_path} 失败：{r_result.error}")

    content = r_result.content
    if content is None:
        return error_result(call_id, f"read：读取 {raw_path} 失败：内容为空")
    raw_bytes = content if isinstance(content, bytes) else content.encode("utf-8")

    question = question or DEFAULT_QUESTION

    # Dispatch to vision model in thread pool (blocking HTTP call)
    text, error = await asyncio.to_thread(
        recognize_image,
        raw_path,
        raw_bytes,
        question,
        workspace_path=str(context.workspace_path),
    )

    if error:
        return error_result(call_id, f"read：{raw_path} 的视觉模型识别失败：{error}")

    return text_result(
        call_id,
        text,
        metadata={
            "file_path": raw_path,
            "size_bytes": len(raw_bytes),
        },
    )
