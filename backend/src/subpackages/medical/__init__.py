"""医疗域后端模块 — PrismClaw Medical Domain Bundle。

提供医疗场景的工具、钩子、治理规则、持久化存储和 REST 路由，
通过 DomainBundle Protocol 接入 PrismClaw 平台。

公共 API（PEP 562 惰性导入 — 仅在首次访问时加载）：
- MedicalBundle: 域 Bundle 入口（由 entry-point 加载）
- MedicalStore / KbStore: 持久化存储
- ensure_medical_schema: DDL 确保
- VM 转换器 + 枚举 + Schema

惰性导入的必要性：
- integration/ 层依赖 prismclaw 核心引擎（pydantic 等），
  在核心未安装的环境下（如独立测试 medical 模型层）
  不应因顶层 import 而触发 ModuleNotFoundError。
- models/schemas 依赖 pydantic，同理解耦。
"""

__all__ = [
    # integration
    "MedicalBundle",
    # persistence
    "MedicalStore",
    "KbStore",
    "ensure_medical_schema",
    "row_to_dict",
    "get_medical_store",
    "get_kb_store",
    # enums
    "ReportType",
    "ReportStatus",
    # schemas
    "VmBase",
    # view models
    "patient_to_vm",
    "report_to_vm",
    "queue_task_to_vm",
    "literature_to_vm",
    "guideline_to_vm",
    "drug_to_vm",
    "drug_option_to_vm",
    "consultation_to_vm",
]

# ── PEP 562 惰性导入 ──────────────────────────────────────────
# 仅在属性被首次访问时才触发真实 import，避免模块加载时
# 拉起 prismclaw 核心 / pydantic 等重量级依赖链。

_LAZY_IMPORTS: dict[str, str] = {
    # integration — 依赖 prismclaw 核心
    "MedicalBundle": ".integration.bundle",
    # persistence — 依赖 sqlalchemy（轻量，但统一惰性）
    "MedicalStore": ".persistence.medical_store",
    "KbStore": ".persistence.kb_store",
    "ensure_medical_schema": ".persistence.schema",
    "row_to_dict": ".persistence.helpers",
    "get_medical_store": ".persistence.helpers",
    "get_kb_store": ".persistence.helpers",
    # enums — 纯标准库，理论上可即时导入，但统一惰性更一致
    "ReportType": ".models.enums",
    "ReportStatus": ".models.enums",
    # schemas — 依赖 pydantic
    "VmBase": ".models.schemas",
    # view models — 依赖 models.schemas（pydantic）
    "patient_to_vm": ".models.view_models",
    "report_to_vm": ".models.view_models",
    "queue_task_to_vm": ".models.view_models",
    "literature_to_vm": ".models.view_models",
    "guideline_to_vm": ".models.view_models",
    "drug_to_vm": ".models.view_models",
    "drug_option_to_vm": ".models.view_models",
    "consultation_to_vm": ".models.view_models",
}


def __getattr__(name: str):
    """PEP 562 — 首次访问公共名称时，按需导入并缓存到模块全局。"""
    module_path = _LAZY_IMPORTS.get(name)
    if module_path is not None:
        import importlib
        module = importlib.import_module(module_path, __name__)
        value = getattr(module, name)
        globals()[name] = value  # 缓存，后续访问不再走 __getattr__
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """让 dir() 和 IDE 自动补全包含 __all__ 中的名称。"""
    return list(globals()) + __all__
