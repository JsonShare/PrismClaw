"""Manifest 解析与校验的纯函数。

从 load_strategy.py 拆分而来，包含清单解析等无副作用的工具函数。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from prismclaw.engine.experts.manifest import (
    ExpertManifest,
    parse_manifest,
)
from prismclaw.engine.experts.manifest.expert_schema import validate_expert_package
from prismclaw.engine.experts.paths import MANIFEST_REL_PATH
from prismclaw.engine.experts.registry.identity import EXPERT_SOURCE

logger = logging.getLogger(__name__)


def _coerce_string_list(raw: object) -> list[str] | None:
    """通用列表归一:非 list/None 返回 None;仅保留非空字符串 strip 后的元素。

    与 skills/parser._coerce_string_list 行为完全对齐,放在此处避免跨模块引
    （parser 引会拉起技能解析大链）。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    values = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return values or None


def _load_json_file(path: Path) -> object:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_canonical_manifest(
    install_path: Path, *, source: str | None = None
) -> ExpertManifest:
    """定位并加载 canonical ``.plugin/plugin.json``，立即解析为 pydantic 模型。"""
    manifest_json_path = install_path / MANIFEST_REL_PATH
    raw = _load_json_file(manifest_json_path)
    if not isinstance(raw, dict):
        raise ValueError("plugin.json must be a JSON object")
    if source == EXPERT_SOURCE:
        validate_expert_package(install_path, raw)
    return parse_manifest(raw)


def _resolve_relative_paths(base: Path, raw: object) -> list[Path]:
    """解析 manifest 中相对路径字段。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [base / raw]
    if isinstance(raw, list):
        return [base / item for item in raw if isinstance(item, str)]
    return []



