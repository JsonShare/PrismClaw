"""Plugin content variable substitution.

Single source of truth for replacing ``${VAR}`` placeholders in plugin-owned
content (skill bodies, command bodies, hook commands). Variable *names* follow
the reference implementation's ecosystem convention so plugins ported from it
(e.g. ``legal-chineselaw``) work unmodified — most importantly
``${PRISMCLAW_PLUGIN_ROOT}``, which plugin content uses to reference files shipped
alongside the plugin.

Supported variables:
    PRISMCLAW_PLUGIN_ROOT       absolute path to the plugin's install directory
    PRISMCLAW_PLUGIN_DATA       absolute path to the plugin's persistent data directory
    PRISMCLAW_MARKETPLACE_DATA  absolute path to the marketplace-level shared data
                             directory (for marketplace sub-plugins that share
                             state across siblings); ``None`` for standalone plugins
    SKILL_DIR                absolute path to the directory of the skill being rendered

Semantics (deliberately uniform across all content types):
    * A variable whose value is ``None`` is left untouched, so absent optional
      values (e.g. a plugin with no data directory) surface as the literal
      placeholder rather than silently vanishing.
    * Unknown variables are left literal.
    * Substitution is single-pass: values are inserted verbatim and never
      re-scanned, so a value containing ``${...}`` cannot trigger recursion.

Shell-quoting of substituted values is the *caller's* responsibility (hook
commands quote via :func:`shlex.quote`; prose content does not). This module
never quotes, keeping it content-agnostic.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

__all__ = [
    "PRISMCLAW_PLUGIN_ROOT",
    "PRISMCLAW_PLUGIN_DATA",
    "PRISMCLAW_MARKETPLACE_DATA",
    "SKILL_DIR",
    "USER_CONFIG_PREFIX",
    "substitute_variables",
    "substitute_user_config_in_content",
    "build_plugin_variables",
]

PRISMCLAW_PLUGIN_ROOT = "PRISMCLAW_PLUGIN_ROOT"
PRISMCLAW_PLUGIN_DATA = "PRISMCLAW_PLUGIN_DATA"
PRISMCLAW_MARKETPLACE_DATA = "PRISMCLAW_MARKETPLACE_DATA"
PRISMCLAW_USER_HOME = "PRISMCLAW_USER_HOME"
SKILL_DIR = "SKILL_DIR"
USER_CONFIG_PREFIX = "user_config."

# The canonical PrismClaw user home, under which runtime, memory, workspaces,
# plugins-data and the user-supplied plugin configs all live. Exposed as a
# variable so plugin prose can portably reference paths like
# ``~/.prismclaw/plugins/config/foo/bar`` without the model ever having to
# expand ``~`` — which it routinely mis-expands (drops the dot, substitutes
# /home/user/foo instead of /home/user/.prismclaw/foo).
_DEFAULT_USER_HOME = str(Path.home() / ".prismclaw")

# A placeholder is ``${NAME}`` where NAME is an identifier that may contain
# dotted segments (e.g. ``user_config.KEY``). Dotted names are matched here so
# higher-level layers (user-config substitution) can build on the same scanner.
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")

_USER_CONFIG_RE = re.compile(r"\$\{user_config\.([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute_variables(content: str, variables: Mapping[str, str | None]) -> str:
    """Replace ``${NAME}`` placeholders in ``content`` from ``variables``.

    Names mapping to ``None`` (or absent from ``variables``) are left as their
    literal placeholder. Values are inserted verbatim (single-pass; no
    re-scanning, no quoting).
    """
    if not content or "${" not in content:
        return content

    def _replace(match: re.Match[str]) -> str:
        value = variables.get(match.group(1))
        return match.group(0) if value is None else value

    return _PLACEHOLDER_RE.sub(_replace, content)


def substitute_user_config_in_content(
    content: str,
    user_config: Mapping[str, object],
    sensitive_keys: frozenset[str] = frozenset(),
) -> str:
    """Replace ``${user_config.KEY}`` placeholders in prose content.

    Implements the reference implementation's three-branch rule for *content*
    (skill/command bodies), as opposed to hook commands:

    1. sensitive key  → a non-revealing placeholder, so a secret never enters
       the model context or any cached/rendered artifact;
    2. undefined key  → left as its literal ``${user_config.KEY}`` placeholder;
    3. otherwise      → the value's string form, inserted verbatim.

    No shell quoting is applied: bodies are prose, not shell commands (hook
    commands quote separately via :func:`shlex.quote`).
    """
    if not content or "${" not in content:
        return content

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in sensitive_keys:
            return f"[redacted:{key}]"
        value = user_config.get(key)
        return match.group(0) if value is None else str(value)

    return _USER_CONFIG_RE.sub(_replace, content)


def build_plugin_variables(
    *,
    plugin_root: Path | str | None,
    plugin_data_dir: Path | str | None = None,
    marketplace_data_dir: Path | str | None = None,
    skill_dir: Path | str | None = None,
) -> dict[str, str | None]:
    """Build the standard plugin variable mapping.

    Each provided path is resolved to an absolute POSIX-style string; omitted
    paths map to ``None`` (their placeholders are left literal by
    :func:`substitute_variables`). ``marketplace_data_dir`` is the shared
    marketplace-level data root (only meaningful for marketplace sub-plugins).
    """

    def _as_str(value: Path | str | None) -> str | None:
        if value is None:
            return None
        return str(Path(value))

    return {
        PRISMCLAW_PLUGIN_ROOT: _as_str(plugin_root),
        PRISMCLAW_PLUGIN_DATA: _as_str(plugin_data_dir),
        PRISMCLAW_MARKETPLACE_DATA: _as_str(marketplace_data_dir),
        PRISMCLAW_USER_HOME: _DEFAULT_USER_HOME,
        SKILL_DIR: _as_str(skill_dir),
    }
