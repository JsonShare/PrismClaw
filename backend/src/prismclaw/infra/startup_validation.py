"""§16.7 安全配置启动校验。

Fail-Closed：生产环境启动时强制校验安全配置，
任何不合规项导致拒绝启动。
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


class StartupValidationError(SystemExit):
    """启动校验失败，拒绝启动。"""
    def __init__(self, message: str) -> None:
        super().__init__(1)
        self.message = message


def validate_security_settings() -> list[str]:
    """执行安全配置启动校验。返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    env = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()
    
    # 1. JWT Secret 长度校验
    jwt_secret = os.getenv("PRISMCLAW_JWT_SECRET", "")
    if env == "production" and len(jwt_secret) < 32:
        errors.append("PRISMCLAW_JWT_SECRET must be >= 32 chars in production")
    
    # 2. HMAC Secret 非默认值
    hmac_secret = os.getenv("PRISMCLAW_RUNTIME_HMAC_SECRET", "dev-secret")
    if env == "production" and hmac_secret == "dev-secret":
        errors.append("PRISMCLAW_RUNTIME_HMAC_SECRET must not be 'dev-secret' in production")
    
    # 3. HMAC Secret 长度
    if env == "production" and len(hmac_secret) < 16:
        errors.append("PRISMCLAW_RUNTIME_HMAC_SECRET must be >= 16 chars in production")
    
    # 4. Sandbox enforce
    sandbox_enforce = os.getenv("PRISMCLAW_SANDBOX_ENFORCE", "true").lower()
    if env == "production" and sandbox_enforce != "true":
        errors.append("PRISMCLAW_SANDBOX_ENFORCE must be 'true' in production")
    
    # 5. DB credentials not default
    db_url = os.getenv("PRISMCLAW_DB_URL", "")
    if env == "production" and not db_url:
        errors.append("PRISMCLAW_DB_URL must be set in production")
    
    # 6. ALLOW_NET must be false for STRICT
    allow_net = os.getenv("PRISMCLAW_ALLOW_NET", "true").lower()
    if env == "production" and allow_net == "true":
        logger.warning("PRISMCLAW_ALLOW_NET=true in production; STRICT workspaces should set this to false")

    # 7. 用户密码非空且非 changeme（JSON users 仍走占位符解析，env 兜底）
    #    注意：users 列表为过渡方案，最终迁移到 DB-backed WebUserStore。
    #    这里校验兜底环境变量，避免空密码启动。
    admin_pwd = os.getenv("PRISMCLAW_USER_ADMIN_PWD", "")
    if env == "production" and (not admin_pwd or admin_pwd == "changeme"):
        errors.append("PRISMCLAW_USER_ADMIN_PWD must be set to a strong value in production (not 'changeme')")
    alice_pwd = os.getenv("PRISMCLAW_USER_ALICE_PWD", "")
    if env == "production" and (not alice_pwd or alice_pwd == "changeme"):
        errors.append("PRISMCLAW_USER_ALICE_PWD must be set to a strong value in production (not 'changeme')")

    # 8. MFA provider 配置（HARD_MFA 生产就绪性）
    #     ConfirmGateway 运行期已 fail-closed（未就绪则拒绝 IRREVERSIBLE 操作），
    #     此处仅作启动期 warning，提示运维补齐 MFA 验证端点配置。
    mfa_url = os.getenv("PRISMCLAW_MFA_VERIFY_URL", "")
    mfa_secret = os.getenv("PRISMCLAW_MFA_SHARED_SECRET", "")
    if env == "production" and (not mfa_url or not mfa_secret):
        logger.warning(
            "PRISMCLAW_MFA_VERIFY_URL / PRISMCLAW_MFA_SHARED_SECRET not set in production; "
            "HARD_MFA (IRREVERSIBLE) operations will be fail-closed rejected until configured"
        )

    return errors


def enforce_startup_validation() -> None:
    """执行启动校验，不合规则拒绝启动。"""
    errors = validate_security_settings()
    if errors:
        for e in errors:
            logger.critical("STARTUP VALIDATION FAILED: %s", e)
        print(f"FATAL — Startup validation failed with {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        raise StartupValidationError(f"{len(errors)} security validation error(s)")
    logger.info("Startup validation passed")
