from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from prismclaw.infra.config.capability_policy import (
    CapabilityPolicyCandidate,
    CapabilityPolicyLoadError,
    CapabilityPolicyRegistry,
)


class _Catalog:
    def __init__(self, **modules: frozenset[str]) -> None:
        self._modules = modules

    def known_ids(self, kind: str) -> frozenset[str]:
        return self._modules.get(kind, frozenset())


def _rewrite(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_state_precedence_override_bulk_default_and_code_default(tmp_path: Path) -> None:
    path = tmp_path / "capability_policy.yaml"
    path.write_text(
        """
version: 1
defaults:
  expert: false
  team: false
  skill: false
bulk:
  team: true
  skill: false
overrides:
  skill:
    pkg:from-override:
      enabled: true
      reason: explicit exception
""".lstrip(),
        encoding="utf-8",
    )
    registry = CapabilityPolicyRegistry(
        path,
        catalog=_Catalog(
            expert=frozenset({"expert-a"}),
            team=frozenset({"team-a"}),
            skill=frozenset({"pkg:from-override", "pkg:from-bulk"}),
        ),
        reload_interval_seconds=0,
    )

    default_state = registry.state_of("expert", "expert-a")
    bulk_state = registry.state_of("team", "team-a")
    override_state = registry.state_of("skill", "pkg:from-override")
    assert (default_state.enabled, default_state.source) == (False, "default")
    assert (bulk_state.enabled, bulk_state.source) == (True, "bulk")
    assert (override_state.enabled, override_state.source, override_state.reason) == (
        True,
        "override",
        "explicit exception",
    )
    assert registry.filter_enabled("skill", ["pkg:from-override", "pkg:from-bulk"]) == [
        "pkg:from-override"
    ]

    missing_file_registry = CapabilityPolicyRegistry(
        tmp_path / "missing.yaml",
        catalog=_Catalog(expert=frozenset({"expert-a"})),
    )
    code_default = missing_file_registry.state_of("expert", "expert-a")
    assert (code_default.enabled, code_default.source) == (True, "missing")
    assert missing_file_registry.snapshot().version == 1


def test_strict_mode_disables_unlisted_modules_and_ignores_defaults(tmp_path: Path) -> None:
    path = tmp_path / "capability_policy.yaml"
    path.write_text(
        """
version: 1
strict: true
defaults:
  expert: true
  skill: true
overrides:
  expert:
    expert-a: true
  skill:
    pkg:allowed: true
""".lstrip(),
        encoding="utf-8",
    )
    registry = CapabilityPolicyRegistry(
        path,
        catalog=_Catalog(
            expert=frozenset({"expert-a", "expert-b"}),
            skill=frozenset({"pkg:allowed", "pkg:blocked"}),
        ),
        reload_interval_seconds=0,
    )

    allowed = registry.state_of("expert", "expert-a")
    unlisted = registry.state_of("expert", "expert-b")
    blocked_skill = registry.state_of("skill", "pkg:blocked")
    assert (allowed.enabled, allowed.source) == (True, "override")
    assert (unlisted.enabled, unlisted.source) == (False, "strict")
    assert (blocked_skill.enabled, blocked_skill.source) == (False, "strict")
    assert registry.filter_enabled("skill", ["pkg:allowed", "pkg:blocked"]) == ["pkg:allowed"]


def test_yaml_syntax_error_fails_cold_start(tmp_path: Path) -> None:
    path = tmp_path / "capability_policy.yaml"
    path.write_text("defaults:\n  expert: [\n", encoding="utf-8")

    with pytest.raises(CapabilityPolicyLoadError, match="CAPABILITY_POLICY_LOAD_FAILED"):
        CapabilityPolicyRegistry(path, catalog=_Catalog())


def test_invalid_entries_are_reported_and_ignored_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "capability_policy.yaml"
    path.write_text(
        """
version: 1
unknown_root: true
overrides:
  expert:
    ghost: false
    team-a: false
    expert-a:
      enabled: false
      unknown_key: true
  skill:
    unqualified-skill: false
""".lstrip(),
        encoding="utf-8",
    )

    registry = CapabilityPolicyRegistry(
        path,
        catalog=_Catalog(
            expert=frozenset({"expert-a"}),
            team=frozenset({"team-a"}),
            skill=frozenset({"pkg:qualified-skill"}),
        ),
    )

    assert registry.is_enabled("expert", "expert-a") is True
    assert registry.is_enabled("team", "team-a") is True
    assert registry.is_enabled("skill", "pkg:qualified-skill") is True
    issues = registry.snapshot().issues
    assert {issue.code for issue in issues} >= {
        "STATE_UNKNOWN_MODULE",
        "STATE_KIND_MISMATCH",
    }
    assert "unknown_root" in {issue.path for issue in issues}
    assert "overrides.expert.expert-a" in {issue.path for issue in issues}
    assert "overrides.skill.unqualified-skill" in {issue.path for issue in issues}


def test_reload_parse_failure_keeps_old_snapshot_then_valid_change_reloads(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capability_policy.yaml"
    path.write_text(
        "version: 1\noverrides:\n  expert:\n    expert-a: false\n",
        encoding="utf-8",
    )
    registry = CapabilityPolicyRegistry(
        path,
        catalog=_Catalog(expert=frozenset({"expert-a"})),
        reload_interval_seconds=0,
    )
    original = registry.snapshot()
    assert original.version == 1
    assert registry.is_enabled("expert", "expert-a") is False

    _rewrite(path, "version: 1\noverrides: [\n")
    assert registry.reload_if_changed() is False
    assert registry.snapshot() is original
    assert registry.is_enabled("expert", "expert-a") is False

    _rewrite(path, "version: 1\noverrides:\n  expert:\n    expert-a: true\n")
    assert registry.reload_if_changed() is True
    reloaded = registry.snapshot()
    assert reloaded.version == original.version + 1
    assert registry.is_enabled("expert", "expert-a") is True


def test_remote_candidate_does_not_mutate_registry_until_commit(tmp_path: Path) -> None:
    original_catalog = _Catalog(expert=frozenset({"expert-a"}))
    candidate_catalog = _Catalog(expert=frozenset({"expert-a", "expert-b"}))
    registry = CapabilityPolicyRegistry(
        tmp_path / "missing.yaml",
        catalog=original_catalog,
    )
    original = registry.snapshot()

    candidate: CapabilityPolicyCandidate = registry.build_candidate(
        {"overrides": {"expert": {"expert-a": False, "expert-b": False}}},
        version=7,
        catalog=candidate_catalog,
    )

    assert registry.snapshot() is original
    assert registry.is_enabled("expert", "expert-a") is True
    assert candidate.snapshot.version == 7
    assert candidate.snapshot.states[("expert", "expert-b")].enabled is False
    mutable_states = cast(dict[tuple[str, str], Any], candidate.snapshot.states)
    with pytest.raises(TypeError):
        mutable_states[("expert", "expert-a")] = mutable_states[("expert", "expert-b")]

    registry.commit(candidate)
    assert registry.snapshot() is candidate.snapshot
    assert registry.is_enabled("expert", "expert-a") is False
    assert registry.is_enabled("expert", "expert-b") is False


def test_config_layer_import_does_not_load_engine_modules() -> None:
    src = Path(__file__).resolve().parents[3] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(src), env.get("PYTHONPATH", "")))
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import prismclaw.infra.config.capability_policy; "
                "assert not any(name == 'prismclaw.engine' or "
                "name.startswith('prismclaw.engine.') for name in sys.modules)"
            ),
        ],
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
