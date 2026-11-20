"""插件清单路径常量（零依赖叶子模块）。

所有加载 / 安装 / 发现 / 方言分析路径统一引用该常量，不再各自拼字符串。
刻意放在 ``plugins`` 包根目录而非 ``manifest`` 子包：``manifest/manifest.py``
依赖 ``registry.identity``，会拉起 registry → lifecycle → loading 的整条导入链，
而链尾的加载模块又要读这两个常量，构成循环导入。本模块只依赖 pathlib。
"""

from __future__ import annotations

from pathlib import Path

# 市场级与插件级清单同名，以 ``plugins`` 数组区分。
MANIFEST_REL_PATH = Path(".plugin") / "plugin.json"

__all__ = ["MANIFEST_REL_PATH"]
