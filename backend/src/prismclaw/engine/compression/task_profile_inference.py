from __future__ import annotations

from typing import Any

from prismclaw.schema.content.context import ContextBlock, TaskProfile


class TaskProfileInference:
    def infer(
        self,
        *,
        prompt: str | None,
        messages: list[Any],
        memory_blocks: list[ContextBlock],
    ) -> TaskProfile:
        material_types: list[str] = []

        def _add_material(material: str) -> None:
            if material not in material_types:
                material_types.append(material)

        tool_turn_seen = False
        image_seen = False
        file_seen = False
        code_seen = False

        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "tool":
                tool_turn_seen = True
                _add_material("tool_result")

            content = message.get("content")
            if isinstance(content, str):
                if "```" in content:
                    code_seen = True
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type in {"image_url", "input_image"}:
                        image_seen = True
                    elif block_type == "file":
                        file_seen = True
                    elif block_type == "text" and "```" in str(block.get("text", "")):
                        code_seen = True

        if code_seen:
            _add_material("code")
        if image_seen:
            _add_material("image")
        if file_seen:
            _add_material("document")
        if not material_types:
            _add_material("text")
        if memory_blocks:
            _add_material("retrieval_result")

        requires_grounding = image_seen or file_seen or bool(memory_blocks)
        prefers_tooling = image_seen or file_seen or tool_turn_seen
        risk_level = "high" if requires_grounding else "normal"
        notes: list[str] = []
        if memory_blocks:
            notes.append("memory_retrieval_attached")
        if image_seen or file_seen:
            notes.append("non_text_input_present")
        return TaskProfile(
            risk_level=risk_level,  # type: ignore[arg-type]
            material_types=material_types,  # type: ignore[arg-type]
            prefers_tooling=prefers_tooling,
            requires_grounding=requires_grounding,
            notes=notes,
        )
