"""配置体系基础设施的单元测试。

覆盖 merge.py 和 resolver.py 的核心逻辑。
"""
from __future__ import annotations

import json

import pytest

from prismclaw.infra.config.merge import (
    DELETE_SENTINEL,
    clean_empty,
    deep_merge,
    filter_stale,
    is_only_schema,
    validate_domain_name,
)
from prismclaw.infra.config.resolver import (
    get_effective_config,
    reset_override,
    reset_override_key,
    save_override,
)


# ═══════════════════════════════════════════════════════════════════════════
# merge.py 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestDeepMerge:
    """deep_merge 算法测试。"""

    def test_leaf_override_no_sibling_loss(self):
        """叶节点覆盖不丢失兄弟键。"""
        base = {"search": {"cnki": True, "pubmed": True, "timeout": 30}}
        override = {"search": {"timeout": 60}}
        result = deep_merge(base, override)
        assert result["search"]["cnki"] is True
        assert result["search"]["pubmed"] is True
        assert result["search"]["timeout"] == 60

    def test_delete_sentinel(self):
        """DELETE 哨兵删除指定键。"""
        base = {"search": {"cnki": True, "pubmed": True}}
        override = {"search": {"cnki": DELETE_SENTINEL}}
        result = deep_merge(base, override)
        assert "cnki" not in result["search"]
        assert result["search"]["pubmed"] is True

    def test_delete_sentinel_key_not_in_base(self):
        """DELETE 哨兵删除不存在的键 → 静默忽略。"""
        base = {"search": {"cnki": True}}
        override = {"search": {"nonexistent": DELETE_SENTINEL}}
        result = deep_merge(base, override)
        assert result["search"]["cnki"] is True

    def test_type_mismatch_skip(self):
        """类型不匹配跳过 + warning。"""
        base = {"timeout": 30}
        override = {"timeout": "30"}
        result = deep_merge(base, override)
        assert result["timeout"] == 30  # 保持 base 值
        assert isinstance(result["timeout"], int)

    def test_nested_merge(self):
        """多层嵌套递归合并。"""
        base = {"a": {"b": {"c": 1, "d": 2}}, "e": 3}
        override = {"a": {"b": {"c": 10}}}
        result = deep_merge(base, override)
        assert result["a"]["b"]["c"] == 10
        assert result["a"]["b"]["d"] == 2
        assert result["e"] == 3

    def test_schema_version_not_merged(self):
        """schema_version 不参与合并。"""
        base = {"schema_version": "1.0", "key": "base"}
        override = {"schema_version": "2.0", "key": "override"}
        result = deep_merge(base, override)
        assert result["key"] == "override"
        # schema_version 来自 base，不被 override 改写
        assert result["schema_version"] == "1.0"

    def test_new_key_allowed(self):
        """键不在 base 中允许新增。"""
        base = {"existing": 1}
        override = {"new_key": 2}
        result = deep_merge(base, override)
        assert result["existing"] == 1
        assert result["new_key"] == 2

    def test_bool_type_mismatch(self):
        """bool 与 int 类型不匹配（Python 里 bool 是 int 子类但 type() 不同）。"""
        base = {"flag": True}
        override = {"flag": 1}
        result = deep_merge(base, override)
        assert result["flag"] is True  # type(bool) != type(int)

    def test_list_override(self):
        """list 类型整体替换（不做元素级合并）。"""
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = deep_merge(base, override)
        assert result["items"] == [4, 5]

    def test_deep_merge_does_not_mutate_base(self):
        """deep_merge 不修改 base 原字典。"""
        base = {"a": {"b": 1}}
        original_base = {"a": {"b": 1}}
        deep_merge(base, {"a": {"b": 2}})
        assert base == original_base


class TestFilterStale:
    """版本守卫 _filter_stale 测试。"""

    def test_removes_unknown_key(self):
        """过滤 defaults 中不存在的键。"""
        override = {"schema_version": "1.0", "unknown_key": "value", "known_key": 1}
        defaults = {"schema_version": "1.0", "known_key": 1}
        cleaned, stale = filter_stale(override, defaults)
        assert "unknown_key" not in cleaned
        assert "known_key" in cleaned
        assert "unknown_key" in stale

    def test_type_mismatch_filter(self):
        """过滤类型不匹配的键。"""
        override = {"timeout": "30"}
        defaults = {"timeout": 30}
        cleaned, stale = filter_stale(override, defaults)
        assert "timeout" not in cleaned
        assert "timeout" in stale

    def test_nested_filter(self):
        """嵌套字典内的 stale 键过滤。"""
        override = {"search": {"cnki": True, "ghost": "val"}}
        defaults = {"search": {"cnki": True}}
        cleaned, stale = filter_stale(override, defaults)
        assert "cnki" in cleaned["search"]
        assert "ghost" not in cleaned["search"]
        assert "search.ghost" in stale

    def test_schema_version_preserved(self):
        """schema_version 始终保留。"""
        override = {"schema_version": "1.0", "extra": 1}
        defaults = {"schema_version": "1.0"}
        cleaned, stale = filter_stale(override, defaults)
        assert cleaned["schema_version"] == "1.0"

    def test_empty_override(self):
        """空覆盖返回空结果。"""
        cleaned, stale = filter_stale({}, {"key": 1})
        assert cleaned == {}
        assert stale == []


class TestCleanEmpty:
    """clean_empty 测试。"""

    def test_removes_empty_dict(self):
        """递归删除空字典。"""
        result = clean_empty({"a": {"b": {}}, "c": 1})
        assert result == {"c": 1}

    def test_preserves_non_empty(self):
        """保留非空字典。"""
        result = clean_empty({"a": {"b": 1}})
        assert result == {"a": {"b": 1}}


class TestIsOnlySchema:
    """is_only_schema 测试。"""

    def test_only_schema_version(self):
        """只剩 schema_version → True。"""
        assert is_only_schema({"schema_version": "1.0"}) is True

    def test_with_other_keys(self):
        """有其他键 → False。"""
        assert is_only_schema({"schema_version": "1.0", "key": 1}) is False

    def test_empty_dict(self):
        """空字典 → False。"""
        assert is_only_schema({}) is False


class TestValidateDomainName:
    """路径遍历防护测试。"""

    def test_valid_names(self):
        """合法域名不抛异常。"""
        validate_domain_name("science")
        validate_domain_name("medical")
        validate_domain_name("my_domain_2")

    def test_rejects_traversal(self):
        """路径遍历攻击被拒绝。"""
        with pytest.raises(ValueError):
            validate_domain_name("../etc")
        with pytest.raises(ValueError):
            validate_domain_name("SCIENCE")  # 大写
        with pytest.raises(ValueError):
            validate_domain_name("a/b")
        with pytest.raises(ValueError):
            validate_domain_name("")

    def test_rejects_dot_prefix(self):
        """以点开头的域名被拒绝。"""
        with pytest.raises(ValueError):
            validate_domain_name(".hidden")


# ═══════════════════════════════════════════════════════════════════════════
# resolver.py 测试（使用 tmp_path 模拟文件系统）
# ═══════════════════════════════════════════════════════════════════════════


class TestGetEffectiveConfig:
    """get_effective_config 测试。"""

    def test_no_override(self, monkeypatch, tmp_path):
        """无覆盖时返回 defaults。"""
        defaults = {
            "schema_version": "1.0",
            "qianfan": {"strategy": "least_used"},
            "search": {"cnki": True, "timeout": 30},
        }
        defaults_path = tmp_path / "configs" / "science.json"
        defaults_path.parent.mkdir(parents=True, exist_ok=True)
        defaults_path.write_text(json.dumps(defaults), encoding="utf-8")

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_defaults_config_path", lambda d: defaults_path)

        result = get_effective_config("science")
        assert result["search"]["cnki"] is True
        assert result["search"]["timeout"] == 30

    def test_with_override(self, monkeypatch, tmp_path):
        """有覆盖时正确合并。"""
        defaults = {
            "schema_version": "1.0",
            "qianfan": {"strategy": "least_used", "safety_reserve": 5},
            "search": {"cnki": True, "pubmed": True, "timeout": 30},
        }
        override_data = {
            "schema_version": "1.0",
            "qianfan": {"strategy": "round_robin"},
            "search": {"cnki": DELETE_SENTINEL, "timeout": 60},
        }
        defaults_path = tmp_path / "configs" / "science.json"
        defaults_path.parent.mkdir(parents=True, exist_ok=True)
        defaults_path.write_text(json.dumps(defaults), encoding="utf-8")

        override_path = tmp_path / "ws" / ".prismclaw" / "configs" / "science.json"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text(json.dumps(override_data), encoding="utf-8")

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_defaults_config_path", lambda d: defaults_path)
        monkeypatch.setattr(resolver, "_override_config_path",
                            lambda d, ws, uid: override_path)

        result = get_effective_config("science", workspace_id="ws", user_id="u1")
        assert result["qianfan"]["strategy"] == "round_robin"  # 覆盖
        assert result["qianfan"]["safety_reserve"] == 5  # 沿用
        assert "cnki" not in result["search"]  # DELETE
        assert result["search"]["pubmed"] is True  # 沿用
        assert result["search"]["timeout"] == 60  # 覆盖

    def test_version_mismatch_filters_stale(self, monkeypatch, tmp_path):
        """版本不一致时过滤 stale 键。"""
        defaults = {
            "schema_version": "2.0",
            "search": {"cnki": True, "timeout": 30},
        }
        # override 的版本是旧的，含有 defaults 中不存在的 stale_key
        override_data = {
            "schema_version": "1.0",
            "stale_key": "should_be_removed",
            "search": {"timeout": 60},
        }
        defaults_path = tmp_path / "configs" / "science.json"
        defaults_path.parent.mkdir(parents=True, exist_ok=True)
        defaults_path.write_text(json.dumps(defaults), encoding="utf-8")

        override_path = tmp_path / "ws" / ".prismclaw" / "configs" / "science.json"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text(json.dumps(override_data), encoding="utf-8")

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_defaults_config_path", lambda d: defaults_path)
        monkeypatch.setattr(resolver, "_override_config_path",
                            lambda d, ws, uid: override_path)

        result = get_effective_config("science", workspace_id="ws", user_id="u1")
        assert "stale_key" not in result
        assert result["search"]["timeout"] == 60


class TestSaveOverride:
    """save_override 测试。"""

    def test_adds_schema_version(self, monkeypatch, tmp_path):
        """保存时自动附加 schema_version。"""
        defaults = {"schema_version": "1.0", "key": "val"}
        defaults_path = tmp_path / "configs" / "science.json"
        defaults_path.parent.mkdir(parents=True, exist_ok=True)
        defaults_path.write_text(json.dumps(defaults), encoding="utf-8")

        override_path = tmp_path / "ws" / ".prismclaw" / "configs" / "science.json"

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_defaults_config_path", lambda d: defaults_path)
        monkeypatch.setattr(resolver, "_override_config_path",
                            lambda d, ws, uid: override_path)

        save_override("science", "ws", "u1", {"search": {"timeout": 60}})

        saved = json.loads(override_path.read_text(encoding="utf-8"))
        assert saved["schema_version"] == "1.0"
        assert saved["search"]["timeout"] == 60


class TestResetOverride:
    """reset_override 测试。"""

    def test_removes_file(self, monkeypatch, tmp_path):
        """重置时删除覆盖文件。"""
        override_path = tmp_path / "ws" / ".prismclaw" / "configs" / "science.json"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text('{"schema_version": "1.0"}', encoding="utf-8")

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_override_config_path",
                            lambda d, ws, uid: override_path)

        reset_override("science", "ws", "u1")
        assert not override_path.exists()


class TestResetOverrideKey:
    """reset_override_key 测试。"""

    def test_deletes_single_key(self, monkeypatch, tmp_path):
        """删除单个键后文件仍保留其他键。"""
        override_path = tmp_path / "ws" / ".prismclaw" / "configs" / "science.json"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_data = {
            "schema_version": "1.0",
            "search": {"cnki": True, "timeout": 60},
        }
        override_path.write_text(json.dumps(override_data), encoding="utf-8")

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_override_config_path",
                            lambda d, ws, uid: override_path)

        reset_override_key("science", "ws", "u1", "search.cnki")
        saved = json.loads(override_path.read_text(encoding="utf-8"))
        assert "cnki" not in saved["search"]
        assert saved["search"]["timeout"] == 60

    def test_removes_file_when_only_schema_left(self, monkeypatch, tmp_path):
        """删除键后只剩 schema_version → 删除整个文件。"""
        override_path = tmp_path / "ws" / ".prismclaw" / "configs" / "science.json"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_data = {
            "schema_version": "1.0",
            "search": {"timeout": 60},
        }
        override_path.write_text(json.dumps(override_data), encoding="utf-8")

        from prismclaw.infra.config import resolver
        monkeypatch.setattr(resolver, "_override_config_path",
                            lambda d, ws, uid: override_path)

        reset_override_key("science", "ws", "u1", "search.timeout")
        assert not override_path.exists()
