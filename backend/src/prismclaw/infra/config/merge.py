"""Deep merge 算法 + 版本守卫 + 工具函数。

与 _helpers.py 中现有的 _deep_merge 的区别：
  1. DELETE 哨兵语义 — "__DELETE__" 删除键，区分"禁用"和"覆盖为 false"
  2. 严格类型匹配 — 类型不匹配跳过 + warning，防止 timeout:30 被 "30" 覆盖
  3. schema_version 不参与合并 — 它是版本守卫的输入，不是配置值
  4. _filter_stale — 过期键自动剥离 + 告警

现有 _deep_merge 不动（服务于核心引擎的模块配置加载），本模块是域配置的增强版。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# DELETE 哨兵：JSON 原生可序列化的字符串标记
# 区分"禁用某源"（override = false）和"删除该键"（override = "__DELETE__"）
DELETE_SENTINEL = "__DELETE__"


# ---------------------------------------------------------------------------
# 路径安全
# ---------------------------------------------------------------------------

_DOMAIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_domain_name(domain: str) -> None:
    """防止路径遍历攻击：域名只允许小写字母/数字/下划线。"""
    if not _DOMAIN_NAME_RE.match(domain):
        raise ValueError(f"Invalid domain name: {domain!r}")


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------

def deep_merge(
    base: dict[str, Any],
    override: dict[str, Any],
    *,
    path: str = "",
) -> dict[str, Any]:
    """递归合并 override 到 base 的副本。

    规则：
      - schema_version 不参与合并
      - DELETE_SENTINEL → 从 result 中删除该键
      - 双方都是 dict → 递归
      - 类型一致（type() 严格匹配）→ 覆盖
      - 类型不一致 → 跳过 + warning
      - 键不在 base 中 → 允许新增（为未来版本预留）
    """
    result = dict(base)
    for key, oval in override.items():
        if key == "schema_version":
            continue  # schema_version 是版本守卫输入，不是配置值
        npath = f"{path}.{key}" if path else key

        # DELETE 哨兵
        if oval == DELETE_SENTINEL:
            result.pop(key, None)
            continue

        if key in result:
            bval = result[key]
            if isinstance(bval, dict) and isinstance(oval, dict):
                result[key] = deep_merge(bval, oval, path=npath)
            elif type(bval) is type(oval):  # noqa: E721 — 严格类型匹配
                result[key] = oval
            else:
                logger.warning(
                    "Config type mismatch at %s: base=%s, override=%s — skipping",
                    npath,
                    type(bval).__name__,
                    type(oval).__name__,
                )
        else:
            # 键不在 base → 允许新增（为未来版本预留）
            result[key] = oval
    return result


# ---------------------------------------------------------------------------
# 版本守卫
# ---------------------------------------------------------------------------

def filter_stale(
    override: dict[str, Any],
    defaults: dict[str, Any],
    *,
    path: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """过滤 override 中 defaults 不存在的键和类型不匹配的键。

    Returns:
        (cleaned_override, stale_keys) — 清理后的覆盖 + 被剔除的键路径列表
    """
    stale: list[str] = []
    cleaned: dict[str, Any] = {}
    for key, val in override.items():
        if key == "schema_version":
            cleaned[key] = val
            continue
        npath = f"{path}.{key}" if path else key
        if key not in defaults:
            stale.append(npath)
            continue
        if isinstance(val, dict) and isinstance(defaults[key], dict):
            sub_cleaned, sub_stale = filter_stale(val, defaults[key], path=npath)
            if sub_cleaned:  # 不写空字典
                cleaned[key] = sub_cleaned
            stale.extend(sub_stale)
        elif type(val) is type(defaults[key]):  # noqa: E721
            cleaned[key] = val
        else:
            stale.append(npath)
    return cleaned, stale


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def clean_empty(d: dict[str, Any]) -> dict[str, Any]:
    """递归删除空字典（deep_merge 后可能残留的空壳）。"""
    result = {}
    for key, val in d.items():
        if isinstance(val, dict):
            val = clean_empty(val)
            if val:  # 空字典不保留
                result[key] = val
        else:
            result[key] = val
    return result


def is_only_schema(d: dict[str, Any]) -> bool:
    """检查字典是否只剩 schema_version（无实际覆盖内容）。"""
    return len(d) == 1 and "schema_version" in d
