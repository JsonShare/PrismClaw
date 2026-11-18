"""ProviderRequestAdapter：上下文传输只有两条路径。

有 ``context_payload`` 时渲染为尾部 ``<system-context>`` 消息；无 payload 的
辅助调用直接发送，不进入桥接。
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.services.llm.client import ProviderRequestAdapter


def _request(context_payload: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        messages=[{"role": "user", "content": "hi"}],
        system="sys",
        context_payload=context_payload,
    )


def test_without_payload_skips_bridge() -> None:
    prepared = ProviderRequestAdapter().adapt(
        request=_request(None),
        llm_provider="openai",
        prompt_cache_runtime=None,
    )
    assert [m["role"] for m in prepared.messages] == ["system", "user"]
    assert "<system-context>" not in str(prepared.messages)


def test_payload_is_appended_as_system_context() -> None:
    prepared = ProviderRequestAdapter().adapt(
        request=_request({"blocks": [{"kind": "runtime_context", "content": "t"}]}),
        llm_provider="openai",
        prompt_cache_runtime=None,
    )
    last = prepared.messages[-1]
    assert last["role"] == "user"
    assert "<system-context>" in last["content"][0]["text"]
    assert not hasattr(prepared, "extensions")
