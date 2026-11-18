from __future__ import annotations

from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result

# ─── DeclareBlockedTool ───────────────────────────────────────────────

class DeclareBlockedTool:
    name = "declare_blocked"
    description = (
        "声明任务目标确实已被阻塞（例如缺少凭据、外部依赖不可达、硬性约束相互冲突）。"
        "请提供稳定的 reason_code，以及具体说明阻塞情况的 reason_message。"
        "声明阻塞本身并不会结束任务：运行循环只会在足够多的推进轮次之后接受该声明，"
        "然后带着你给出的原因终止。任务难度大、存在不确定性、仍有可做的工作、"
        "或预算即将耗尽，均不属于阻塞条件。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason_code": {
                "type": "string",
                "description": "稳定的机器可读代码，例如 'missing_credentials' 或 'external_service_unreachable'。",
            },
            "reason_message": {
                "type": "string",
                "description": "具体的阻塞情况：尝试了什么，以及究竟是什么导致无法完成。",
            },
        },
        "required": ["reason_code", "reason_message"],
        "additionalProperties": False,
    }
    side_effect = False
    is_concurrency_safe = True
    tool_class = "read"
    effect_class = "pure_read"
    # 阻塞申报由主循环的完成判定消费；子代理循环没有该裁决点，
    # 暴露给它只会产出"已记录"的空指引。
    visibility_scope = "main_only"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        from prismclaw.engine.toolkit.registry.tool_runtime import GoalBlockIntent

        call_id = args.get("_call_id", "")
        code = args.get("reason_code")
        message = args.get("reason_message")
        code = code.strip() if isinstance(code, str) else ""
        message = message.strip() if isinstance(message, str) else ""
        if not code or not message:
            return error_result(
                call_id,
                "declare_blocked：'reason_code' 与 'reason_message' 均为必填。",
            )

        context.goal_block_intent = GoalBlockIntent(code=code, message=message)
        return text_result(
            call_id,
            f"declare_blocked：已记录阻塞意图（{code}）。停止调用工具，说明"
            "阻塞条件与已交付内容；运行循环将决定是否采纳该申报。",
            metadata={"reason_code": code, "reason_message": message},
        )


# ─── AskUserQuestionTool ──────────────────────────────────────────────

class AskUserQuestionTool:
    name = "ask_user_question"
    description = "向用户提问并暂停执行，收到作答后继续。仅当需要的信息只有用户知道（偏好、意图、决策或需要用户补充的材料）且不同选择的后果差异显著时使用。文件位置、现有实现、当前状态等可发现的事实必须先探索，禁止拿来问用户；能合理假设的直接假设并在结果中声明。需要用户补充信息或上传文件时也用本工具，不要只在正文里说明。每次调用只提交一个问题，选项 2-4 个，推荐项置首并标「（推荐）」；同一任务的连续追问不超过 3 个。答案标记「（跳过）」的题目按首位选项继续，并在最终结果中声明该假设，不要重复追问。"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "本次需要用户回答的问题；每次调用只放 1 个",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "问题正文，一句话，只问一个决策点"},
                        "header": {"type": "string", "description": "该问题的短标题（12 字以内），用于答案回传时对应"},
                        "options": {
                            "type": "array",
                            "description": "选项之间互斥；推荐选项放首位并在 label 后加「（推荐）」；不要放凑数项",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"}
                                },
                                "required": ["label"],
                                "additionalProperties": False,
                            }
                        },
                        "multi_select": {"type": "boolean", "description": "是否允许多选", "default": False},
                    },
                    "required": ["question", "header", "options", "multi_select"],
                    "additionalProperties": False,
                }
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }
    side_effect = False
    is_concurrency_safe = False  # interactive: pauses execution for user input
    tool_class = "read"
    effect_class = "pure_read"
    requires_interactive_user = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """防御性兜底：正常路径下本方法永不应被调用。

        run_loop 的 HITL 分支（check_hitl）在 dispatch 之前拦截
        AskUserQuestion 并暂停等待用户作答；执行到达这里即说明拦截链
        失效（装配缺失 / headless 误配置）。返回显式 error 而非假成功
        「已提问」——假成功会让模型以为用户已收到提问而带着幻觉继续
        执行（谎报比不提供更糟）；显式 error 让模型立刻改走可用的
        非交互路径。
        """
        call_id = args.get("_call_id", "")
        questions = args.get("questions", [])

        if not questions:
            return error_result(call_id, "ask_user_question：缺少 'questions' 参数")
        return error_result(
            call_id,
            "ask_user_question 进入了直接执行路径：HITL 拦截链未能暂停以获取用户输入"
            "（属于配置错误）。用户实际上并未被提问——请不要假设已获得回答；"
            "请改用可用的非交互方案推进，或明确说明你需要什么。",
        )
