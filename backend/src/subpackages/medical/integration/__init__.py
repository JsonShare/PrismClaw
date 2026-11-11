"""医疗域集成层 — Bundle 是与 PrismClaw 核心的唯一集成点。

PEP 562 惰性导入：integration.bundle 依赖 prismclaw 核心引擎，
在核心未安装的环境下不应因 import subpackages.medical.integration
而触发 ModuleNotFoundError。
"""

__all__ = ["MedicalBundle"]

_LAZY_IMPORTS: dict[str, str] = {
    "MedicalBundle": ".bundle",
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
