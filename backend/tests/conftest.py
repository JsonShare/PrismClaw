"""Step 0-1 最小 conftest: 隔离 PRISMCLAW_ROOT 为临时目录, 避免污染真实工作区。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# 保证 backend/src/ 在 import 路径首（三目录重构后源码位于 backend/src/）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def reset_memory_selector_cooldown():
    """memory_selector 的超时冷却带全局状态，逐用例复位，避免测试相互影响。"""
    from prismclaw.engine.compression.memory_selector import reset_selector_cooldown

    reset_selector_cooldown()
    yield
    reset_selector_cooldown()


@pytest.fixture(autouse=True)
def isolate_prismclaw_root(tmp_path_factory, monkeypatch):
    """每测试一个用例就新建独立 <tmp_root>/.prismclaw/, 防止串数据。

    ``PRISMCLAW_ROOT`` 只锚定项目根，**不覆盖** ``settings.workspaces.default``
    （后者来自 server.yaml 的 ``~/.prismclaw/workspaces``）。因此这里必须
    同时把 settings 缓存替换成指向临时目录的一份，否则任何走
    ``resolve_user_root()`` 的用例都会 mkdir/写盘到真实 home。
    """
    tmp = tmp_path_factory.mktemp("pc_iso_")
    (tmp / ".prismclaw").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISMCLAW_ROOT", str(tmp))

    from prismclaw.infra.settings import _helpers as _settings_helpers

    real = _settings_helpers.load_settings(force_reload=True)
    isolated = real.model_copy(deep=True)
    isolated.workspaces.default = str(tmp / ".prismclaw" / "workspaces")
    # 缓存变量住在 _helpers 模块内，必须 patch 该模块的全局名。
    monkeypatch.setattr(_settings_helpers, "_cached_settings", isolated)
    try:
        yield tmp
    finally:
        _settings_helpers._cached_settings = None
        shutil.rmtree(tmp, ignore_errors=True)
