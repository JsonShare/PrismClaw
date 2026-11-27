"""医疗域持久化存储 — MedicalStore + KbStore + Helpers。

PEP 562 惰性导入：Store 类依赖 sqlalchemy，统一惰性导入保持一致性。
DDL 位于域包 ``deploy/sql/*.sql``，由核心启动时自动扫描执行。
"""

__all__ = [
    "MedicalStore",
    "KbStore",
    "row_to_dict",
    "get_medical_store",
    "get_kb_store",
    "get_brief_store",
]

_LAZY_IMPORTS: dict[str, str] = {
    "MedicalStore": ".medical_store",
    "KbStore": ".kb_store",
    "row_to_dict": ".helpers",
    "get_medical_store": ".helpers",
    "get_kb_store": ".helpers",
    "get_brief_store": ".helpers",
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
