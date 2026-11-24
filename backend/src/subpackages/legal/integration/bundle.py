"""法律域 Bundle —— 实现 DomainBundle Protocol，注册法律域能力。

当前提供：
- 前端菜单/页面描述（get_manifest 从编译产物读取）
- USER_PROMPT_SUBMIT 上下文注入钩子（法律域标注要求，按任务内容触发）
- on_build_infrastructure 执行法律域初始化脚本（当前为注释占位）
- on_build_lifecycle 仍为空实现
"""

from __future__ import annotations

import logging
from typing import Any

from prismclaw.engine.hooks import HookEvent
from prismclaw.engine.plugins.hooks.handlers import HookHandler
from prismclaw.engine.plugins.hooks.schema import HookContext, HookResult
from prismclaw.schema.services.domain import (
    DomainInfrastructureContext,
    DomainLifecycleContext,
    DomainManifest,
    DomainRuntimeContext,
)

logger = logging.getLogger(__name__)

_LEGAL_ADVISORY = (
    "[法律域要求] 本任务涉及法律法规、合同条款、合规判断或效力认定："
    "必须区分「条文原文」与「解读」，标注生效日期与效力状态，"
    "不得将学理观点等同于法律效力。"
)

# 分级门控：宁可漏判（少一段提示），不可误注入（污染无关对话）。
#   强信号 —— 技术语境极少出现的词，单独命中即注入；
#   弱信号 —— 技术语境高频词（协议/管辖/继承），需 ≥2 个不同弱词，
#             或出现法律语境词才注入。
_LEGAL_STRONG = (
    "法律",
    "法规",
    "法条",
    "司法解释",
    "诉讼",
    "仲裁",
    "侵权",
    "违约",
    "刑法",
    "民法",
    "行政法",
    "著作权",
    "法律效力",
    "合规",
    "判例",
    "合同",
    "专利",
    "商标",
    "劳动合同",
    "离职",
    "劳务",
)
_LEGAL_WEAK = (
    "协议",
    "条款",
    "继承",
    "婚姻",
    "租赁",
    "借贷",
    "担保",
    "起诉",
    "上诉",
    "管辖",
)
_LEGAL_CONTEXT = (
    "法院",
    "律师",
    "当事人",
    "约定",
    "缔约",
    "甲方",
    "乙方",
    "被告",
    "原告",
    "仲裁机构",
    "判决",
)


def is_legal_task(text: str) -> bool:
    """判断本轮输入是否属于法律任务（分级匹配，保守优先）。"""
    if not text:
        return False
    if any(k in text for k in _LEGAL_STRONG):
        return True
    weak_hits = [k for k in _LEGAL_WEAK if k in text]
    if not weak_hits:
        return False
    if len(weak_hits) >= 2:
        return True
    return any(c in text for c in _LEGAL_CONTEXT)


class LegalAdvisoryHook(HookHandler):
    """USER_PROMPT_SUBMIT 钩子 —— 仅当本轮输入涉法时注入标注要求。

    通用 agent 定义已去掉领域专属标注（保持领域中立），法律域的标注
    要求由本钩子在域启用时按需注入。
    """

    @property
    def name(self) -> str:
        return "legal_advisory"

    @property
    def event(self) -> str:
        return "user_prompt_submit"

    async def execute(self, context: HookContext) -> HookResult:
        if not is_legal_task(context.user_message or ""):
            return HookResult.success()
        return HookResult(additional_context=_LEGAL_ADVISORY)


class LegalBundle:
    """法律域 Bundle —— 提供页面入口、菜单描述与法律标注注入。"""

    name = "legal"
    required_core_version = ">=0.1.0,<2.0.0"
    depends_on: list[str] = []
    task_store: Any | None = None  # Protocol 数据成员；本域无长任务 store

    # ── 基础设施：执行法律域初始化脚本（当前为占位） ──────────────

    async def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        from prismclaw.persistence.sql_runner import apply_domain_schema

        await apply_domain_schema(ctx.db_engine, "legal")

    # ── 运行时：注册法律域上下文注入钩子 ──────────────────────────

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        """注册 USER_PROMPT_SUBMIT 法律标注按需注入钩子。"""
        ctx.register_hook(HookEvent.USER_PROMPT_SUBMIT, LegalAdvisoryHook())
        ctx.commit()

    # ── 生命周期：暂空（骨架阶段无域路由） ───────────────────────

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        """骨架阶段无域级 REST 路由。"""

    # ── 前端菜单/页面描述 ──────────────────────────────────────

    def get_manifest(self) -> DomainManifest:
        """返回法律域前端菜单与页面描述（由 domains.yaml 编译生成）。"""
        from .._manifest_generated import MANIFEST

        return MANIFEST
