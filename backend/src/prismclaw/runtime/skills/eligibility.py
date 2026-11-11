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

    if reqs.env and not check_env(reqs.env):
        return False

    return True


def filter_eligible(skills: list[SkillManifest]) -> list[SkillManifest]:
    # AUTO 才参与 LLM 自主调用；其它策略（USER_ONLY / DISABLED）由 CLI/用户触发
    return [s for s in skills if is_eligible(s) and s.invocation_policy is InvocationPolicy.AUTO]
