"""RuntimeExecutor 的路径授权边界。

锁定的不变量（与部署拓扑无关，dev / 容器内一致）：

  1. 任何文件读写都只能落在**请求的 workspace_root** 之内；
     ``web_alice/default`` 的会话不能读写 ``web_bob/default`` 的任何文件——
     无论用相对 ``..``、绝对路径还是 ``~`` 展开；
  2. 运行时只读根只放行 ``~/.prismclaw/assets``（静态能力），
     不得放行整个 ``~/.prismclaw``（其中含 ``workspaces/`` 与凭据目录）；
  3. 只读根对写操作不生效——写入 ``assets/`` 必须被拒绝；
  4. 请求级 ``workspace_root`` 必须是 executor 自身根的严格子路径，
     否则一个共享 agent 会被单个请求扩权到宿主任意目录。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prismclaw.engine.runtime.agent.executor import RuntimeExecutor


@pytest.fixture()
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """构造 alice/bob 两个用户 + assets 静态能力目录。"""
    home = tmp_path / "home"
    pc = home / ".prismclaw"
    assets = pc / "assets"
    alice = pc / "workspaces" / "web_alice" / "default"
    bob = pc / "workspaces" / "web_bob" / "default"
    for d in (assets / "skills" / "demo", alice / "outputs", bob):
        d.mkdir(parents=True)
    (assets / "skills" / "demo" / "SKILL.md").write_text("capability", encoding="utf-8")
    (bob / "secret.txt").write_text("bob-secret", encoding="utf-8")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # ``Path.expanduser`` 读的是 ``HOME`` 环境变量，不是 ``Path.home``，两者都要指过去
    monkeypatch.setenv("HOME", str(home))
    # 让 resolve_project_root 落到 tmp，避免把仓库的 configs/src 加进只读根
    monkeypatch.setenv("PRISMCLAW_ROOT", str(tmp_path))
    return {"home": home, "pc": pc, "assets": assets, "alice": alice, "bob": bob}


def _executor(layout: dict[str, Path]) -> RuntimeExecutor:
    return RuntimeExecutor(workspace_root=str(layout["alice"]))


# ── 读：跨用户必须拒绝 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_denies_cross_user_relative(layout: dict[str, Path]):
    out = await _executor(layout).read_text("../../web_bob/default/secret.txt")
    assert out["success"] is False
    assert "outside" in out["error"]


@pytest.mark.asyncio
async def test_read_denies_cross_user_absolute(layout: dict[str, Path]):
    out = await _executor(layout).read_text(str(layout["bob"] / "secret.txt"))
    assert out["success"] is False
    assert "outside" in out["error"]


@pytest.mark.asyncio
async def test_read_denies_cross_user_tilde(layout: dict[str, Path]):
    out = await _executor(layout).read_text(
        "~/.prismclaw/workspaces/web_bob/default/secret.txt"
    )
    assert out["success"] is False
    assert "outside" in out["error"]


@pytest.mark.asyncio
async def test_read_allows_own_workspace(layout: dict[str, Path]):
    (layout["alice"] / "outputs" / "a.txt").write_text("mine", encoding="utf-8")
    out = await _executor(layout).read_text("outputs/a.txt")
    assert out["success"] is True
    assert out["content"] == "mine"


@pytest.mark.asyncio
async def test_read_allows_static_capability(layout: dict[str, Path]):
    out = await _executor(layout).read_text(
        "~/.prismclaw/assets/skills/demo/SKILL.md"
    )
    assert out["success"] is True
    assert out["content"] == "capability"


# ── 写：跨用户与只读根都必须拒绝 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_write_denies_cross_user(layout: dict[str, Path]):
    out = await _executor(layout).write_text(
        "../../web_bob/default/pwn.txt", "pwn"
    )
    assert out["success"] is False
    assert not (layout["bob"] / "pwn.txt").exists()


@pytest.mark.asyncio
async def test_write_denies_readonly_assets(layout: dict[str, Path]):
    target = layout["assets"] / "skills" / "demo" / "SKILL.md"
    out = await _executor(layout).write_text(str(target), "HACKED")
    assert out["success"] is False
    assert target.read_text(encoding="utf-8") == "capability"


@pytest.mark.asyncio
async def test_write_stream_denies_cross_user(layout: dict[str, Path]):
    out = await _executor(layout).write_stream_begin(
        "../../web_bob/default/stream.txt", "text"
    )
    assert out["success"] is False
    assert not (layout["bob"] / "stream.txt").exists()


# ── 结构化检索：跨用户必须拒绝 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fs_list_denies_cross_user(layout: dict[str, Path]):
    out = await _executor(layout).fs_list("../../web_bob/default")
    assert out.get("entries") == []
    assert "outside" in out.get("error", "")


@pytest.mark.asyncio
async def test_fs_grep_denies_cross_user(layout: dict[str, Path]):
    out = await _executor(layout).fs_grep("bob-secret", "../../web_bob/default")
    assert out.get("matches") == []
    assert "outside" in out.get("error", "")


# ── 请求级 workspace_root 不得扩权 ────────────────────────────────────


@pytest.mark.asyncio
async def test_request_root_must_be_within_executor_root(layout: dict[str, Path]):
    """共享 agent（根=/workspace）只允许请求收窄到子目录，不允许跑出根外。"""
    exe = RuntimeExecutor(workspace_root=str(layout["pc"] / "workspaces"))
    out = await exe.read_text(str(layout["bob"] / "secret.txt"), workspace_root="/")
    assert out["success"] is False
    assert "workspace_root" in out["error"]


@pytest.mark.asyncio
async def test_request_root_narrows_read_scope(layout: dict[str, Path]):
    """共享 agent 下，请求带 alice 的根时 bob 的文件不可读。"""
    exe = RuntimeExecutor(workspace_root=str(layout["pc"] / "workspaces"))
    denied = await exe.read_text(
        "../../web_bob/default/secret.txt", workspace_root=str(layout["alice"])
    )
    assert denied["success"] is False

    (layout["alice"] / "outputs" / "a.txt").write_text("mine", encoding="utf-8")
    ok = await exe.read_text("outputs/a.txt", workspace_root=str(layout["alice"]))
    assert ok["success"] is True
    assert ok["content"] == "mine"


# ── bash：请求级根同样不得扩权 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_bash_rejects_root_outside_executor_root(layout: dict[str, Path]):
    exe = RuntimeExecutor(workspace_root=str(layout["alice"]))
    out = await exe.bash("true", workspace_root="/")
    assert out["exit_code"] == -1
    assert "workspace_root" in out["stderr"]
