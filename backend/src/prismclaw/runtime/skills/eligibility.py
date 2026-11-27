from __future__ import annotations

import os
import shutil
import sys

from prismclaw.schema.agent.domains import InvocationPolicy
from prismclaw.schema.services.skills import SkillManifest


def check_os(os_list: list[str]) -> bool:
    if not os_list:
        return True
    return sys.platform in os_list


def check_bins(bins: list[str]) -> bool:
    if not bins:
        return True
    return all(shutil.which(b) is not None for b in bins)


def check_any_bins(any_bins: list[str]) -> bool:
    if not any_bins:
        return True
    return any(shutil.which(b) is not None for b in any_bins)


def check_env(env_vars: list[str]) -> bool:
    if not env_vars:
        return True
    return all(os.environ.get(e) for e in env_vars)


def is_eligible(skill: SkillManifest) -> bool:
    reqs = skill.requirements

    if reqs.os and not check_os(reqs.os):
        return False

    if reqs.bins and not check_bins(reqs.bins):
        return False

    if reqs.any_bins and not check_any_bins(reqs.any_bins):
        return False

    return not (reqs.env and not check_env(reqs.env))


def is_user_visible(skill: SkillManifest) -> bool:
    """用户侧（选择器 / 显式调用）可见：auto 与 user_only。"""
    return skill.invocation_policy in (InvocationPolicy.AUTO, InvocationPolicy.USER_ONLY)


def is_model_visible(skill: SkillManifest) -> bool:
    """模型侧（检索 / 自主激活）可见：auto 与 model_only。"""
    return skill.invocation_policy in (InvocationPolicy.AUTO, InvocationPolicy.MODEL_ONLY)


def filter_eligible(skills: list[SkillManifest]) -> list[SkillManifest]:
    """只做环境可用性过滤；调用策略由各可见性入口分别判断。"""
    return [s for s in skills if is_eligible(s)]
