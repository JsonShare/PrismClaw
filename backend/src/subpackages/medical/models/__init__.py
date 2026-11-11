"""医疗域模型层 — Table 定义、枚举、Schema、ViewModel。

PEP 562 惰性导入：schemas 依赖 pydantic，在核心未安装的环境下
不应因 import subpackages.medical.models 而触发 ModuleNotFoundError。
"""

__all__ = [
    # tables
    "metadata",
    "patients",
    "patient_reports",
    "kb_literatures",
    "kb_guidelines",
    "kb_drugs",
    "PATIENT_COLUMNS",
    "REPORT_COLUMNS",
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
]

_LAZY_IMPORTS: dict[str, str] = {
    # tables
    "metadata": ".tables",
    "patients": ".tables",
    "patient_reports": ".tables",
    "kb_literatures": ".tables",
    "kb_guidelines": ".tables",
    "kb_drugs": ".tables",
    "PATIENT_COLUMNS": ".tables",
    "REPORT_COLUMNS": ".tables",
    # enums
    "ReportType": ".enums",
    "ReportStatus": ".enums",
    # schemas — 依赖 pydantic
    "VmBase": ".schemas",
    "AgentRequest": ".schemas",
    "AgentResponse": ".schemas",
    # view models
    "patient_to_vm": ".view_models",
    "report_to_vm": ".view_models",
    "queue_task_to_vm": ".view_models",
    "literature_to_vm": ".view_models",
    "guideline_to_vm": ".view_models",
    "drug_to_vm": ".view_models",
    "drug_option_to_vm": ".view_models",
}


def __getattr__(name: str):
    module_path = _LAZY_IMPORTS.get(name)
    if module_path is not None:
        import importlib
        module = importlib.import_module(module_path, __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return list(globals()) + __all__
