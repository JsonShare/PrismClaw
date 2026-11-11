"""P10-SEC-04: WorkspaceRuntimeClient template_id → agent_url 路由测试。

验证：
  1. 精确匹配 template_id（medical_phi → phi agent）
  2. 未知 kind 回退到 __default__
  3. 完全无 default 时回退到映射第一个值
  4. 从 PRISMCLAW_RUNTIME_AGENT_URLS 环境变量 JSON 构造映射
  5. _post 实际路由到对应 URL（mock httpx 验证 X-Template-ID 头与 base_url）
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismclaw.engine.runtime.client import WorkspaceRuntimeClient


def test_resolve_exact_match():
    c = WorkspaceRuntimeClient(
        agent_urls={
            "general": "http://agent-general:9001",
            "medical_phi": "http://agent-phi:9001",
        }
    )
    assert c._resolve_agent_url("medical_phi") == "http://agent-phi:9001"
    assert c._resolve_agent_url("general") == "http://agent-general:9001"


def test_resolve_falls_back_to_default():
    c = WorkspaceRuntimeClient(
        agent_urls={
            "__default__": "http://agent-default:9001",
            "general": "http://agent-general:9001",
        }
    )
    # unknown kind → __default__
    assert c._resolve_agent_url("legal_case") == "http://agent-default:9001"
    assert c._resolve_agent_url("medical_phi") == "http://agent-default:9001"


def test_resolve_falls_back_to_first_value_without_default():
    c = WorkspaceRuntimeClient(
        agent_urls={
            "general": "http://agent-general:9001",
            "medical_phi": "http://agent-phi:9001",
        }
    )
    # no __default__ → first value in dict
    assert c._resolve_agent_url("unknown") == "http://agent-general:9001"


def test_env_json_map():
    env = {"PRISMCLAW_RUNTIME_AGENT_URLS": '{"general":"http://g:9001","medical_phi":"http://p:9001"}'}
    with patch.dict(os.environ, env, clear=True):
        c = WorkspaceRuntimeClient()
    assert c._resolve_agent_url("medical_phi") == "http://p:9001"
    assert c._resolve_agent_url("general") == "http://g:9001"


@pytest.mark.asyncio
async def test_post_routes_to_kind_url_and_sets_header():
    c = WorkspaceRuntimeClient(
        agent_urls={
            "general": "http://agent-general:9001",
            "medical_phi": "http://agent-phi:9001",
        }
    )
    fake_client = MagicMock()
    fake_client.is_closed = False
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"ok": True})
    fake_client.post = AsyncMock(return_value=fake_resp)
    c._clients["http://agent-phi:9001"] = fake_client

    result = await c._post("/bash", {"command": "ls"}, template_id="medical_phi")

    assert result == {"ok": True}
    fake_client.post.assert_awaited_once()
    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"]["X-Template-ID"] == "medical_phi"


@pytest.mark.asyncio
async def test_post_uses_call_level_workspace_id_over_instance():
    c = WorkspaceRuntimeClient(
        agent_urls={"__default__": "http://agent:9001"},
        workspace_id="ws-instance",
    )
    fake_client = MagicMock()
    fake_client.is_closed = False
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"ok": True})
    fake_client.post = AsyncMock(return_value=fake_resp)
    c._clients["http://agent:9001"] = fake_client

    # call-level workspace_id 优先于实例级
    await c._post("/bash", {"command": "ls"}, template_id="general", workspace_id="ws-call")
    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"]["X-Workspace-ID"] == "ws-call"

    # 未传 call-level 时回退到实例级
    await c._post("/bash", {"command": "ls"}, template_id="general")
    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"]["X-Workspace-ID"] == "ws-instance"
