"""证据推断注册表——核心定义扩展点，域模块注册实现。

核心只维护注册表和调度逻辑，不包含任何领域知识。
各域模块在 on_build_runtime() 中调用 register_inferencer() 注册推断器。

推断器签名: (source_type, source_path, result_text) -> dict[str, str]
  返回 dict 的 key 须带域名前缀，如 "medical.evidence_level"。
"""
from __future__ import annotations

from typing import Callable

# 类型别名
EvidenceInferencer = Callable[[str, str, str], dict[str, str]]

# 全局注册表: {domain_name: [inferencer, ...]}
_INFERENCERS: dict[str, list[EvidenceInferencer]] = {}


def register_inferencer(domain_name: str, inferencer: EvidenceInferencer) -> None:
    """注册领域证据推断器。

    由域模块在 on_build_runtime() 中调用，或由编排层在 apply_runtime() 后统一注册。
    """
    _INFERENCERS.setdefault(domain_name, []).append(inferencer)


def infer_domain_fields(
    source_type: str,
    source_path: str,
    result_text: str,
) -> dict[str, str]:
    """遍历所有已注册的推断器，合并返回领域扩展字段。"""
    result: dict[str, str] = {}
    for _domain, inferencers in _INFERENCERS.items():
        for inferencer in inferencers:
            result.update(inferencer(source_type, source_path, result_text))
    return result


def unregister_inferencer(domain_name: str) -> None:
    """反注册域证据推断器——回滚时由 DomainRuntimeContext 调用。"""
    _INFERENCERS.pop(domain_name, None)


def clear_registry() -> None:
    """清空注册表——仅测试使用。"""
    _INFERENCERS.clear()
