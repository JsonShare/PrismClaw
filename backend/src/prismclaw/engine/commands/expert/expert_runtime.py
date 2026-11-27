from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.engine.commands.context import CommandContext
from prismclaw.schema.agent.capability import ExpertCommandCapability


@dataclass(slots=True)
class ExpertCommandRuntime:
    expert_name: str
    capability: ExpertCommandCapability

    @property
    def name(self) -> str:
        return self.capability.name

    def export_payload(self) -> dict[str, Any]:
        payload = dict(self.capability.metadata)
        payload.setdefault("type", "expert_command")
        payload.setdefault("name", self.capability.name)
        payload.setdefault("description", self.capability.description)
        payload.setdefault("body", self.capability.body)
        return payload

    async def dispatch(self, args: str, ctx: CommandContext) -> None:
        # 声明命令身份，让 hook matcher 可见
        if ctx.project_command_identity is not None:
            ctx.project_command_identity(
                skill_name=self.name,
                expert_name=self.expert_name,
            )
        await ctx.dispatch_user_message(self.build_invocation_prompt(args))

    def build_invocation_prompt(self, args: str) -> str:
        command = self.export_payload()
        name = str(command.get("name", "/expert:unknown"))
        description = str(command.get("description", "")).strip()
        body = str(command.get("body", "")).strip()
        allowed_tools = command.get("allowed_tools")
        model = command.get("model")
        output_styles = command.get("output_styles")

        lines = [
            f"执行专家包命令 {name}。",
            f"命令运行时来源：expert={self.expert_name}",
        ]
        if description:
            lines.append(f"命令描述：{description}")
        if isinstance(model, str) and model.strip():
            lines.append(f"专家包清单建议的模型：{model.strip()}")
        if isinstance(allowed_tools, list) and allowed_tools:
            lines.append(f"专家包声明的允许工具提示：{'、'.join(allowed_tools)}")
        if isinstance(output_styles, list) and output_styles:
            lines.append(f"建议的输出风格：{'、'.join(output_styles)}")
        lines.append("请在相关场景下严格遵循下方的专家包命令指令。")
        lines.append("")
        lines.append(body)
        lines.append("")
        lines.append("用户参数：")
        lines.append(args.strip() or "（无）")
        return "\n".join(lines)


def build_expert_command_runtime(
    expert_name: str,
    capability: ExpertCommandCapability,
) -> ExpertCommandRuntime:
    return ExpertCommandRuntime(expert_name=expert_name, capability=capability)
