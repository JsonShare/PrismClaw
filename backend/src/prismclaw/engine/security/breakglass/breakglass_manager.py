"""P2-SEC-07：BreakGlass 熔断机制 — Emergency Access 场景。

当正常安全管线阻断操作但业务需要紧急执行时（如医疗急救、法律截止日期），
授权人员可以通过 Break-Glass 机制临时绕过安全限制。

关键安全约束：
  - requester_id != approver_id（硬性校验，同一人不能自我批准）
  - 同部门 → 告警 + 要求在场验证
  - Token 有时间窗口（默认 30min，最长 2h）
  - 所有 Break-Glass 使用必须写入 WORM 审计日志
  - IAM 独立角色（breakglass_approver）才能批准
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from prismclaw.engine.security.gate.types import BreakGlassStoreProtocol
from prismclaw.schema.content.workspace import SandboxTier
# 避免与 breakglass_recorder 循环 import — 内部模块名显式指向同包
from prismclaw.engine.security.breakglass.breakglass_recorder import BreakGlassRecorder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BreakGlassToken:
    """Break-Glass token。"""
    token: str
    session_id: str
    requester_id: str
    approver_id: str
    reason: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    revoked: bool = False
    workspace_ids: tuple[str, ...] = ()
    tool_pattern: str = ""
    sandbox_tier_cap: SandboxTier | None = None

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired


class InMemoryBreakGlassStore(BreakGlassStoreProtocol):
    """内存 Break-Glass 存储（开发/测试用）。"""

    def __init__(self) -> None:
        self._tokens: dict[str, BreakGlassToken] = {}

    def create_token(self, session_id: str, reason: str, duration_seconds: int = 1800) -> str:
        token_str = f"bg-{secrets.token_urlsafe(16)}"
        self._tokens[token_str] = BreakGlassToken(
            token=token_str,
            session_id=session_id,
            requester_id="",  # 由 BreakGlassManager 填充
            approver_id="",
            reason=reason,
            expires_at=time.time() + min(duration_seconds, 7200),  # 最长 2h
        )
        return token_str

    def validate_token(self, token: str) -> dict[str, Any] | None:
        t = self._tokens.get(token)
        if t is None or not t.is_valid:
            return None
        return {
            "token": t.token,
            "session_id": t.session_id,
            "requester_id": t.requester_id,
            "approver_id": t.approver_id,
            "reason": t.reason,
            "expires_at": t.expires_at,
            "workspace_ids": t.workspace_ids,
            "tool_pattern": t.tool_pattern,
            "sandbox_tier_cap": t.sandbox_tier_cap,
        }

    def revoke_token(self, token: str) -> None:
        t = self._tokens.get(token)
        if t:
            self._tokens[token] = BreakGlassToken(
                token=t.token,
                session_id=t.session_id,
                requester_id=t.requester_id,
                approver_id=t.approver_id,
                reason=t.reason,
                created_at=t.created_at,
                expires_at=t.expires_at,
                revoked=True,
            )

    def store_token(self, token: BreakGlassToken) -> None:
        """存储完整 token（含 requester/approver）。"""
        self._tokens[token.token] = token


@dataclass
class BreakGlassRequest:
    """Break-Glass 请求。"""
    session_id: str
    requester_id: str
    reason: str
    duration_seconds: int = 1800
    workspace_id: str = ""


@dataclass
class BreakGlassResult:
    """Break-Glass 操作结果。"""
    allowed: bool
    reason: str = ""
    token: str = ""
    approver_id: str = ""
    warning: str = ""


class BreakGlassManager:
    """Break-Glass 熔断管理器。

    核心安全约束：
      1. requester_id != approver_id（硬性校验）
      2. 同部门 → 告警
      3. Token 有时间窗口
      4. 所有操作写入 WORM 审计
      5. P4-SEC-01：BreakGlassRecorder 录屏 — token 创建即 start_recording，
         revoke/过期前 stop_recording，分片写入 WORM 存储（哈希链 + 24h 完整性校验）
    """

    def __init__(
        self,
        store: BreakGlassStoreProtocol | None = None,
        *,
        worm_audit: Any = None,
        max_duration_seconds: int = 7200,  # 最长 2h
        recorder: BreakGlassRecorder | None = None,
    ) -> None:
        self._store = store or InMemoryBreakGlassStore()
        self._worm_audit = worm_audit
        self._max_duration = max_duration_seconds
        # P4-SEC-01：BreakGlassRecorder（可选，默认创建本地存储实例）
        self._recorder = recorder if recorder is not None else BreakGlassRecorder()
        # token → (session_id, auto_stop_task)：过期自动 stop_recording 回收
        self._recording_tasks: dict[str, asyncio.Task] = {}

    @property
    def recorder(self) -> BreakGlassRecorder:
        """P4-SEC-01 接入点：当前绑定的录屏记录器。"""
        return self._recorder

    def request(
        self,
        req: BreakGlassRequest,
        *,
        approver_id: str,
    ) -> BreakGlassResult:
        """处理 Break-Glass 请求。

        核心约束：requester_id != approver_id。
        P4-SEC-01 接入：批准成功后立即启动录屏（BreakGlassRecorder），
        并按 duration 调度 auto-stop，避免录屏文件无限增长。
        """
        # 硬性校验：同一人不能自我批准
        if req.requester_id == approver_id:
            return BreakGlassResult(
                allowed=False,
                reason="breakglass_same_person: requester cannot self-approve",
            )

        # 检查时长上限
        duration = min(req.duration_seconds, self._max_duration)

        # 创建 token
        token_str = self._store.create_token(
            session_id=req.session_id,
            reason=req.reason,
            duration_seconds=duration,
        )

        # 存储完整 token（含 requester/approver）
        if isinstance(self._store, InMemoryBreakGlassStore):
            full_token = BreakGlassToken(
                token=token_str,
                session_id=req.session_id,
                requester_id=req.requester_id,
                approver_id=approver_id,
                reason=req.reason,
                expires_at=time.time() + duration,
            )
            self._store.store_token(full_token)

        # P4-SEC-01：启动录屏
        try:
            self._recorder.start_recording(token_str, req.session_id)
        except Exception as e:
            logger.error("BreakGlassManager: start_recording failed token=%s err=%s", token_str, e)

        # 调度过期自动 stop_recording（复用或新建事件循环）
        self._schedule_auto_stop(token_str, duration)

        # WORM 审计
        if self._worm_audit is not None:
            self._worm_audit.log(
                req.session_id,
                "breakglass_created",
                {
                    "token": token_str,
                    "requester_id": req.requester_id,
                    "approver_id": approver_id,
                    "reason": req.reason,
                    "duration_seconds": duration,
                },
            )

        # 同部门告警
        warning = ""
        if self._same_department(req.requester_id, approver_id):
            warning = "breakglass_same_department: on-site verification recommended"

        return BreakGlassResult(
            allowed=True,
            reason="breakglass_approved",
            token=token_str,
            approver_id=approver_id,
            warning=warning,
        )

    def validate(
        self,
        token: str,
        *,
        workspace_id: str = "",
        tool_name: str = "",
        required_tier: SandboxTier | None = None,
    ) -> BreakGlassResult:
        """验证 Break-Glass token。

        过期/无效 token 会顺带触发录屏 stop，避免 revoke 绕过情况下的录屏残留。
        可选校验 workspace_id、tool_name、required_tier 与 token 的 scoping 字段。
        """
        info = self._store.validate_token(token)
        if info is None:
            # token 已失效 — 兜底停止录屏
            self._cancel_auto_stop(token)
            self._run_async(self._safe_stop_recording(token))
            return BreakGlassResult(
                allowed=False,
                reason="breakglass_invalid_or_expired",
            )

        # Scoping: workspace_ids
        token_workspace_ids = info.get("workspace_ids", ())
        if token_workspace_ids and workspace_id and workspace_id not in token_workspace_ids:
            return BreakGlassResult(
                allowed=False,
                reason=f"breakglass_workspace_mismatch: {workspace_id} not in {token_workspace_ids}",
            )

        # Scoping: tool_pattern
        token_tool_pattern = info.get("tool_pattern", "")
        if token_tool_pattern and tool_name and not fnmatch(tool_name, token_tool_pattern):
            return BreakGlassResult(
                allowed=False,
                reason=f"breakglass_tool_mismatch: {tool_name} does not match {token_tool_pattern}",
            )

        # Scoping: sandbox_tier_cap
        token_tier_cap = info.get("sandbox_tier_cap")
        from prismclaw.engine.security.gate.routing.risk_router import _TIER_ORDER
        if token_tier_cap is not None and required_tier is not None:
            if _TIER_ORDER.get(required_tier, 0) > _TIER_ORDER.get(token_tier_cap, 0):
                return BreakGlassResult(
                    allowed=False,
                    reason=f"breakglass_tier_exceeded: required {required_tier} exceeds cap {token_tier_cap}",
                )

        return BreakGlassResult(
            allowed=True,
            reason="breakglass_valid",
            token=token,
            approver_id=info.get("approver_id", ""),
        )

    def active_token(self, workspace_id: str, tool_name: str) -> BreakGlassToken | None:
        """查找匹配 workspace+tool 的活跃（未过期、未撤销）token。"""
        if not isinstance(self._store, InMemoryBreakGlassStore):
            return None  # 非内存存储不支持遍历
        for t in self._store._tokens.values():
            if not t.is_valid:
                continue
            if t.workspace_ids and workspace_id not in t.workspace_ids:
                continue
            if t.tool_pattern and not fnmatch(tool_name, t.tool_pattern):
                continue
            return t
        return None

    def revoke(self, token: str) -> None:
        """撤销 Break-Glass token（同时停止录屏 + 上传索引）。"""
        # 先取 session_id（revoke 后 validate_token 返回 None）
        info = self._store.validate_token(token) or {}
        session_id = info.get("session_id", "")

        self._store.revoke_token(token)
        self._cancel_auto_stop(token)
        self._run_async(self._safe_stop_recording(token))

        if self._worm_audit is not None:
            self._worm_audit.log(
                session_id,
                "breakglass_revoked",
                {"token": token},
            )

    # ── P4-SEC-01 录屏辅助 ──────────────────────────────────────────

    def _run_async(self, coro: Any) -> None:
        """在当前事件循环中安全运行协程（兼容 sync/async 调用上下文）。

        替代 asyncio.get_event_loop()（Python 3.10+ 已弃用）：
          - 有运行中的循环 → create_task fire-and-forget
          - 无运行循环 → asyncio.run()（新建临时循环并关闭）
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(coro)
        else:
            asyncio.run(coro)

    async def _safe_stop_recording(self, token: str) -> None:
        """安全停止录屏：失败不抛异常，只记日志。"""
        try:
            await self._recorder.stop_recording(token)
        except Exception as e:
            logger.error("BreakGlassManager: stop_recording failed token=%s err=%s", token, e)

    def _schedule_auto_stop(self, token: str, duration_seconds: int) -> None:
        """按 token 生命周期调度自动停止录屏。"""
        async def _auto_stop():
            try:
                await asyncio.sleep(max(0, duration_seconds))
            except asyncio.CancelledError:
                return
            try:
                await self._safe_stop_recording(token)
            finally:
                self._recording_tasks.pop(token, None)

        # 取消旧的（重复 request 同一 token 少见，但防御）
        self._cancel_auto_stop(token)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 同步环境：记录 warning，不调度（revoke/validate 会兜底）
            logger.warning(
                "BreakGlassManager: no running loop, auto-stop NOT scheduled for token %s",
                token,
            )
            return
        task = loop.create_task(_auto_stop())
        self._recording_tasks[token] = task

    def _cancel_auto_stop(self, token: str) -> None:
        task = self._recording_tasks.pop(token, None)
        if task is not None and not task.done():
            task.cancel()

    @staticmethod
    def _same_department(user1: str, user2: str) -> bool:
        """简单部门检查（基于用户 ID 前缀）。Phase 2b 接入 IAM。"""
        # 临时实现：取第二个 segment 作为部门标识
        # 格式：{dept}-{username}，如 "cardio-zhang" vs "neuro-li"
        parts1 = user1.split("-")
        parts2 = user2.split("-")
        if len(parts1) >= 2 and len(parts2) >= 2:
            return parts1[0] == parts2[0]
        return user1[:3] == user2[:3]
