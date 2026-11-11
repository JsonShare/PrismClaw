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
import urllib.error
import urllib.request
from pathlib import Path

from prismclaw.engine.toolkit.misc.image_preprocess import (
    ImageTooLargeError,
    compress_for_vision_bytes,
)
from prismclaw.engine.toolkit.builtin.file_helpers import session_root
from prismclaw.engine.toolkit.builtin.shared_tool_helpers import require_runtime, ws_kind, ws_root
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, ToolResult, error_result, text_result
from prismclaw.engine.toolkit.file.ocr_cache import read_ocr_cache, write_ocr_cache
from prismclaw.engine.toolkit.file.workspace import WorkspaceBoundaryError, WorkspaceResolver
from prismclaw.schema.config.config import VisionConfig

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = frozenset(
    (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico", ".tiff", ".tif")
)

_DEFAULT_QUESTION = (
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


def set_vision_config(config: VisionConfig) -> None:
    global _vision_config
    _vision_config = config


def _get_vision_config() -> tuple[str, str, str, int]:
    """从 vision 配置返回 (endpoint, model, api_key, timeout)。"""
    v = _vision_config or VisionConfig()
    base_url = v.base_url.rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    return endpoint, v.model, v.api_key, v.timeout_seconds


def _recognize_from_bytes(path: str, img_data: bytes, question: str, workspace_path: str | None = None) -> tuple[str, str | None]:
    """Compress + base64 encode + call vLLM API.

    Returns (text, error) — one of them is None.
    """
    # 查缓存：仅缓存默认 question
    if question == _DEFAULT_QUESTION and workspace_path:
        cached_text = read_ocr_cache(workspace_path, path)
        if cached_text:
            return cached_text, None

    ext = Path(path).suffix.lower().lstrip(".")

    try:
        compressed, mime = compress_for_vision_bytes(img_data, ext=ext)
    except ImageTooLargeError as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"image processing failed: {exc}"

    b64 = base64.b64encode(compressed).decode("ascii")

    endpoint, model, api_key, timeout = _get_vision_config()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 4096,
        # OCR 必须确定性解码：vLLM 默认 temperature=1.0 会在水印/重复文字
        # （如页眉「XX医院」）上退化为重复循环，吞掉全部 max_tokens。
        # temperature=0 贪心解码 + repetition_penalty 抑制水印重复。
        "temperature": 0.0,
        "top_p": 1.0,
        "repetition_penalty": 1.1,
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        # 写缓存
        if question == _DEFAULT_QUESTION and workspace_path:
            write_ocr_cache(workspace_path, path, text, model)
        return str(text), None
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_data = json.loads(err_body)
            err_msg = err_data.get("error", {}).get("message", err_body)
        except Exception:
            err_msg = str(exc)
        logger.warning("vLLM vision failed for %s: %s", path, err_msg)
        return "", f"HTTP {exc.code}: {err_msg}"
    except Exception as exc:
        logger.warning("vLLM vision failed for %s: %s", path, exc)
        return "", str(exc)


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

    if ext_with_dot not in _IMAGE_EXTENSIONS:
        return error_result(
            call_id,
            f"read：不支持的图片格式 '{ext}'。"
            f"支持的格式：{', '.join(sorted(_IMAGE_EXTENSIONS))}",
        )

    try:
        full_path = resolver.resolve_within(
            context.workspace_path, raw_path, session_root=session_root(context),
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

    raw_bytes = r_result.content if isinstance(r_result.content, bytes) else r_result.content.encode("utf-8")

    question = question or _DEFAULT_QUESTION

    # Dispatch to vision model in thread pool (blocking HTTP call)
    text, error = await asyncio.to_thread(
        _recognize_from_bytes, raw_path, raw_bytes, question,
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
