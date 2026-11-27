"""专家包注册表子包。"""

from prismclaw.engine.experts.registry.descriptors import (
    AgentDescriptor,
    BaseDescriptor,
    CommandDescriptor,
    DescriptorKind,
    ExpertDescriptor,
    SkillDescriptor,
)
from prismclaw.engine.experts.registry.expert_registry import (
    ExpertRegistry,
    ExpertRegistryIssue,
)
from prismclaw.engine.experts.registry.identity import (
    EXPERT_ID_PATTERN,
    EXPERT_SOURCE,
    VALID_SOURCES,
    ExpertID,
    is_valid_expert_id,
    is_valid_expert_name,
    make_expert_id,
    parse_expert_identifier,
)
from prismclaw.engine.experts.registry.registry import DescriptorRegistry

__all__ = [
    "EXPERT_SOURCE",
    "EXPERT_ID_PATTERN",
    "VALID_SOURCES",
    "AgentDescriptor",
    "BaseDescriptor",
    "CommandDescriptor",
    "DescriptorKind",
    "DescriptorRegistry",
    "ExpertRegistry",
    "ExpertRegistryIssue",
    "ExpertDescriptor",
    "ExpertID",
    "SkillDescriptor",
    "is_valid_expert_id",
    "is_valid_expert_name",
    "make_expert_id",
    "parse_expert_identifier",
]
