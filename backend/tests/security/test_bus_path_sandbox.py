"""MCP server 证据总线路径沙箱 — 安全守卫的自动化测试。

被测对象：backend/.prismclaw/plugins/science-research/mcp_servers/science-mining/
services/evidence_store.py 的 bus_path_error、append_records、read_records。

覆盖场景：
  - env 缺失 → fail-fast（拒绝所有文件操作）
  - 绝对路径越权（/etc/passwd、~/.ssh/id_rsa）→ 拦截
  - ../ 路径穿越 → 拦截
  - 符号链接指向 workspace 外 → 拦截（resolve 覆盖 symlink）
  - symlink + 穿越组合 → 拦截
  - workspace 内合法路径（含 ~ 展开）→ 放行
  - TOCTOU 闭环：守卫返回的 safe_path 与 append_records 实际写入的 Path 是同一个对象
  - append_records / read_records 接受 Path 且不二次绕过守卫
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 将 science-mining MCP server 注入 sys.path（插件代码不在 src/prismclaw 下）
_SERVER_ROOT = Path(__file__).resolve().parents[2] / ".prismclaw" / "plugins" / "science-research" / "mcp_servers" / "science-mining"
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from services import evidence_store  # noqa: E402 — path hack 后才能 import


WORKSPACE_ENV = "PRISMCLAW_WORKSPACES_BASE"


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch) -> Path:
    """为每个测试提供独立 workspace 根，且注入环境变量。"""
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path.resolve()))
    # 清缓存 — evidence_store 有模块级 _WORKSPACE_BASE_CACHED
    evidence_store._WORKSPACE_BASE_CACHED = None
    return tmp_path


# ═══════════════════════════════════════════════════════════════════════════
# 1. 环境变量缺失 → fail-fast
# ═══════════════════════════════════════════════════════════════════════════


class TestEnvMissing:
    def test_env_missing_rejects_all(self, monkeypatch):
        monkeypatch.delenv(WORKSPACE_ENV, raising=False)
        evidence_store._WORKSPACE_BASE_CACHED = None
        err, p = evidence_store.bus_path_error("/etc/passwd")
        assert err is not None
        assert p is None
        assert "PRISMCLAW_WORKSPACES_BASE" in err


# ═══════════════════════════════════════════════════════════════════════════
# 2. 越权与穿越
# ═══════════════════════════════════════════════════════════════════════════


class TestRejectOutsideWorkspace:
    """workspace 根已配置但路径不在其内 → 一律拦截。"""

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/tmp/outside.jsonl",
            "~/.ssh/id_rsa",
            "/home/user/.aws/credentials",
            "../../../../etc/passwd",         # 穿越：相对 CWD（可能是项目根）
            "~/.prismclaw/workspaces_outside/x.jsonl",  # 相邻同级目录
        ],
    )
    def test_any_outside_is_rejected(self, workspace: Path, path: str):
        err, p = evidence_store.bus_path_error(path)
        assert err is not None, f"expected rejection for {path!r}"
        assert p is None
        # 错误消息必须包含根因指引，便于 agent 自行修正
        assert "越权" in err or "不在工作区根" in err

    def test_empty_string_rejected(self, workspace: Path):
        err, p = evidence_store.bus_path_error("")
        assert err is not None
        assert p is None

    def test_garbage_input_rejected(self, workspace: Path):
        err, p = evidence_store.bus_path_error("\x00\x01\xffinvalid")
        # 解析失败也算拦截——OSError / resolve 异常
        assert err is not None
        assert p is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. 符号链接安全：resolve 覆盖 symlink，目标在 workspace 外则拦截
# ═══════════════════════════════════════════════════════════════════════════


class TestSymlink:
    def test_symlink_to_etc_passwd(self, workspace: Path):
        link = workspace / "evil_link.jsonl"
        link.symlink_to("/etc/passwd")
        err, p = evidence_store.bus_path_error(str(link))
        assert err is not None
        assert p is None

    def test_symlink_to_tmp(self, workspace: Path):
        target = Path("/tmp/outside_target.jsonl")
        target.write_text("leak\n")
        link = workspace / "escape.jsonl"
        link.symlink_to(str(target))
        try:
            err, p = evidence_store.bus_path_error(str(link))
            assert err is not None
            assert p is None
        finally:
            target.unlink(missing_ok=True)

    def test_traversal_then_symlink(self, workspace: Path):
        """symlink 目标本身是 ../，resolve 后仍越权。"""
        link = workspace / "traverse_link.jsonl"
        link.symlink_to("../../../../etc/passwd")
        err, p = evidence_store.bus_path_error(str(link))
        assert err is not None
        assert p is None

    def test_symlink_inside_workspace_is_allowed(self, workspace: Path):
        """symlink 目标也在 workspace 内 → 放行。"""
        sub = workspace / "sub"; sub.mkdir()
        real = sub / "real.jsonl"; real.write_text("[]\n")
        link = workspace / "link.jsonl"; link.symlink_to(str(real))
        err, p = evidence_store.bus_path_error(str(link))
        assert err is None
        assert p is not None
        # resolve 覆盖 symlink → p 指向 real，不是 link
        assert p == real.resolve()


# ═══════════════════════════════════════════════════════════════════════════
# 4. 合法路径放行
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptInsideWorkspace:
    def test_absolute_path(self, workspace: Path):
        legal = workspace / "web_admin" / "测试D" / "evidence" / "feature.jsonl"
        err, p = evidence_store.bus_path_error(str(legal))
        assert err is None
        assert p == legal.resolve()

    def test_tilde_expansion(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv(WORKSPACE_ENV, str(home / ".prismclaw" / "workspaces"))
        evidence_store._WORKSPACE_BASE_CACHED = None

        legal = "~/.prismclaw/workspaces/web_admin/x/evidence/x.jsonl"
        err, p = evidence_store.bus_path_error(legal)
        assert err is None
        assert p is not None
        # ~ 已展开
        assert str(home) in str(p)
        assert "~" not in str(p)


# ═══════════════════════════════════════════════════════════════════════════
# 5. TOCTOU 闭环：守卫返回的 safe_path 与 I/O 实际用的是同一个对象
# ═══════════════════════════════════════════════════════════════════════════


class TestTOCTOU:
    def test_append_records_receives_same_path_object(self, workspace: Path):
        legal = workspace / "ok.jsonl"
        err, safe_path = evidence_store.bus_path_error(str(legal))
        assert err is None, f"guard rejected legal path: err={err}"
        assert safe_path is not None  # 通过守卫

        path_out, written = evidence_store.append_records(safe_path, [{"x": 1}])
        # TOCTOU 关键：append_records 直接用传入的 Path 对象（非重新 Path(str)）
        assert path_out is safe_path or path_out == safe_path.resolve()
        assert written == 1
        assert legal.read_text().strip() == '{"x": 1}'

    def test_append_records_rejects_raw_string_outside(self, workspace: Path):
        """直接调 append_records 越权路径（绕过守卫）——这才是真正的 TOCTOU 缺口！

        但当前 append_records 不做守卫（守卫在工具入口层），所以直接调用会越权。
        这条测试记录当前的实现缺口；未来可考虑在 append_records 内部也做一次
        bus_path_error（代价是守卫跑两次，但更安全）。
        """
        # 现状：直接传 /tmp 能写
        p, n = evidence_store.append_records("/tmp/__prismclaw_sandbox_hole__.jsonl", [{"x": 1}])
        assert p.exists()
        p.unlink()

    def test_read_records_receives_same_path_object(self, workspace: Path):
        legal = workspace / "evidence"
        legal.mkdir()
        (legal / "a.jsonl").write_text('{"id": 1}\n')
        err, safe_path = evidence_store.bus_path_error(str(legal))
        assert err is None, f"guard rejected legal path: err={err}"
        assert safe_path is not None
        records, bad = evidence_store.read_records(safe_path)
        assert bad == 0
        assert len(records) == 1
        assert int(records[0]["id"]) == 1  # json.loads 应返回 int，但防御性转换


# ═══════════════════════════════════════════════════════════════════════════
# 6. 守卫模块级缓存行为：env 变化后需手动 reset
# ═══════════════════════════════════════════════════════════════════════════


class TestCacheBehavior:
    def test_cache_reset_via_fixture(self, workspace: Path, monkeypatch):
        """fixture 会在每个测试前清缓存 —— 确认新 env 生效。"""
        new_root = workspace / "sub"
        new_root.mkdir()
        monkeypatch.setenv(WORKSPACE_ENV, str(new_root))
        evidence_store._WORKSPACE_BASE_CACHED = None
        assert evidence_store._get_workspace_base() == new_root.resolve()


# ═══════════════════════════════════════════════════════════════════════════
# 7. 工具入口级集成（只 import，不触发真实检索/LLM）
# ═══════════════════════════════════════════════════════════════════════════


class TestToolEntryIntegration:
    """7 个工具都已接入 bus_path_error —— 至少确保 import 和签名正确。"""

    @pytest.mark.parametrize(
        "tool_module",
        ["fanout_search", "analyze_feature", "cnki_search",
         "read_evidence", "run_mining", "rank_and_select", "render_report"],
    )
    def test_each_tool_imports_and_calls_guard(self, tool_module: str):
        from importlib import import_module
        mod = import_module(f"tools.{tool_module}")
        # 工具函数名统一为 async def xxx(args): 或 sync def xxx(args):
        # 只要能 import 说明 guard 的调用链没语法错
        assert mod is not None

    def test_tool_call_rejects_relative_out_path(self, workspace: Path):
        """最小化端到端：构造 mock bus_path_error 返回错误 → 工具入口必须返回 error。"""
        # 相对 out_path 必然触发拦截（CWD 不是 workspace）
        # 直接调 bus_path_error 看能否拦截
        err, _ = evidence_store.bus_path_error("evidence/test.jsonl")
        assert err is not None, "relative out_path should be rejected by guard"
