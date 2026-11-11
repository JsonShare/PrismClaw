from __future__ import annotations

import hmac
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from prismclaw.app.auth.auth import create_jwt
from prismclaw.app.auth.email_codes import (
    acquire_action_lock,
    clear_code,
    issue_code,
    release_action_lock,
    verify_code,
)
from prismclaw.app.web.deps import get_web_deps
from prismclaw.infra.settings import SmtpSettings, WebSettings

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

# 邮箱：不做 DNS 校验，只做长度、字符与换行注入防护；昵称禁止 @，因此不会与邮箱混淆。
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_NICKNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")


class TokenRequest(BaseModel):
    identifier: str
    password: str


class TokenResponse(BaseModel):
    token: str
    user_id: str
    nickname: str | None = None


class EmailCodeRequest(BaseModel):
    email: str
    scene: Literal["register", "reset"]


class RegisterRequest(BaseModel):
    nickname: str
    email: str
    code: str
    password: str


class RegisterResponse(BaseModel):
    user_id: str
    nickname: str
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


def _normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 254 or "\r" in email or "\n" in email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return email


def _normalize_nickname(raw: str) -> str:
    nickname = raw.strip()
    if not _NICKNAME_RE.match(nickname):
        raise HTTPException(status_code=400, detail="昵称需为 2-20 位中文、字母、数字或下划线")
    return nickname


def _check_password(raw: str) -> str:
    if (
        not 8 <= len(raw) <= 32
        or any(ch.isspace() for ch in raw)
        or re.search(r"[A-Za-z]", raw) is None
        or re.search(r"\d", raw) is None
    ):
        raise HTTPException(status_code=400, detail="密码需为 8-32 位，同时包含字母和数字，且不含空格")
    return raw


def _check_code(raw: str) -> str:
    code = raw.strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="验证码应为 6 位数字")
    return code


def _require_registration(request: Request) -> tuple[Any, Any, SmtpSettings, str]:
    """注册/找回密码所需依赖：用户仓库、Redis、SMTP 配置、Redis 键前缀。"""
    web_deps = get_web_deps(request)
    repo = web_deps.user_repository
    if repo is None:
        raise HTTPException(status_code=503, detail="用户存储不可用，暂不支持注册与找回密码")
    settings = web_deps.settings_full
    smtp: SmtpSettings = settings.channels.web.smtp
    if not smtp.enabled:
        raise HTTPException(status_code=503, detail="邮箱验证码功能未启用")
    redis = web_deps.redis_client
    if redis is None:
        raise HTTPException(status_code=503, detail="验证码服务不可用")
    return repo, redis, smtp, settings.redis.key_prefix


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@auth_router.post("/token")
async def issue_token(body: TokenRequest, request: Request) -> TokenResponse:
    web_deps = get_web_deps(request)
    settings: WebSettings = web_deps.web_settings
    repo = web_deps.user_repository
    identifier = body.identifier.strip()
    if not identifier or not body.password:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if repo is not None:
        user = await repo.authenticate(identifier, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        # 必须用规范化后的 user.id 签发，前端缓存错误标识会导致工作区错位。
        token = create_jwt(user.id, settings.jwt_secret, expires_in=settings.jwt_expires)
        return TokenResponse(token=token, user_id=user.id, nickname=user.nickname or user.id)

    # 回退路径：用户表不可用时按配置文件用户校验。
    for user in settings.users:
        if user.id == identifier and hmac.compare_digest(user.password, body.password):
            token = create_jwt(user.id, settings.jwt_secret, expires_in=settings.jwt_expires)
            return TokenResponse(token=token, user_id=user.id, nickname=user.id)
    raise HTTPException(status_code=401, detail="用户名或密码错误")


@auth_router.post("/email-code", status_code=204)
async def send_email_code(body: EmailCodeRequest, request: Request) -> Response:
    """发送验证码。为避免账号枚举，一律返回 204。

    register 场景总是投递（邮箱已注册时由注册接口返回 409）；
    reset 场景仅在邮箱确实绑定了账号时才投递，但响应保持一致。
    """
    repo, redis, smtp, prefix = _require_registration(request)
    email = _normalize_email(body.email)

    # 未绑定账号的邮箱也走同一套冷却/配额，仅跳过投递，避免用 429 反推账号存在。
    should_deliver = True
    if body.scene == "reset":
        should_deliver = await repo.get_by_email(email) is not None

    await issue_code(
        redis,
        prefix=prefix,
        smtp=smtp,
        email=email,
        scene=body.scene,
        client_ip=_client_ip(request),
        deliver=should_deliver,
    )
    return Response(status_code=204)


@auth_router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> RegisterResponse:
    repo, redis, _smtp, prefix = _require_registration(request)
    email = _normalize_email(body.email)
    nickname = _normalize_nickname(body.nickname)
    password = _check_password(body.password)
    code = _check_code(body.code)

    if not await acquire_action_lock(redis, prefix=prefix, email=email):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    try:
        await verify_code(redis, prefix=prefix, email=email, scene="register", code=code)

        if await repo.get_by_email(email) is not None:
            # 邮箱已注册：保留验证码，便于用户只改昵称后重试。
            raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录或找回密码")
        if await repo.nickname_exists(nickname):
            raise HTTPException(status_code=409, detail="该昵称已被占用，请换一个")

        try:
            user = await repo.create(
                user_id=uuid.uuid4().hex,
                password=password,
                email=email,
                nickname=nickname,
            )
        except IntegrityError:
            raise HTTPException(status_code=409, detail="邮箱或昵称已被占用")

        await clear_code(redis, prefix=prefix, email=email, scene="register")
        return RegisterResponse(
            user_id=user.id,
            nickname=user.nickname or nickname,
            email=user.email or email,
        )
    finally:
        await release_action_lock(redis, prefix=prefix, email=email)


@auth_router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request) -> None:
    repo, redis, _smtp, prefix = _require_registration(request)
    email = _normalize_email(body.email)
    password = _check_password(body.new_password)
    code = _check_code(body.code)

    if not await acquire_action_lock(redis, prefix=prefix, email=email):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    try:
        await verify_code(redis, prefix=prefix, email=email, scene="reset", code=code)
        user = await repo.get_by_email(email)
        if user is None:
            raise HTTPException(status_code=400, detail="该邮箱未绑定账号")
        if not await repo.set_password(user.id, password):
            raise HTTPException(status_code=400, detail="重置密码失败，请稍后再试")
        await clear_code(redis, prefix=prefix, email=email, scene="reset")
    finally:
        await release_action_lock(redis, prefix=prefix, email=email)
