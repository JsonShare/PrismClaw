from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.engine.commands.context import CommandContext
from prismclaw.schema.agent.capability import PluginCommandCapability


@dataclass(slots=True)
class PluginCommandRuntime:
    plugin_name: str
    capability: PluginCommandCapability

    @property
    def name(self) -> str:
        return self.capability.name

    def export_payload(self) -> dict[str, Any]:
        payload = dict(self.capability.metadata)
        payload.setdefault("type", "plugin_command")
        payload.setdefault("name", self.capability.name)
        payload.setdefault("description", self.capability.description)
        payload.setdefault("body", self.capability.body)
        return payload

    async def dispatch(self, args: str, ctx: CommandContext) -> None:
        # 声明命令身份，让 hook matcher 可见
        if ctx.project_command_identity is not None:
            ctx.project_command_identity(
                skill_name=self.name,
                plugin_name=self.plugin_name,
            )
        await ctx.dispatch_user_message(self.build_invocation_prompt(args))

    def build_invocation_prompt(self, args: str) -> str:
        command = self.export_payload()
        name = str(command.get("name", "/plugin:unknown"))
        description = str(command.get("description", "")).strip()
        body = str(command.get("body", "")).strip()
        allowed_tools = command.get("allowed_tools")
        model = command.get("model")
        output_styles = command.get("output_styles")
        mcp_servers = command.get("mcp_servers")
        plugin_options = command.get("plugin_options")

        lines = [
            f"执行插件命令 {name}。",
            f"命令运行时来源：plugin={self.plugin_name}",
        ]
        if description:
            lines.append(f"命令描述：{description}")
        if isinstance(model, str) and model.strip():
            lines.append(f"插件清单建议的模型：{model.strip()}")
        if isinstance(allowed_tools, list) and allowed_tools:
            lines.append(f"插件声明的允许工具提示：{'、'.join(allowed_tools)}")
        if isinstance(output_styles, list) and output_styles:
            lines.append(f"建议的输出风格：{'、'.join(output_styles)}")
        if isinstance(mcp_servers, list) and mcp_servers:
            lines.append(f"插件声明的 MCP 服务：{'、'.join(mcp_servers)}")
        if isinstance(plugin_options, dict) and plugin_options:
            option_summary = "、".join(
                f"{key}={value}" for key, value in sorted(plugin_options.items())
            )
            lines.append(f"已解析的插件用户配置：{option_summary}")
        lines.append("请在相关场景下严格遵循下方的插件命令指令。")
        lines.append("")
        lines.append(body)
        lines.append("")
        lines.append("用户参数：")
        lines.append(args.strip() or "（无）")
        return "\n".join(lines)


def build_plugin_command_runtime(
    plugin_name: str,
    capability: PluginCommandCapability,
) -> PluginCommandRuntime:
    return PluginCommandRuntime(plugin_name=plugin_name, capability=capability)
