"""Step 0-1 最小 conftest: 隔离 PRISMCLAW_ROOT 为临时目录, 避免污染真实工作区。"""
from __future__ import annotations

import sys
import shutil
from pathlib import Path

import pytest

# 保证 backend/src/ 在 import 路径首（三目录重构后源码位于 backend/src/）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolate_prismclaw_root(tmp_path_factory, monkeypatch):
    """每测试一个用例就新建独立 <tmp_root>/.prismclaw/, 防止串数据。"""
    tmp = tmp_path_factory.mktemp("pc_iso_")
    (tmp / ".prismclaw").mkdir(parents=True, exist_ok=True)
    # 只在没有显式指定时注入; 用户用例需要真实 project_root 时可自行用自己的 workspace_path 覆盖
    monkeypatch.setenv("PRISMCLAW_ROOT", str(tmp))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
