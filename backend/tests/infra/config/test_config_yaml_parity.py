"""配置体系 YAML 一致性测试。

覆盖三件事：
1. 8 个 YAML 合并后可被 Settings 完整校验，且关键键不被静默丢弃；
2. CONFIG_KEY_OWNERS（顶层键 → 归属文件）与真实 YAML 顶层键 1:1；
3. dump_document 写回后可重新加载且数据不变（ruamel 环境下同时验证注释保留）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"

_MODULE_FILES = (
    "infra.yaml",
    "models.yaml",
    "agent.yaml",
    "mcp.yaml",
    "capabilities.yaml",
    "memory.yaml",
    "security.yaml",
)

from prismclaw.infra.config_io import dump_document, load_document  # noqa: E402
from prismclaw.infra.settings._helpers import (  # noqa: E402
    CONFIG_KEY_OWNERS,
    _deep_merge,
)


def _merged_config() -> dict:
    anchor = load_document(CONFIGS_DIR / "server.yaml")
    assert anchor is not None, "server.yaml 无法解析"
    data: dict = dict(anchor)
    for name in _MODULE_FILES:
        doc = load_document(CONFIGS_DIR / name)
        assert doc is not None, f"{name} 无法解析"
        data = _deep_merge(data, doc)
    return data


def test_all_yaml_files_present_and_parse():
    for name in ("server.yaml", *_MODULE_FILES):
        assert (CONFIGS_DIR / name).is_file(), f"缺少 {name}"
    merged = _merged_config()
    assert merged, "合并结果为空"


def test_merged_config_validates_without_dropped_keys():
    from prismclaw.infra.settings import Settings

    merged = _merged_config()
    settings = Settings.model_validate(merged)
    # 顶层键必须全部在 Schema 中有承载——extra="ignore" 会静默吞键，
    # 这里做全量（而非抽样）断言：任何 yaml 顶层键都不允许被丢弃。
    unknown_top = set(merged) - set(Settings.model_fields)
    assert not unknown_top, f"yaml 顶层键无 schema 承载（会被静默丢弃）: {unknown_top}"
    # 批 0 修复的静默丢弃键——必须真实生效
    assert settings.agent.control.max_repairs >= 0
    # 批 1 收敛后的唯一结果裁剪来源
    cap = settings.agent.context.compaction.tool_results.per_result_char_cap
    assert cap == 8000
    assert not hasattr(settings.agent.context, "tool_output"), "tool_output 配置块应已删除"


def test_key_owners_cover_all_yaml_top_keys():
    merged = _merged_config()
    top_keys = set(merged)
    owners = set(CONFIG_KEY_OWNERS)
    assert top_keys == owners, f"顶层键与归属表不一致: yaml 独有={top_keys - owners}, 表独有={owners - top_keys}"
    for key, fname in CONFIG_KEY_OWNERS.items():
        assert (CONFIGS_DIR / fname).is_file(), f"归属文件缺失 {fname}（键 {key}）"
        doc = load_document(CONFIGS_DIR / fname)
        assert key in doc, f"{fname} 不含其归属键 {key}"


def test_dump_roundtrip_keeps_data(tmp_path: Path):
    source = CONFIGS_DIR / "agent.yaml"
    data = load_document(source)
    assert data is not None
    target = tmp_path / "agent.yaml"
    dump_document(target, data)
    reloaded = load_document(target)
    assert reloaded == data, "写回后数据不一致"


def test_dump_roundtrip_preserves_comments_when_ruamel(tmp_path: Path):
    try:
        from prismclaw.infra.config_io import _ruamel_yaml
    except Exception:  # pragma: no cover
        return
    if _ruamel_yaml() is None:  # pragma: no cover - PyYAML 降级环境不验证注释
        pytest.skip("ruamel.yaml 不可用，跳过注释保留断言")
    source = CONFIGS_DIR / "models.yaml"
    text = source.read_text(encoding="utf-8")
    comment_lines = [ln for ln in text.splitlines() if ln.strip().startswith("#")]
    assert comment_lines, "models.yaml 应包含注释（测试前提）"
    data = load_document(source)
    target = tmp_path / "models.yaml"
    dump_document(target, data)
    new_text = target.read_text(encoding="utf-8")
    assert any("#" in ln for ln in new_text.splitlines()), "ruamel 写回后注释丢失"