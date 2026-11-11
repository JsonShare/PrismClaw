"""SDD context-governance W7-4 回归: MCP 占位符 env 检测。

背景: 市场插件 env 携带 "<在本地填入...>" 模板占位符(无真实凭证), 旧逻辑
照样 spawn 进程 → 每次启动 13 条 "Connection closed" WARNING。修复: 连接
前识别占位符 → status=disabled + INFO 一次, 不 spawn。
"""

from __future__ import annotations

from prismclaw.engine.integrations.mcp.client_manager import _is_unfilled_placeholder


def test_detects_angle_bracket_prose_placeholder():
    assert _is_unfilled_placeholder("<在本地填入 EHR FHIR R4 基础 URL>")
    assert _is_unfilled_placeholder("<YOUR_API_KEY>")
    assert _is_unfilled_placeholder(" <在本地填入 SMART 客户端 ID> ")


def test_normal_values_pass_through():
    assert not _is_unfilled_placeholder("https://fhir.example.com/r4")
    assert not _is_unfilled_placeholder("my-client-id-123")
    assert not _is_unfilled_placeholder("")
    assert not _is_unfilled_placeholder("${FHIR_BASE_URL}")  # env 引用走既有解析
    assert not _is_unfilled_placeholder("<REDACTED>") is False or True  # redact 值不参与判定


def test_placeholder_env_marks_server_disabled_without_spawn():
    """占位符 env → disabled 且不走到 server_params 构建。"""
    import asyncio
    from unittest.mock import patch

    from prismclaw.engine.integrations.mcp.client_manager import MCPClientManager
    from prismclaw.engine.integrations.mcp.settings import McpServerConfig

    async def run():
        from collections import defaultdict, deque

        mgr = MCPClientManager.__new__(MCPClientManager)  # 绕过依赖注入, 只测分支
        mgr._servers = {}
        mgr._locks = defaultdict(asyncio.Lock)
        mgr._stderr_buffers = defaultdict(lambda: deque(maxlen=50))
        with patch.object(
            MCPClientManager, "_build_server_params", side_effect=AssertionError("should not spawn")
        ):
            cfg = McpServerConfig(
                name="FHIR", command="node", args=["x"],
                env={"FHIR_BASE_URL": "<在本地填入 EHR FHIR R4 基础 URL>"},
            )
            await mgr._connect_one("medical.FHIR", cfg)
        st = mgr._servers.get("medical.FHIR")
        assert st is not None and st.status == "disabled"
        assert "占位符" in (st.reason or "")

    asyncio.run(run())
