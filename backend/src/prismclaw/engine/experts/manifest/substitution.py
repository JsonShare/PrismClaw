"""Expert content variable substitution.

Single source of truth for replacing ``${VAR}`` placeholders in expert-owned
content (skill bodies, command bodies, hook commands). Variable *names* follow
the reference implementation's ecosystem convention so experts ported from it
(e.g. ``my-expert``) work unmodified — most importantly
``${PRISMCLAW_PLUGIN_ROOT}``, which expert content uses to reference files shipped
alongside the expert.

Supported variables:
    PRISMCLAW_PLUGIN_ROOT       absolute path to the expert's install directory
    PRISMCLAW_PLUGIN_DATA       absolute path to the expert's persistent data directory
    SKILL_DIR                  absolute path to the directory of the skill being rendered

Semantics (deliberately uniform across all content types):
    * A variable whose value is ``None`` is left untouched, so absent optional
      values (e.g. a expert with no data directory) surface as the literal
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
from collections.abc import Mapping
from pathlib import Path

__all__ = [
    "PRISMCLAW_PLUGIN_ROOT",
    "PRISMCLAW_PLUGIN_DATA",
    "SKILL_DIR",
    "substitute_variables",
    "build_expert_variables",
]

PRISMCLAW_PLUGIN_ROOT = "PRISMCLAW_PLUGIN_ROOT"
PRISMCLAW_PLUGIN_DATA = "PRISMCLAW_PLUGIN_DATA"
PRISMCLAW_USER_HOME = "PRISMCLAW_USER_HOME"
SKILL_DIR = "SKILL_DIR"

# The canonical PrismClaw user home, under which runtime, memory, workspaces,
# experts-data and the user-supplied expert configs all live. Exposed as a
# variable so expert prose can portably reference paths like
# ``~/.prismclaw/experts/config/foo/bar`` without the model ever having to
# expand ``~`` — which it routinely mis-expands (drops the dot, substitutes
# /home/user/foo instead of /home/user/.prismclaw/foo).
_DEFAULT_USER_HOME = str(Path.home() / ".prismclaw")

# A placeholder is ``${NAME}`` where NAME is an identifier that may contain
# dotted segments. Dotted names are matched here so higher-level layers can
# build on the same scanner.
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")


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


def build_expert_variables(
    *,
    expert_root: Path | str | None,
    expert_data_dir: Path | str | None = None,
    skill_dir: Path | str | None = None,
) -> dict[str, str | None]:
    """Build the standard expert variable mapping.

    Each provided path is converted to a string; omitted paths map to ``None``
    (their placeholders are left literal by :func:`substitute_variables`).
    """

    def _as_str(value: Path | str | None) -> str | None:
        if value is None:
            return None
        return str(Path(value))

    return {
        PRISMCLAW_PLUGIN_ROOT: _as_str(expert_root),
        PRISMCLAW_PLUGIN_DATA: _as_str(expert_data_dir),
        PRISMCLAW_USER_HOME: _DEFAULT_USER_HOME,
        SKILL_DIR: _as_str(skill_dir),
    }
