from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    # 依赖反转：compression 层禁止 runtime import execution 层。
    # 类型仅用于注解（from __future__ annotations 使其惰性求值）；
    # 运行时通过 type(state) 动态构造，不产生反向依赖。
    from prismclaw.engine.execution.checkpoint.models import (
        SystemPromptSectionRecord,
        SystemPromptSectionState,
    )


@dataclass(frozen=True)
class SectionRetention:
    source: Literal["preflight", "snapshot", "unchanged"] = "unchanged"
    lifecycle_phase: Literal["active", "durable"] | None = None
    retain_all_dynamic_sections: bool = False
    retained_dynamic_section_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source == "unchanged":
            if self.lifecycle_phase is not None:
                raise ValueError("unchanged section retention must not declare a lifecycle phase")
            if self.retain_all_dynamic_sections or self.retained_dynamic_section_names:
                raise ValueError("unchanged section retention must not retain dynamic sections")
            return
        if self.lifecycle_phase is None:
            raise ValueError("section retention must declare a lifecycle phase")
        if self.retain_all_dynamic_sections and self.retained_dynamic_section_names:
            raise ValueError(
                "section retention cannot keep all dynamic sections and a filtered subset at once"
            )

    @classmethod
    def unchanged(cls) -> "SectionRetention":
        return cls()

    @classmethod
    def active_from_preflight(cls) -> "SectionRetention":
        return cls(
            source="preflight",
            lifecycle_phase="active",
            retain_all_dynamic_sections=True,
        )

    @classmethod
    def durable_static(cls) -> "SectionRetention":
        return cls(
            source="snapshot",
            lifecycle_phase="durable",
        )

    @classmethod
    def durable_with_dynamic_sections(
        cls,
        *section_names: str,
    ) -> "SectionRetention":
        retained_names = tuple(
            dict.fromkeys(name for name in section_names if isinstance(name, str) and name)
        )
        return cls(
            source="snapshot",
            lifecycle_phase="durable",
            retained_dynamic_section_names=retained_names,
        )


def apply_section_retention(
    state: SystemPromptSectionState,
    retention: SectionRetention,
) -> SystemPromptSectionState:
    if retention.source == "unchanged":
        return state.model_copy(deep=True)

    retained_static_sections = [
        section.model_copy(deep=True) for section in state.static_sections
    ]
    retained_dynamic_sections = _retained_dynamic_sections(
        state.dynamic_sections,
        retention,
    )
    retained_section_names = {
        section.name for section in [*retained_static_sections, *retained_dynamic_sections] if section.name
    }
    retained_cache_break_sections = [
        name for name in state.cache_break_sections if name in retained_section_names
    ]
    return type(state)(
        lifecycle_phase=retention.lifecycle_phase or "empty",
        static_sections=retained_static_sections,
        dynamic_sections=retained_dynamic_sections,
        cache_break_sections=retained_cache_break_sections,
    )


def _retained_dynamic_sections(
    dynamic_sections: list[SystemPromptSectionRecord],
    retention: SectionRetention,
) -> list[SystemPromptSectionRecord]:
    if retention.retain_all_dynamic_sections:
        return [section.model_copy(deep=True) for section in dynamic_sections]
    retained_names = set(retention.retained_dynamic_section_names)
    if not retained_names:
        return []
    return [
        section.model_copy(deep=True)
        for section in dynamic_sections
        if section.name in retained_names
    ]
