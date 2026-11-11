"""模型注册表 — backend/configs/models.yaml 的 models.providers 段读写。

分工：
  - models.yaml 是模型基建的唯一归属文件：
      · `default_model` —— 全局唯一默认模型名，主系统与所有业务域共享同一份；
      · `providers.*`  —— 服务商凭据 + 模型能力：
          base_url / api_key / llm_provider 挂在【服务商】上，
          display_name / modalities / context_window / max_output_tokens 挂在【模型】上。

为什么凭据挂在服务商而不是模型：下游业务域（medical 等）直接用核心 LLM 客户端
（其 `default_model` 由本文件的 `default_model` 同步而来），核心再交给
`resolve_credentials()` 按模型名反查服务商拿 base_url/api_key。
所以凭据必须落在服务商上才能被解析到。

不变式：
  - 出厂（仓库自带 models.yaml）条目 builtin=true → 可改不可删、不可停用；
  - api_key 只写不读——对外一律返回 has_api_key 布尔，不回传明文；
  - 默认模型必须始终可路由——它既不可删也不可停用，否则下次启动时
    `build_kernel_runtime` 会因解析不到服务商直接抛错；
  - 默认模型改名时同步改写 `default_model`，不留指向空气的悬空引用。

对外 API：
  - list_models()                      列出全部 (服务商, 模型) 条目
  - get_model(provider, model)         读取单个条目
  - create_model(payload)              新增：服务商不存在则一并创建
  - update_model(old_provider, old_model, payload)  修改（支持改名/换服务商）
  - delete_model(provider, model)      删除（内置拒绝、默认模型拒绝）
  - set_model_enabled(provider, model, enabled)     停用/启用（内置拒绝、默认模型拒绝停用）
  - get_default_model()                读取全局默认模型名
  - set_default_model(provider, model) 设置全局默认模型（条目不存在或已停用则拒绝）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .resolver import _read_config_document, _write_config_document

logger = logging.getLogger(__name__)


class ModelRegistryError(ValueError):
    """模型注册表操作失败（路由层转 4xx）。"""


# ---------------------------------------------------------------------------
# 文件读写
# ---------------------------------------------------------------------------

def _models_config_path() -> Path:
    """返回 models 段所属配置文件路径（{项目根}/backend/configs/models.yaml）。

    复用 settings 的模块文件归属判定：谁持有顶层键 `models` 就写回谁，
    避免把改动落到不承载该段的骨架文件上。
    """
    from prismclaw.infra.settings import config_file_owner
    return config_file_owner("models")


def _read_models_file() -> dict[str, Any]:
    """读取 models.yaml 原始文档（保留 display_name/enabled/builtin 等扩展字段）。

    刻意不走 pydantic：ProviderSettings/ModelEntry 是 extra="ignore"，
    经它们往返会静默丢掉这些仅供 UI 使用的元数据。
    """
    return _read_config_document(_models_config_path()) or {}


def _write_models_file(data: dict[str, Any]) -> None:
    """原子写回 models.yaml 并强制重载 settings 缓存。"""
    _write_config_document(_models_config_path(), data)
    from prismclaw.infra.settings import load_settings
    load_settings(force_reload=True)


def _providers_container(data: dict[str, Any]) -> dict[str, Any]:
    """定位（必要时创建）models.providers 容器。"""
    models = data.get("models")
    if not isinstance(models, dict):
        models = {}
        data["models"] = models
    providers = models.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        models["providers"] = providers
    return providers


def _models_container(provider: dict[str, Any]) -> dict[str, Any]:
    """定位（必要时创建）某服务商的 models 容器。"""
    models = provider.get("models")
    if not isinstance(models, dict):
        models = {}
        provider["models"] = models
    return models


def _default_model_of(data: dict[str, Any]) -> str:
    """读取 models.default_model（裸模型名）。缺失或非字符串返回空串。"""
    models = data.get("models")
    if not isinstance(models, dict):
        return ""
    value = models.get("default_model")
    return value.strip() if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# 条目构造 / 投影
# ---------------------------------------------------------------------------

def _entry_projection(
    provider_name: str,
    provider: dict[str, Any],
    model_id: str,
    entry: dict[str, Any],
    *,
    default_model: str,
) -> dict[str, Any]:
    """把 (服务商, 模型) 两级结构投影成扁平行（供 UI 列表消费）。

    `is_default` 按【模型名】判定，与核心 `resolve_provider_for_model` 的路由口径
    一致——核心也是拿模型名在全部服务商里找，找到即用。
    """
    modalities = entry.get("modalities")
    inputs = modalities.get("input") if isinstance(modalities, dict) else None
    inputs = inputs if isinstance(inputs, list) else ["text"]
    return {
        "provider": provider_name,
        "model": model_id,
        "display_name": entry.get("display_name") or "",
        "api_format": provider.get("llm_provider") or "openai",
        "base_url": provider.get("base_url") or "",
        "has_api_key": bool(provider.get("api_key")),
        "supports_image": "image" in inputs,
        "context_window": entry.get("context_window"),
        "max_output_tokens": entry.get("max_output_tokens"),
        "enabled": bool(entry.get("enabled", True)),
        "builtin": bool(entry.get("builtin")),
        "is_default": bool(model_id) and model_id == default_model,
    }


def _positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ModelRegistryError(f"{field} must be an integer") from None
    if parsed < 1:
        raise ModelRegistryError(f"{field} must be >= 1")
    return parsed


def _apply_model_fields(entry: dict[str, Any], payload: dict[str, Any]) -> None:
    """把请求体里出现的字段写入模型条目（缺省字段不动）。"""
    if "display_name" in payload:
        display_name = (payload.get("display_name") or "").strip()
        if display_name:
            entry["display_name"] = display_name
        else:
            entry.pop("display_name", None)

    if "supports_image" in payload:
        modalities = entry.get("modalities")
        if not isinstance(modalities, dict):
            modalities = {}
        modalities["input"] = ["text", "image"] if payload["supports_image"] else ["text"]
        modalities.setdefault("output", ["text"])
        entry["modalities"] = modalities

    for field in ("context_window", "max_output_tokens"):
        if field not in payload:
            continue
        raw = payload[field]
        if raw is None or raw == "":
            entry.pop(field, None)
        else:
            entry[field] = _positive_int(raw, field)


def _apply_provider_fields(provider: dict[str, Any], payload: dict[str, Any]) -> None:
    """把请求体里出现的字段写入服务商条目（缺省字段不动）。"""
    api_format = payload.get("api_format")
    if api_format:
        provider["llm_provider"] = str(api_format).strip()

    if "base_url" in payload:
        base_url = (payload.get("base_url") or "").strip().rstrip("/")
        if base_url:
            provider["base_url"] = base_url
        else:
            provider.pop("base_url", None)

    if "api_key" in payload:
        api_key = (payload.get("api_key") or "").strip()
        if api_key:
            provider["api_key"] = api_key
        # 空字符串 = 用户没填（不是「清空」）——密钥一经写入不因空输入而被抹掉


# ---------------------------------------------------------------------------
# 定位
# ---------------------------------------------------------------------------

def _locate(
    data: dict[str, Any],
    provider_name: str,
    model_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """定位 (providers, provider, entry)；缺任一层即报错。"""
    providers = _providers_container(data)
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        raise ModelRegistryError(f"unknown provider: {provider_name}")
    models = provider.get("models")
    if not isinstance(models, dict):
        raise ModelRegistryError(f"provider has no models: {provider_name}")
    entry = models.get(model_id)
    if not isinstance(entry, dict):
        raise ModelRegistryError(f"unknown model: {provider_name}/{model_id}")
    return providers, provider, entry


def _is_builtin(entry: dict[str, Any]) -> bool:
    """内置判定只看模型条目自身。

    刻意【不】看服务商——否则在出厂服务商下新增的自定义模型会「继承」内置身份，
    从此不可删除。内置是模型级的出厂事实，不是服务商级的。
    """
    return bool(entry.get("builtin"))


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def list_models() -> list[dict[str, Any]]:
    """列出全部模型条目（扁平行，内置优先、同类按名称排序）。"""
    data = _read_models_file()
    default_model = _default_model_of(data)
    providers = _providers_container(data)
    rows: list[dict[str, Any]] = []
    for provider_name in providers:
        provider = providers[provider_name]
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id in models:
            entry = models[model_id]
            if not isinstance(entry, dict):
                continue
            rows.append(
                _entry_projection(
                    provider_name, provider, model_id, entry, default_model=default_model
                )
            )
    rows.sort(key=lambda r: (not r["builtin"], r["provider"], r["model"]))
    return rows


def get_model(provider_name: str, model_id: str) -> dict[str, Any]:
    """读取单个模型条目。"""
    data = _read_models_file()
    _provider, provider, entry = _locate(data, provider_name, model_id)
    assert isinstance(provider, dict)
    return _entry_projection(
        provider_name, provider, model_id, entry, default_model=_default_model_of(data)
    )


def create_model(payload: dict[str, Any]) -> dict[str, Any]:
    """新增模型条目；服务商不存在则一并创建（新建的服务商一律非内置）。"""
    provider_name = (payload.get("provider") or "").strip()
    model_id = (payload.get("model") or "").strip()
    if not provider_name:
        raise ModelRegistryError("provider is required")
    if not model_id:
        raise ModelRegistryError("model is required")

    data = _read_models_file()
    providers = _providers_container(data)

    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        provider = {}
        providers[provider_name] = provider
    _apply_provider_fields(provider, payload)

    models = _models_container(provider)
    if model_id in models:
        raise ModelRegistryError(f"model already exists: {provider_name}/{model_id}")

    entry: dict[str, Any] = {}
    _apply_model_fields(entry, payload)
    models[model_id] = entry

    _write_models_file(data)
    return _entry_projection(
        provider_name, provider, model_id, entry, default_model=_default_model_of(data)
    )


def update_model(
    provider_name: str,
    model_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """修改模型条目。支持改名（model/display_name）与换服务商（provider）。

    内置条目允许修改——这正是填入真实 api_key / base_url 的入口。
    """
    data = _read_models_file()
    providers, provider, entry = _locate(data, provider_name, model_id)

    _apply_provider_fields(provider, payload)
    _apply_model_fields(entry, payload)

    new_provider_name = (payload.get("provider") or provider_name).strip() or provider_name
    new_model_id = (payload.get("model") or model_id).strip() or model_id

    if new_provider_name == provider_name and new_model_id == model_id:
        _write_models_file(data)
        return _entry_projection(
            provider_name, provider, model_id, entry, default_model=_default_model_of(data)
        )

    # 改名/迁移：从旧位置摘除，落到新位置（目标已存在则报错，避免静默覆盖）
    models = provider["models"]
    if new_provider_name != provider_name:
        target_provider = providers.get(new_provider_name)
        if not isinstance(target_provider, dict):
            target_provider = {}
            providers[new_provider_name] = target_provider
        _apply_provider_fields(target_provider, payload)
    else:
        target_provider = provider

    target_models = _models_container(target_provider)
    if new_model_id in target_models:
        raise ModelRegistryError(f"target model already exists: {new_provider_name}/{new_model_id}")

    del models[model_id]
    target_models[new_model_id] = entry

    # 改名不能把默认模型甩成悬空引用——默认模型名同步跟着改
    if _default_model_of(data) == model_id:
        data["models"]["default_model"] = new_model_id

    _write_models_file(data)
    return _entry_projection(
        new_provider_name,
        target_provider,
        new_model_id,
        entry,
        default_model=_default_model_of(data),
    )


def delete_model(provider_name: str, model_id: str) -> None:
    """删除模型条目。内置条目拒绝；服务商删空且非内置时一并移除。"""
    data = _read_models_file()
    providers, provider, entry = _locate(data, provider_name, model_id)

    if _is_builtin(entry):
        raise ModelRegistryError(f"builtin model cannot be deleted: {provider_name}/{model_id}")
    if _default_model_of(data) == model_id:
        raise ModelRegistryError(
            f"default model cannot be deleted: {provider_name}/{model_id}"
        )

    del provider["models"][model_id]
    if not provider["models"]:
        del providers[provider_name]

    _write_models_file(data)


def set_model_enabled(provider_name: str, model_id: str, enabled: bool) -> dict[str, Any]:
    """停用/启用模型条目。内置条目拒绝。"""
    data = _read_models_file()
    providers, provider, entry = _locate(data, provider_name, model_id)

    if _is_builtin(entry):
        raise ModelRegistryError(f"builtin model cannot be disabled: {provider_name}/{model_id}")
    if not enabled and _default_model_of(data) == model_id:
        raise ModelRegistryError(
            f"default model cannot be disabled: {provider_name}/{model_id}"
        )

    entry["enabled"] = bool(enabled)
    _write_models_file(data)
    return _entry_projection(
        provider_name, provider, model_id, entry, default_model=_default_model_of(data)
    )


def set_default_model(provider_name: str, model_id: str) -> list[dict[str, Any]]:
    """把某个已启用的模型设为全局默认。返回更新后的完整条目列表。

    返回整表是刻意的：`is_default` 是行级派生字段，只有整表一起回传，
    前端才能一次刷新把旧默认行的徽标摘掉。
    """
    data = _read_models_file()
    _providers, _provider, entry = _locate(data, provider_name, model_id)

    if not bool(entry.get("enabled", True)):
        raise ModelRegistryError(f"model is disabled: {provider_name}/{model_id}")

    models = data.get("models")
    if not isinstance(models, dict):
        models = {}
        data["models"] = models
    models["default_model"] = model_id

    _write_models_file(data)
    return list_models()
