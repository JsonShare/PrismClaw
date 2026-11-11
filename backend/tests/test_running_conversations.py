"""运行中会话列表（GET /conversations/running）权威源单测。

事故背景（2026-10-26 11:48）：tech-mining 长任务运行于工作区「测试A」的会话，
用户刷新页面后前端以 workspace_id=default 查询 running 列表（activeWorkspaceId
刷新后未恢复），该会话被工作区过滤漏掉 → GET /run 未被调用 → 前端不水合 →
「任务从 UI 消失」。修复后 running 列表改为用户级内存权威源
（SessionQueue.list_running_conversations），不再依赖工作区参数。

事故背景（2026-11-09 根因）：ConversationWebRuntimeProjection.is_active() 只看
四字段（loading_active / 非终态子代理 / 待审批 / running 工具），chat_runner 在
TextChunk/ReasoningChunk 置 loading_active=False → LLM 流式生成阶段四字段全空
→ 运行中 run 被误判不活跃 → 漏列 + /run 返回 null → 前端水合断链。重构后
判活入口 is_conversation_running = 投影 is_active() OR RunControl.is_active()
（rc.active 是「run 进行中」的权威生命周期信号）；GET /run 改为内存直读
（树条目仅审计留档，不再参与判活与构建）。

覆盖：
1. 空投影不判活
2. 运行中工具调用（无子代理）判活——长工具场景刷新可见性
3. finish_web_runtime 收尾：running 工具翻 error → 不再判活（防 forever-running）
4. 非终态子代理判活；finish 清空后不再判活
5. 路由层用户前缀过滤：他人会话不泄漏进本用户列表
6. GET /run 内存交叉校验：树里是 active 但内存无活跃投影 → 返回 null
   （纵深防御，防止陈旧 active 条目被 hydrateRun 重水合成「第二段执行过程」）
7. RunControl 权威判活：投影细节 idle（流式生成阶段形态）但 rc.active=True
   → 判活；run 结束（rc.active=False + finish）→ 不判活；/run 内存直读时
   投影实时正文胜出树内旧值
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prismclaw.app.web.chat.chat_protocol import SessionQueue
from prismclaw.app.web.routes import (
    get_conversation_run,
    get_running_conversations,
)
from prismclaw.schema.session.types import (
    RUN_PROJECTION_CUSTOM_TYPE,
    CustomEntry,
    SessionHeader,
    SessionTree,
    generate_entry_id,
)


def _run(coro):
    return asyncio.run(coro)


def _add_running_tool(sq: SessionQueue, cid: str, name: str = "sub_agent") -> None:
    proj = sq.get_or_create_web_runtime_projection(cid)
    proj.tool_calls.append({"id": "tc1", "name": name, "status": "running"})


# ---------------------------------------------------------------------------
# 1. 空投影不判活
# ---------------------------------------------------------------------------

def test_empty_projection_inactive():
    sq = SessionQueue()
    cid = "web:admin:s:aaa"
    sq.get_or_create_web_runtime_projection(cid)
    assert cid not in sq.list_running_conversations()


# ---------------------------------------------------------------------------
# 2. 运行中工具调用判活（无子代理的长工具场景）
# ---------------------------------------------------------------------------

def test_running_tool_call_activates():
    sq = SessionQueue()
    cid = "web:admin:s:bbb"
    _add_running_tool(sq, cid)
    assert cid in sq.list_running_conversations()


def test_done_tool_call_does_not_activate():
    sq = SessionQueue()
    cid = "web:admin:s:bbb2"
    proj = sq.get_or_create_web_runtime_projection(cid)
    proj.tool_calls.append({"id": "tc1", "name": "bash", "status": "done"})
    assert cid not in sq.list_running_conversations()


# ---------------------------------------------------------------------------
# 3. finish_web_runtime 收尾 running 工具 → 不再判活
# ---------------------------------------------------------------------------

def test_finish_flips_running_tool_to_error():
    sq = SessionQueue()
    cid = "web:admin:s:ccc"
    _add_running_tool(sq, cid)
    assert cid in sq.list_running_conversations()

    sq.finish_web_runtime(cid)
    proj = sq.get_web_runtime_projection(cid)
    # 收尾后投影应已回收（inactive 即 pop）；即便未被回收（如仍有审批项），
    # running 工具也必须翻成 error 终态。
    if proj is not None:
        assert all(tc.get("status") != "running" for tc in proj.tool_calls)
        assert all(
            tc.get("status") == "error"
            for tc in proj.tool_calls
            if tc.get("name") == "sub_agent"
        )
    assert cid not in sq.list_running_conversations()


# ---------------------------------------------------------------------------
# 4. 非终态子代理判活；finish 清空后不再判活
# ---------------------------------------------------------------------------

def test_subagent_item_activates_and_finish_clears():
    sq = SessionQueue()
    cid = "web:admin:s:ddd"
    proj = sq.get_or_create_web_runtime_projection(cid)
    proj.subagent_items["sub-1"] = {"partial_status": "tool_calling"}
    assert cid in sq.list_running_conversations()

    sq.finish_web_runtime(cid)
    assert cid not in sq.list_running_conversations()


# ---------------------------------------------------------------------------
# 5. 路由层用户前缀过滤
# ---------------------------------------------------------------------------

def _fake_request(sq: SessionQueue) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(web_deps=SimpleNamespace(session_queue=sq))
        )
    )


def test_route_filters_by_user_prefix():
    async def run():
        sq = SessionQueue()
        mine = "web:admin:s:eee"
        other = "web:bob:s:fff"
        _add_running_tool(sq, mine)
        _add_running_tool(sq, other)

        res = await get_running_conversations(_fake_request(sq), user_id="admin")
        assert res == {"conversations": [mine]}

    _run(run())


def test_route_without_session_queue_returns_empty():
    async def run():
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(web_deps=SimpleNamespace(session_queue=None))
            )
        )
        res = await get_running_conversations(request, user_id="admin")
        assert res == {"conversations": []}

    _run(run())


# ---------------------------------------------------------------------------
# 6. GET /run 内存交叉校验（纵深防御）
# ---------------------------------------------------------------------------


def _make_active_run_tree(cid: str) -> SessionTree:
    """构造一棵带 active run_projection 条目的树（模拟「陈旧 active 快照」）。"""
    tree = SessionTree(header=SessionHeader(id="", workspace_id="", agent_id=""))
    entry = CustomEntry(
        id=generate_entry_id(set()),
        type="custom",
        custom_type=RUN_PROJECTION_CUSTOM_TYPE,
        data={
            "conversation_id": cid,
            "state": "active",
            "streaming_text": "执行中…",
            "timeline_nodes": [],
            "task_runtime": {},
            "phase": None,
            "phase_at": None,
            "clarification": None,
            "updated_at": 0,
        },
        parent_id=tree.leaf_id or "",
    )
    tree.append(entry)
    return tree


def _patch_verify(monkeypatch_tree):
    """把 routes._load_and_verify 替换成返回固定树，隔离对真实存储的依赖。"""
    import prismclaw.app.web.routes as routes_mod

    async def _fake_verify(*_args, **_kwargs):
        return monkeypatch_tree

    routes_mod._load_and_verify = _fake_verify


def test_get_run_null_when_no_live_projection():
    """树里是 active，但内存无活跃投影 → 必须返回 null。

    这正是「刷新后执行过程被分割成两段」的根因防线：run 已结束（内存投影被
    finish_web_runtime 回收）但树里残留 active 条目时，不能把陈旧快照误判为
    进行中、交给前端 hydrateRun 重建流式气泡。
    """

    async def run():
        from prismclaw.app.web import routes as routes_mod

        cid = "web:admin:s:getrun1"
        tree = _make_active_run_tree(cid)
        original = routes_mod._load_and_verify
        _patch_verify(tree)
        try:
            sq = SessionQueue()  # 无内存投影
            req = _fake_request(sq)
            res = await get_conversation_run(req, session_id=cid, workspace_id="default", user_id="admin")
            assert res == {"run": None}, res
        finally:
            routes_mod._load_and_verify = original

    _run(run())


def test_get_run_returns_when_live_projection_active():
    """内存投影确实活跃时 → 返回 run（不误杀进行中的会话）。"""

    async def run():
        from prismclaw.app.web import routes as routes_mod

        cid = "web:admin:s:getrun2"
        tree = _make_active_run_tree(cid)
        original = routes_mod._load_and_verify
        _patch_verify(tree)
        try:
            sq = SessionQueue()
            proj = sq.get_or_create_web_runtime_projection(cid)
            proj.loading_active = True  # 模拟 run 仍活跃
            req = _fake_request(sq)
            res = await get_conversation_run(req, session_id=cid, workspace_id="default", user_id="admin")
            assert res.get("run") is not None, res
            assert res["run"]["state"] == "active"
        finally:
            routes_mod._load_and_verify = original

    _run(run())


# ---------------------------------------------------------------------------
# 7. RunControl 权威判活（2026-11 重构回归）
# ---------------------------------------------------------------------------


def test_run_control_activates_when_projection_details_idle():
    """B1 根因回归：LLM 流式生成阶段投影四字段全空（is_active=False），
    但 RunControl 仍 active → 必须判活。

    这是「刷新后运行中任务消失」的第二代根因：chat_runner 在
    TextChunk/ReasoningChunk 置 loading_active=False，投影判活口径覆盖
    不到流式生成阶段。RunControl（rc.active，chat_runner 主 try 首语句
    置 True / finally 同步段最前翻 False）是「run 进行中」的权威生命
    周期信号——判活入口 OR 它之后，流式阶段刷新可见。
    """
    sq = SessionQueue()
    cid = "web:admin:s:rc1"
    # 投影存在但细节全空——LLM 流式生成阶段的真实形态
    proj = sq.get_or_create_web_runtime_projection(cid)
    assert not proj.is_active()

    rc = sq.get_run_control(cid)
    rc.active = True  # chat_runner「rc.active = True」的效果

    assert sq.is_conversation_running(cid) is True
    assert cid in sq.list_running_conversations()


def test_run_control_inactive_after_finish():
    """run 结束（finally：rc.active=False → finish_web_runtime pop 投影）
    → 不再判活。

    列表候选集是投影字典（_web_runtime）：run 必然有投影载体（chat_runner
    先建投影再置 rc.active），RunControl 是载体之上的生命周期信号——
    「无投影 + rc.active=True」在生产流中不可达，不构成列表漏列。
    """
    sq = SessionQueue()
    cid = "web:admin:s:rc2"
    # 与 chat_runner 生产顺序一致：先建投影，再置 rc.active
    sq.get_or_create_web_runtime_projection(cid)
    rc = sq.get_run_control(cid)
    rc.active = True
    assert cid in sq.list_running_conversations()

    # chat_runner finally 同步段的收尾序列：rc 先翻 False，再 pop 投影。
    # rc 存在但 inactive（is_active = active and not abort_event.is_set()
    # 的 False 分支）时不得判活。
    rc.active = False
    assert sq.is_conversation_running(cid) is False
    sq.finish_web_runtime(cid)
    assert cid not in sq.list_running_conversations()


def test_get_run_returns_memory_payload_when_run_control_active():
    """投影细节 idle 但 RunControl active → /run 返回内存构建的 payload，
    且投影实时正文（streaming_text）胜出树内旧值——内存直读语义。

    树里的 run_projection 条目重构后仅审计留档：streaming_text 等运行态
    字段只读内存投影（_build_run_projection_payload L462），树值不参与
    （tree 参数仍参与 task_runtime 构建，那是既有口径，非本次重构对象）。
    """

    async def run():
        from prismclaw.app.web import routes as routes_mod

        cid = "web:admin:s:rc3"
        # 树里 streaming_text="执行中…"（陈旧值）；内存投影是实时正文
        tree = _make_active_run_tree(cid)
        original = routes_mod._load_and_verify
        _patch_verify(tree)
        try:
            sq = SessionQueue()
            proj = sq.get_or_create_web_runtime_projection(cid)
            proj.streaming_text = "内存实时正文"
            rc = sq.get_run_control(cid)
            rc.active = True  # 判活唯一依据：RunControl
            req = _fake_request(sq)
            res = await get_conversation_run(req, session_id=cid, workspace_id="default", user_id="admin")
            assert res.get("run") is not None, res
            assert res["run"]["streaming_text"] == "内存实时正文"
        finally:
            routes_mod._load_and_verify = original

    _run(run())


def test_get_run_idle_short_circuits_without_tree_load():
    """判活前置：非运行中会话 GET /run 直接 null，不加载树。

    判活是纯内存操作（投影 OR RunControl）；历史/已结束/不存在的会话
    不必为返回 null 支付整棵树加载，也不以 404/403 泄漏会话存在性
    （前端对非 200 与 null 同为跳过）。
    """

    async def run():
        from prismclaw.app.web import routes as routes_mod

        cid = "web:admin:s:idle1"
        original = routes_mod._load_and_verify

        async def _must_not_load(*_args, **_kwargs):
            raise AssertionError("idle GET /run must not load the tree")

        routes_mod._load_and_verify = _must_not_load
        try:
            sq = SessionQueue()  # 无投影、无 RunControl → 非运行中
            req = _fake_request(sq)
            res = await get_conversation_run(req, session_id=cid, workspace_id="default", user_id="admin")
            assert res == {"run": None}, res
        finally:
            routes_mod._load_and_verify = original

    _run(run())


def test_get_run_bare_session_id_resolves_s_prefix_candidate():
    """裸 session_id（不带 web: 前缀）经 "s:" 回退候选命中内存判活。

    归一化链：base=web:{user}:{workspace} → 裸 id 拼为 {base}:{id}，
    判活候选补 {base}:s:{id}——运行中会话以裸 id 查询同样能水合。
    """

    async def run():
        from prismclaw.app.web import routes as routes_mod

        # 完整树 id 形态（含 workspace 段与 s: 段）
        cid = "web:admin:default:s:bare1"
        tree = _make_active_run_tree(cid)
        original = routes_mod._load_and_verify
        _patch_verify(tree)
        try:
            sq = SessionQueue()
            # 与生产顺序一致：先建投影（细节 idle——B1 流式生成形态），
            # 再置 rc.active；判活靠 RunControl 命中，payload 靠投影构建。
            sq.get_or_create_web_runtime_projection(cid)
            rc = sq.get_run_control(cid)
            rc.active = True
            req = _fake_request(sq)
            # 裸 id + 正确 workspace → 第二候选 {base}:s:{id} 命中
            res = await get_conversation_run(req, session_id="bare1", workspace_id="default", user_id="admin")
            assert res.get("run") is not None, res
        finally:
            routes_mod._load_and_verify = original

    _run(run())
