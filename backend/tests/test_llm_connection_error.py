"""流式连接错误的分类回归测试。

覆盖 2026-10-26 修复：上游/代理截断 SSE 流时，aiohttp / httpx / litellm 抛出的
底层连接异常（MidStreamFallbackError / APIConnectionError / httpx.ReadError /
aiohttp.ClientPayloadError 等）必须被归类为 CONNECTION_ERROR，而非 UNKNOWN——
否则 pipeline 的重试回路无法把它识别为可重试的 transient 错误。
"""

from __future__ import annotations


from prismclaw.engine.services.llm.provider_resolver import (
    _connection_error_types,
    classify_error,
)
from prismclaw.schema.llm.errors import LLMError, LLMErrorCode


def test_connection_error_types_nonempty():
    # 必须至少覆盖 litellm 的 MidStreamFallbackError / APIConnectionError，
    # 否则流式截断的异常会漏判。
    types = _connection_error_types()
    assert types, "connection error types family must not be empty"


def test_classify_midstream_fallback_is_connection_error():
    from litellm.exceptions import MidStreamFallbackError

    exc = MidStreamFallbackError(
        "litellm.APIConnectionError: Response payload is not completed: "
        "<TransferEncodingError: 400, message='Not enough data to satisfy transfer length header.'>",
        model="qwen3.7-plus",
        llm_provider="openai",
    )
    assert classify_error(exc) == LLMErrorCode.CONNECTION_ERROR


def test_classify_httpx_read_error_is_connection_error():
    import httpx

    exc = httpx.ReadError("Response payload is not completed")
    assert classify_error(exc) == LLMErrorCode.CONNECTION_ERROR


def test_classify_aiohttp_payload_error_is_connection_error():
    import aiohttp

    exc = aiohttp.ClientPayloadError("Response payload is not completed")
    assert classify_error(exc) == LLMErrorCode.CONNECTION_ERROR


def test_classify_generic_value_error_not_connection_error():
    # 负向：普通异常不应被误判为连接错误。
    assert classify_error(ValueError("boom")) == LLMErrorCode.UNKNOWN


def test_classify_llmerror_wrap_once():
    # Wrap-once：LLMError 自带 code，绝不按消息子串重分类。
    exc = LLMError(LLMErrorCode.UNKNOWN, "APIConnectionError happened")
    assert classify_error(exc) == LLMErrorCode.UNKNOWN


def test_classify_camelcase_api_connection_error_message_misses_substring():
    # 显式证明旧子串启发式（'connection error'）命中不了 camelCase 的
    # APIConnectionError——这正是为什么改用 isinstance 类型判定。
    msg = "litellm.APIConnectionError: APIConnectionError: OpenAIException - Response payload is not completed"
    assert "connection error" not in msg.lower()
    # 而类型判定仍能正确归类（用 litellm 真实类型）。
    from litellm.exceptions import APIConnectionError

    assert (
        classify_error(APIConnectionError(msg, llm_provider="openai", model="qwen3.7-plus"))
        == LLMErrorCode.CONNECTION_ERROR
    )
