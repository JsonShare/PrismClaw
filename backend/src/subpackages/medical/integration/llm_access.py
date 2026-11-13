"""医疗域的模型客户端访问器 — 域与核心模型基建的唯一接触面。

按 bundle.py 的架构纪律，domain / persistence / services / api 都不直接导入核心，
需要的模型能力在这里包一层再给出去。好处是：核心换了 LLM 封装，医疗域只改这一个文件。

提供四样东西：
- get_medical_llm    核心文本模型客户端（模型名由 models.default_model 全局决定）
- get_medical_vision 视觉模型（扫描件 OCR，配置缺失时返回 None）
- complete_json      结构化调用（tool_calls 优先，正文 JSON 兜底，失败重试）
- ocr_image          图片转文字
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from fastapi import Request

from prismclaw.engine.services.llm.client import LiteLLMClient
from prismclaw.schema.llm.request import PackedLLMRequest

logger = logging.getLogger(__name__)

_VISION_STATE_KEY = "medical_vision_client"

# 结构化调用失败重试次数：第一次温度 0 追求稳定，第二次 0.2 换个解码空间
JSON_MAX_ATTEMPTS = 2

# 全局 LLM 信号量：parse_pipeline + analysis_pipeline 共享
# 两阶段（元数据+结构化）串行占用 1 个槽，不叠加
MEDICAL_LLM_SEMAPHORE = asyncio.Semaphore(2)

OCR_SYSTEM = (
    "你是医学文档 OCR 助手。逐字输出图片中的文字，保留原有段落、项目符号与表格结构。"
    "不要翻译、不要总结、不要添加解释，也不要输出图片里没有的内容。"
)
OCR_PROMPT = "请输出这张病历图片中的全部文字。"


def get_medical_llm(request: Request) -> Any | None:
    """核心文本模型客户端（模型名由 models.default_model 全局决定）。

    返回 LiteLLMClient 实例，未装配时为 None。
    """
    deps = getattr(request.app.state, "runner_deps", None)
    return getattr(deps, "llm", None)


def get_medical_vision(request: Request) -> LiteLLMClient | None:
    """
    视觉模型客户端，按 settings.vision 构造并缓存在 app.state 上。

    视觉模型不在 models.providers 里（它是按功能点单独配置的），
    所以不能复用核心那一个 client，得单独建。
    """
    cached = getattr(request.app.state, _VISION_STATE_KEY, None)
    if cached is not None:
        return cached

    vision = getattr(getattr(request.app.state, "settings", None), "vision", None)
    model = getattr(vision, "model", "") or ""
    base_url = getattr(vision, "base_url", "") or ""
    if not model or not base_url:
        logger.warning("未配置视觉模型，扫描件病历无法 OCR")
        return None

    client = LiteLLMClient(
        default_model=model,
        api_key=getattr(vision, "api_key", "") or "",
        api_base=base_url,
        # OCR 场景不需要长思考，且该端点无 providers 配置可声明
        disable_thinking=True,
    )
    setattr(request.app.state, _VISION_STATE_KEY, client)
    return client


async def complete_json(
    llm: Any,
    *,
    system: str,
    prompt: str,
    tool: dict[str, Any],
    tool_name: str,
    attempts: int = JSON_MAX_ATTEMPTS,
) -> dict[str, Any] | None:
    """让模型按 tool schema 输出 JSON，返回归一化前的原始 dict。

    返回 None 表示「调通了但没解析出结构」，调用方按该段留空处理；
    抛异常表示模型不可用，由调用方决定整条流水线是否失败。

    三层防御：
    1. tool_calls 方式（标准 function calling）
    2. _robust_json_extract 从正文提取（思考标签 / Markdown / 暴力扫描）
    3. 纯文本 JSON prompt（不带 tools 参数，兼容不支持 function calling 的端点）
    """
    from ..domain.parsing.prompts import _robust_json_extract, parse_tool_payload

    if llm is None:
        raise RuntimeError("未装配 LLM 客户端")

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await llm.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system=system,
                    context_payload=None,
                    cache_strategy={},
                ),
                tools=[tool],
                temperature=0.2 if attempt else 0,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("医疗解析 LLM 调用失败（第 %d 次）: %s", attempt + 1, exc)
            continue

        payload = parse_tool_payload(response, tool_name)
        if payload is not None:
            return payload
        logger.info("医疗解析未取到结构化输出，重试（第 %d 次）", attempt + 1)

    # ── Defense 3：纯文本 JSON prompt（无 tools 参数）──
    # 适用于 vLLM 等不支持 function calling 的端点
    logger.info("医疗解析 tool_calls 方式全部失败，尝试纯文本 JSON prompt")
    json_prompt = (
        prompt
        + "\n\n请严格按以下 JSON schema 输出，不要输出其他内容：\n"
        + json.dumps(tool, ensure_ascii=False, indent=2)
        + "\n\n直接输出 JSON，不要包裹在代码块中。"
    )
    try:
        response = await llm.complete(
            request=PackedLLMRequest(
                messages=[{"role": "user", "content": json_prompt}],
                system=system,
                context_payload=None,
                cache_strategy={},
            ),
            temperature=0,
        )
        payload = _robust_json_extract(getattr(response, "text", "") or "")
        if payload is not None:
            logger.info("医疗解析纯文本 JSON prompt 成功")
            return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("医疗解析纯文本 JSON prompt 也失败: %s", exc)

    if last_error is not None:
        raise last_error
    return None


# 单张图片 OCR 超时（秒）。Vision 模型 MidStreamFallbackError 会导致无限挂起，
# 此超时确保单页 OCR 快速失败，不阻塞整条并行流水线。
OCR_PER_IMAGE_TIMEOUT = 60


async def ocr_image(vision: Any, image_bytes: bytes, mime: str) -> str:
    """视觉模型读图转文字。

    带 asyncio.wait_for 超时保护：Vision 模型偶尔会出现
    MidStreamFallbackError（重复生成同一 chunk），导致流式响应无限挂起。
    超时后抛 asyncio.TimeoutError，由 _ocr_join_parallel 的 per-page
    异常捕获处理——该页标记失败，其余页继续。
    """
    encoded = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = await asyncio.wait_for(
            vision.complete(
                request=PackedLLMRequest(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": OCR_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                                },
                            ],
                        }
                    ],
                    system=OCR_SYSTEM,
                    context_payload=None,
                    cache_strategy={},
                ),
                temperature=0,
            ),
            timeout=OCR_PER_IMAGE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("OCR 单页超时 (%ds)，跳过该页", OCR_PER_IMAGE_TIMEOUT)
        raise
    return (getattr(response, "text", "") or "").strip()
