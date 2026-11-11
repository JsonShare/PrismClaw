from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import smtplib
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Any, Literal

from fastapi import HTTPException

from prismclaw.infra.settings import SmtpSettings

logger = logging.getLogger(__name__)

Scene = Literal["register", "reset"]

# 验证码策略：6 位数字、5 分钟有效、同一邮箱 60 秒冷却、每码最多错 5 次。
CODE_TTL_SECONDS = 300
COOLDOWN_SECONDS = 60
SEND_LIMIT = 5
SEND_WINDOW_SECONDS = 3600
VERIFY_LIMIT = 5
IP_LIMIT = 10
ACTION_LOCK_SECONDS = 10


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _key(prefix: str, *parts: str) -> str:
    return f"{prefix}auth:{':'.join(parts)}"


def _code_key(prefix: str, scene: str, email: str) -> str:
    return _key(prefix, "code", scene, _sha1(email))


def _cooldown_key(prefix: str, email: str) -> str:
    return _key(prefix, "cd", _sha1(email))


def _send_key(prefix: str, email: str) -> str:
    return _key(prefix, "send", _sha1(email))


def _try_key(prefix: str, scene: str, email: str) -> str:
    return _key(prefix, "try", scene, _sha1(email))


def _ip_key(prefix: str, ip: str) -> str:
    return _key(prefix, "ip", _sha1(ip))


def _lock_key(prefix: str, email: str) -> str:
    return _key(prefix, "lock", _sha1(email))


async def _consume_quota(redis: Any, key: str, limit: int, window: int) -> bool:
    """计数 +1，首次写入时设置过期；返回是否仍在配额内。"""
    value = await redis.incr(key)
    if value == 1:
        await redis.expire(key, window)
    return value <= limit


async def _refund_quota(redis: Any, key: str) -> None:
    if await redis.decr(key) <= 0:
        await redis.delete(key)


def _send_sync(smtp: SmtpSettings, to_addr: str, code: str, scene: str) -> None:
    action = "注册" if scene == "register" else "找回密码"
    message = EmailMessage()
    message["Subject"] = f"PrismClaw {action}验证码"
    message["From"] = Address(display_name=smtp.from_name or "PrismClaw", addr_spec=smtp.from_addr)
    message["To"] = to_addr
    message.set_content(
        f"你的 PrismClaw {action}验证码是：{code}\n"
        f"验证码 5 分钟内有效，请勿转发给他人。\n"
        f"若非本人操作，请忽略本邮件。\n"
    )
    if smtp.encryption == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=smtp.timeout_seconds)
    else:
        client = smtplib.SMTP(smtp.host, smtp.port, timeout=smtp.timeout_seconds)
    with client:
        if smtp.encryption == "starttls":
            client.starttls()
        if smtp.username:
            client.login(smtp.username, smtp.password)
        client.send_message(message)


async def issue_code(
    redis: Any,
    *,
    prefix: str,
    smtp: SmtpSettings,
    email: str,
    scene: Scene,
    client_ip: str,
    deliver: bool = True,
) -> None:
    """发送验证码。顺序为：冷却占位 → 配额 → 投递 → 写码。

    ``deliver=False`` 时照常占用冷却与配额、写入验证码，但不投递邮件。
    用于「找回密码但邮箱未绑定」的场景：响应状态与耗时特征需与已绑定邮箱
    保持一致，否则可以通过是否触发 429 反推账号是否存在。

    投递失败会释放冷却与配额，调用方只需处理 HTTPException。
    """
    cooldown = _cooldown_key(prefix, email)
    if not await redis.set(cooldown, "1", ex=COOLDOWN_SECONDS, nx=True):
        raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试")

    send_key = _send_key(prefix, email)
    ip_key = _ip_key(prefix, client_ip)
    if not await _consume_quota(redis, send_key, SEND_LIMIT, SEND_WINDOW_SECONDS):
        await redis.delete(cooldown)
        raise HTTPException(status_code=429, detail="该邮箱发送次数过多，请稍后再试")
    if not await _consume_quota(redis, ip_key, IP_LIMIT, SEND_WINDOW_SECONDS):
        await redis.delete(cooldown)
        await _refund_quota(redis, send_key)
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    code = f"{secrets.randbelow(1_000_000):06d}"
    if deliver:
        try:
            await asyncio.to_thread(_send_sync, smtp, email, code, scene)
        except Exception:
            logger.exception("验证码邮件发送失败")
            await redis.delete(cooldown)
            await _refund_quota(redis, send_key)
            await _refund_quota(redis, ip_key)
            raise HTTPException(status_code=503, detail="验证码邮件发送失败，请稍后再试")

    await redis.set(_code_key(prefix, scene, email), code, ex=CODE_TTL_SECONDS)
    await redis.delete(_try_key(prefix, scene, email))


async def verify_code(redis: Any, *, prefix: str, email: str, scene: Scene, code: str) -> None:
    """校验验证码；错误次数达到上限时立即作废该验证码。"""
    key = _code_key(prefix, scene, email)
    stored = await redis.get(key)
    if not stored:
        raise HTTPException(status_code=400, detail="验证码已失效，请重新获取")

    if not secrets.compare_digest(str(stored), code):
        try_key = _try_key(prefix, scene, email)
        attempts = await redis.incr(try_key)
        if attempts == 1:
            await redis.expire(try_key, CODE_TTL_SECONDS)
        if attempts >= VERIFY_LIMIT:
            await redis.delete(key)
            await redis.delete(try_key)
            raise HTTPException(status_code=400, detail="验证码错误次数过多，请重新获取")
        raise HTTPException(status_code=400, detail="验证码错误")


async def clear_code(redis: Any, *, prefix: str, email: str, scene: Scene) -> None:
    await redis.delete(_code_key(prefix, scene, email))
    await redis.delete(_try_key(prefix, scene, email))


async def acquire_action_lock(redis: Any, *, prefix: str, email: str) -> bool:
    """注册/重置的短并发锁，避免同一邮箱被重复提交。"""
    return bool(await redis.set(_lock_key(prefix, email), "1", ex=ACTION_LOCK_SECONDS, nx=True))


async def release_action_lock(redis: Any, *, prefix: str, email: str) -> None:
    await redis.delete(_lock_key(prefix, email))
