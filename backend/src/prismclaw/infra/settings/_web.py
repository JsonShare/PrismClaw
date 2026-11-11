from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from prismclaw.schema.agent.types import WorkspaceMode


class WebUserConfig(BaseModel):
    id: str
    password: str

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class WebUserStoreSettings(BaseSettings):
    """DB-backed web user store settings (PostgreSQL only).

    Uses the application-wide shared AsyncEngine (see ``prismclaw.infra.db``).
    No separate connection configuration — the user store shares
    ``StorageSettings.postgres_dsn``.

    Env vars:
      PRISMCLAW_WEB_USER_ENABLED          enable DB-backed user store
      PRISMCLAW_WEB_USER_BOOTSTRAP_ADMIN_ID / _PASSWORD   first-run admin
      PRISMCLAW_WEB_USER_LOCKOUT_THRESHOLD / _WINDOW_SECONDS
      PRISMCLAW_WEB_USER_PASSWORD_SCHEME   scrypt | bcrypt
    """

    enabled: bool = True
    bootstrap_admin_id: str = Field("")
    bootstrap_admin_password: str = Field("")
    lockout_threshold: int = Field(5)
    lockout_window_seconds: int = Field(900)
    password_scheme: Literal["scrypt", "bcrypt"] = Field("scrypt")

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_WEB_USER_", populate_by_name=True, extra="ignore"
    )


class SmtpSettings(BaseModel):
    """注册 / 找回密码验证码的邮件发送通道。

    关闭时注册与找回密码接口返回 503，登录不受影响。
    """

    enabled: bool = False
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    from_name: str = "PrismClaw"
    encryption: Literal["none", "starttls", "ssl"] = "starttls"
    timeout_seconds: int = 15

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class WebSettings(BaseSettings):
    enabled: bool = False
    user_store: WebUserStoreSettings = Field(default_factory=lambda: WebUserStoreSettings())
    auth_token: str = Field("")
    jwt_secret: str = Field("change-me-in-production")
    jwt_expires: int = Field(2592000, ge=60)
    """JWT 有效期（秒），默认 30 天。过期后需重新登录（当前无 refresh 机制）。"""
    heartbeat_interval: int = Field(30)
    pong_timeout: int = Field(10)
    max_connections_per_user: int = Field(12)
    """Per-user WS 连接上限。3 过严：多标签页/多窗口是正常用法（实测单用户
    6 个标签即触发 4004 断连横幅反复闪烁），放宽以避免多标签场景的连接震荡。"""
    trust_proxy_headers: bool = Field(False)
    default_mode: WorkspaceMode = Field(
        "chat"
    )
    """Default mode when ``chat.send`` arrives without an explicit ``mode`` field."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    users: list[WebUserConfig] = Field(default_factory=list)
    smtp: SmtpSettings = Field(default_factory=lambda: SmtpSettings())

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class ChannelsSettings(BaseSettings):
    web: WebSettings = Field(default_factory=lambda: WebSettings())

    model_config = SettingsConfigDict(extra="ignore")
