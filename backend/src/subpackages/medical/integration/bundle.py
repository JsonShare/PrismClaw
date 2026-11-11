"""医疗域 Bundle — 实现 DomainBundle Protocol，注册全部医疗域能力。

三阶段注册：
1. on_build_infrastructure — 注册持久化存储（MedicalStore + KbStore）+ DDL
2. on_build_runtime — 注册 8 个医疗工具（5 原有 + 3 咨询只读）+ PHI 脱敏钩子 + 咨询上下文注入钩子 + HIPAA 治理规则
                       + 证据推断器 + 免责声明 + 敏感扩展 + PHI 模式
3. on_build_lifecycle — 注册 5 组 REST 路由

此文件是医疗域与 PrismClaw 核心引擎的唯一集成点（integration 层），
其他层（config/models/domain/persistence/services/api）不直接导入核心。
"""
from __future__ import annotations

import logging
from typing import Any

from prismclaw.schema.services.domain import (
    DomainInfrastructureContext,
    DomainRuntimeContext,
    DomainLifecycleContext,
    DomainManifest,
)
# HookEvent 定义在 prismclaw.engine.hooks，通过 facade 门面访问
from prismclaw.runtime.facade.hooks import HookEvent  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)


class MedicalBundle:
    """医疗域 Bundle — 包装医疗域能力 + 提供页面入口。

    工具覆盖：智能病历、医学翻译、指南检索、药品检索、文献检索
              + 3 个咨询只读工具（read_patient_reports/read_raw_report/read_patient_profile）。
    安全：PHI 脱敏钩子（PRE_TOOL）+ 咨询上下文注入（SESSION_START）+ HIPAA 治理 Reducer。
    """

    name = "medical"
    required_core_version = ">=0.1.0,<2.0.0"
    depends_on: list[str] = []
    task_store: Any | None = None  # Protocol 数据成员；本域无长任务 store

    # ── 基础设施：数据库 Schema + 持久化存储 ──────────────────────

    async def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        """注册医疗域持久化存储并确保数据库 Schema 就绪。"""
        from ..persistence.medical_store import MedicalStore
        from ..persistence.kb_store import KbStore
        from ..persistence.brief_store import BriefStore
        from ..persistence.schema import ensure_medical_schema

        ctx.register_store("medical", MedicalStore(ctx.db_engine))
        ctx.register_store("medical_kb", KbStore(ctx.db_engine))
        ctx.register_store("medical_briefs", BriefStore(ctx.db_engine))

        # 确保 DDL 就绪
        try:
            await ensure_medical_schema(ctx.db_engine)
        except Exception:
            logger.exception("医疗域 Schema 初始化失败")

    # ── 运行时：工具 + 钩子 + 治理规则 + 域扩展 ────────────────────

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        """注册医疗域工具、PHI 脱敏钩子和 HIPAA 治理规则。"""
        self._register_tools(ctx)
        self._register_hooks(ctx)
        self._register_governance(ctx)
        self._register_domain_extensions(ctx)
        ctx.commit()

    def _register_tools(self, ctx: DomainRuntimeContext) -> None:
        """注册 8 个医疗工具（5 个原有 + 3 个咨询只读）。"""
        from ..persistence.medical_store import MedicalStore
        from ..tools.smart_medical_record import SmartMedicalRecordTool
        from ..tools.medical_translate import MedicalTranslateTool
        from ..tools.search_guideline import SearchGuidelineTool
        from ..tools.search_drug import SearchDrugTool
        from ..tools.search_literature import SearchLiteratureTool
        from ..tools.read_patient_reports import ReadPatientReportsTool
        from ..tools.read_raw_report import ReadRawReportTool
        from ..tools.read_patient_profile import ReadPatientProfileTool

        medical_store: MedicalStore | None = ctx.domain_stores.get("medical")

        # 原有工具
        record_tool = SmartMedicalRecordTool()
        record_tool.store = medical_store

        # 咨询只读工具
        reports_tool = ReadPatientReportsTool()
        reports_tool.store = medical_store
        raw_report_tool = ReadRawReportTool()
        raw_report_tool.store = medical_store
        profile_tool = ReadPatientProfileTool()
        profile_tool.store = medical_store

        ctx.register_tool(record_tool)
        ctx.register_tool(MedicalTranslateTool())
        ctx.register_tool(SearchGuidelineTool())
        ctx.register_tool(SearchDrugTool())
        ctx.register_tool(SearchLiteratureTool())
        ctx.register_tool(reports_tool)
        ctx.register_tool(raw_report_tool)
        ctx.register_tool(profile_tool)

    def _register_hooks(self, ctx: DomainRuntimeContext) -> None:
        """注册 PHI 脱敏钩子（PRE_TOOL）+ 咨询上下文注入钩子（SESSION_START）。"""
        from ..domain.hooks.phi_redact import PHIRedactHook
        from ..domain.hooks.consultation_context import ConsultationContextHook
        from ..persistence.medical_store import MedicalStore

        ctx.register_hook(HookEvent.PRE_TOOL, PHIRedactHook())

        # SESSION_START hook：咨询会话启动时注入患者报告清单摘要
        medical_store: MedicalStore | None = ctx.domain_stores.get("medical")
        consultation_hook = ConsultationContextHook()
        consultation_hook._store = medical_store
        ctx.register_hook(HookEvent.SESSION_START, consultation_hook)

    def _register_governance(self, ctx: DomainRuntimeContext) -> None:
        """注册 HIPAA 治理 Reducer。"""
        from ..domain.governance.hipaa import HIPAAReducer

        ctx.register_governance(HIPAAReducer())

    def _register_domain_extensions(self, ctx: DomainRuntimeContext) -> None:
        """注册证据推断器、免责声明、敏感扩展、PHI 模式。"""
        # 证据推断器
        from ..domain.analysis.evidence import medical_evidence_inferencer
        from prismclaw.engine.subagent.core.evidence_inference import (
            register_inferencer,
            unregister_inferencer,
        )

        register_inferencer("medical", medical_evidence_inferencer)
        ctx.defer_registry_cleanup(lambda: unregister_inferencer("medical"))

        # 免责声明
        from prismclaw.engine.execution.request._disclaimer_registry import (
            DomainAdvisoryRule,
            register_advisory_rule,
            unregister_advisory_rules,
        )

        register_advisory_rule(DomainAdvisoryRule(
            domain="medical",
            title="医疗免责声明（强制）",
            body='当任务涉及医疗信息（药物剂量、禁忌证、治疗建议、诊断标准）时，'
                 '必须在回答末尾附加免责声明：'
                 '"以上信息仅供参考，不构成医疗建议。具体用药请遵医嘱。" '
                 '无论置信度高低都必须附加此声明——即使是 [HIGH] 证据也不例外。',
        ))
        ctx.defer_registry_cleanup(lambda: unregister_advisory_rules("medical"))

        # 敏感扩展
        from prismclaw.engine.security.gate.routing._extension_registry import (
            register_sensitive_extensions,
            unregister_sensitive_extensions,
        )

        register_sensitive_extensions("medical", frozenset({
            ".dcm", ".dicom", ".hl7", ".nifti", ".nii", ".phi", ".emr", ".ehr",
        }))
        ctx.defer_registry_cleanup(lambda: unregister_sensitive_extensions("medical"))

        # PHI 模式
        from prismclaw.schema.shared._phi_pattern_registry import (
            register_phi_patterns,
            unregister_phi_patterns,
        )
        from ..domain.governance.phi_patterns import MEDICAL_PHI_PATTERNS

        register_phi_patterns("medical", MEDICAL_PHI_PATTERNS)
        ctx.defer_registry_cleanup(lambda: unregister_phi_patterns("medical"))

    # ── 生命周期：REST 路由注册 ───────────────────────────────────

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        """注册医疗域 REST API 路由。"""
        from ..api.records import router as records_router
        from ..api.patients import router as patients_router
        from ..api.kb import router as kb_router
        from ..api.consultations import router as consultations_router
        from ..api.briefs import router as briefs_router

        ctx.register_route(records_router, "/api/medical/records")
        ctx.register_route(patients_router, "/api/medical/patients")
        ctx.register_route(kb_router, "/api/medical/kb")
        ctx.register_route(consultations_router, "/api/medical/consultations")
        ctx.register_route(briefs_router, "/api/medical/briefs")

    # ── 前端菜单/页面描述 ──────────────────────────────────────

    def get_manifest(self) -> DomainManifest:
        """返回医疗域前端菜单与页面描述。"""
        from .._manifest_generated import MANIFEST
        return MANIFEST
