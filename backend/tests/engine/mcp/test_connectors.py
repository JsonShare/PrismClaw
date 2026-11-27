from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismclaw.engine.mcp.connectors import ConnectorRegistry


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_servers_only_include_enabled_entries(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mcp.json",
        {
            "connectors": {
                "enabled-server": {"command": "uv", "args": ["run", "server"]},
                "disabled-server": {"command": "uv", "enabled": False},
            }
        },
    )

    registry = ConnectorRegistry(path=path)

    assert set(registry.servers) == {"enabled-server"}
    assert registry.has_enabled is True
    settings = registry.settings()
    assert settings.enabled is True
    assert set(settings.servers) == {"enabled-server"}


def test_all_disabled_means_no_enabled_connectors(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mcp.json",
        {"connectors": {"off": {"command": "uv", "enabled": False}}},
    )

    registry = ConnectorRegistry(path=path)

    assert registry.servers == {}
    assert registry.has_enabled is False
    assert registry.settings().enabled is False


def test_missing_file_raises_value_error_with_path(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"

    with pytest.raises(ValueError, match=str(path)):
        ConnectorRegistry(path=path)


def test_invalid_json_raises_value_error_with_path(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match=str(path)):
        ConnectorRegistry(path=path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "mcp.json", {"mcp": {}, "connectors": {}})

    with pytest.raises(ValueError, match="unsupported top-level keys"):
        ConnectorRegistry(path=path)


def test_reserved_name_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mcp.json",
        {"connectors": {"bad:name": {"command": "uv"}}},
    )

    with pytest.raises(ValueError, match="must not contain"):
        ConnectorRegistry(path=path)
