"""LocalSink 的落点与返回值契约。

``commit()`` 返回的路径以 ``workspace_root`` 为基准（前端与附件解析都按它
拼接），而 ``begin()`` 的安全边界也是这个根。两者必须同源——历史上
``begin()`` 用未 resolve 的路径、``commit()`` 又拼了 ``resolve()``，
在 macOS（``/var`` 是 ``/private/var`` 的符号链接）上 commit 必然抛
``ValueError``，而文件其实已经落盘，形成"写成功但报失败"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismclaw.engine.toolkit.file.upload_pipeline import InvalidPathError, LocalSink


@pytest.mark.asyncio
async def test_commit_returns_path_relative_to_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "web_alice" / "default"
    dest = ws / "sessions" / "s1" / "uploads"
    dest.mkdir(parents=True)

    sink = LocalSink(ws, dest)
    await sink.begin("u1", "a.txt", 3)
    await sink.write_chunk("u1", 1, b"abc")
    result = await sink.commit("u1")

    assert result["path"] == "sessions/s1/uploads/a.txt"
    assert result["size"] == 3
    assert (dest / "a.txt").read_bytes() == b"abc"


@pytest.mark.asyncio
async def test_commit_survives_symlinked_tmp_path(tmp_path: Path) -> None:
    """macOS 上 tmp_path 常在 /var 下（符号链接到 /private/var）。

    begin/commit 必须用同一套 resolve 语义，否则就是上面那个崩溃。
    这里显式用 resolve 前的路径构造，复现真实调用方的行为。
    """
    ws = tmp_path / "web_alice" / "default"
    (ws / "uploads").mkdir(parents=True)
    unresolved_ws = Path(str(ws))  # 保持未 resolve 的写法
    unresolved_dest = Path(str(ws / "uploads"))

    sink = LocalSink(unresolved_ws, unresolved_dest)
    await sink.begin("u1", "b.txt", 3)
    await sink.write_chunk("u1", 1, b"xyz")
    assert (await sink.commit("u1"))["path"] == "uploads/b.txt"


def test_dest_root_outside_workspace_rejected(tmp_path: Path) -> None:
    """biz_domain 与工作区同级，不在 workspace_root 内——构造期就该拒绝。"""
    user_root = tmp_path / "web_alice"
    ws = user_root / "default"
    biz_inbox = user_root / "biz_domain" / "medical" / "inbox" / "r1"
    ws.mkdir(parents=True)
    biz_inbox.mkdir(parents=True)

    with pytest.raises(InvalidPathError, match="不在工作区"):
        LocalSink(ws, biz_inbox)


@pytest.mark.asyncio
async def test_begin_neutralizes_traversal(tmp_path: Path) -> None:
    """``..`` 被剥离而非报错：文件必须仍落在 dest_root 之内。"""
    ws = tmp_path / "web_alice" / "default"
    dest = ws / "uploads"
    dest.mkdir(parents=True)
    sink = LocalSink(ws, dest)

    await sink.begin("u1", "../escape.txt", 3)
    await sink.write_chunk("u1", 1, b"abc")
    result = await sink.commit("u1")

    written = (ws / result["path"]).resolve()
    written.relative_to(dest.resolve())  # 仍在 dest_root 内
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
async def test_duplicate_name_does_not_overwrite(tmp_path: Path) -> None:
    ws = tmp_path / "web_alice" / "default"
    dest = ws / "uploads"
    dest.mkdir(parents=True)

    sink = LocalSink(ws, dest)
    for idx, payload in ((1, b"first"), (2, b"second")):
        await sink.begin(f"u{idx}", "same.txt", len(payload))
        await sink.write_chunk(f"u{idx}", 1, payload)
        await sink.commit(f"u{idx}")

    files = sorted(p.name for p in dest.iterdir())
    assert len(files) == 2
    assert "same.txt" in files
