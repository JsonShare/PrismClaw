from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkspaceSettings(BaseSettings):
    default: str = "~/.prismclaw/workspaces"
    bootstrap_files: list[str] = Field(
        default_factory=lambda: ["AGENTS.md"]
    )
    # 工作区级信任控制：列出需要被视为不受信任的工作区路径。
    # 未列出的工作区默认受信任（向后兼容）。
    untrusted_workspaces: list[str] = Field(
        default_factory=list
    )

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class SkillSettings(BaseSettings):
    # —— 静态发现链：repo 管控、sync 到 assets/，绝对 home 路径。
    # origin.py / provider.py / management.py / profile_loader.py /
    # editor.py / discovery.py / plugin_lifecycle.py / runner_deps.py 读这里。
    asset_skills_dir: str = Field("~/.prismclaw/assets/skills")

    # —— 运行时安装链：graduation / ClawHub 安装到 workspace 内，相对路径拼到 workspace_root。
    # skills.py、skill_graduation.py、agent_loop 动态发现读这里。
    workspace_skills_dir: str = Field(".work/skills")

    clawhub_base_url: str = Field("https://clawhub.ai")
    max_skill_file_bytes: int = Field(256_000)
    max_candidates_per_root: int = Field(300)
    max_skills_loaded_per_source: int = Field(200)

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class BuiltinToolSettings(BaseSettings):
    """Feature flags for conditionally registered builtin tools.

    ``fetch_mcp_resource`` ships disabled (BT-D11): the code path is present
    and tested, but the tool is not registered with ``ToolRegistry`` until
    operators light it up.

    grep/glob are always registered and no longer gated by flags. Web
    search/fetch/extract are no longer builtins — they are served by a
    standalone MCP web-retrieval service and toggled through MCP
    configuration.
    """

    fetch_mcp_resource_enabled: bool = Field(
        default=False
    )

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")
