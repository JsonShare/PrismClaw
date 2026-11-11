"""配置体系基础设施。

Layer 0: 模型基建 (backend/configs/models.yaml) — 服务商凭据 + 模型能力 + 全局默认模型
Layer 1: 域默认 (backend/src/subpackages/{domain}/configs/{domain}.json) + 工作区覆盖 ({ws}/.work/configs/{domain}.json)

模型基建只有一份，主系统与所有业务域共享：域不再自己解析模型名，直接用核心
LLM 客户端（其 `default_model` 由 models.yaml 的 `default_model` 同步而来）。

对外 API 见 models_registry.py（模型注册表）、resolver.py（域配置解析）；合并算法见 merge.py。
"""

from .merge import DELETE_SENTINEL, deep_merge, filter_stale
from .models_registry import (
    ModelRegistryError,
    create_model,
    delete_model,
    get_model,
    list_models,
    set_default_model,
    set_model_enabled,
    update_model,
)
from .resolver import (
    get_effective_config,
    reset_override,
    reset_override_key,
    save_override,
)

__all__ = [
    "DELETE_SENTINEL",
    "ModelRegistryError",
    "create_model",
    "deep_merge",
    "delete_model",
    "filter_stale",
    "get_effective_config",
    "get_model",
    "list_models",
    "reset_override",
    "reset_override_key",
    "save_override",
    "set_default_model",
    "set_model_enabled",
    "update_model",
]
