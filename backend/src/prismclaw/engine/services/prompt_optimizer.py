"""Prompt optimization — rewrite a user prompt into a clearer, more executable form."""

from __future__ import annotations

import logging

from prismclaw.engine.services.llm.stream_fn import LiteLLMStreamFn
from prismclaw.schema.llm.request import PackedLLMRequest

logger = logging.getLogger(__name__)

_DEFAULT_OPTIMIZER_SYSTEM = """你是用户本人的大脑。用户的输入是他想表达的内容，但可能不够完整、清晰或具体。

请站在用户的角度，想象他真正想表达什么，并用他的口吻把话说得更完整、更清晰、更具体，就像用户自己把话说完整一样。

规则：
1. 必须严格保留用户的原始意图和核心需求，不要添加用户没提到的任务。
2. 用用户的口吻重新组织语言，使用“我想要...”“请帮我...”这样的表达方式。
3. 补充用户可能遗漏的细节：背景、约束、期望的输出格式、风格、受众等。
4. 如果用户的问题比较模糊，补充合理的假设（但要明确标注是假设）。
5. 不要使用“作为一个...”的角色设定，不要像工程师一样改写。
6. 使用简洁、无歧义的中文表达；若用户输入为英文，请用英文输出。
7. 直接输出优化后的内容，不要加任何解释。
"""


class PromptOptimizer:
    """Service that rewrites user prompts for better LLM execution."""

    def __init__(
        self,
        llm_client: LiteLLMStreamFn,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> None:
        self._llm_client = llm_client
        self._system_prompt = system_prompt or _DEFAULT_OPTIMIZER_SYSTEM
        self._temperature = temperature

    async def optimize(self, prompt: str) -> str:
        """Rewrite *prompt* into a clearer, more executable version."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is required")

        response = await self._llm_client.complete(
            request=PackedLLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system=self._system_prompt,
                context_payload=None,
                cache_strategy={},
            ),
            temperature=self._temperature,
        )
        return _strip_markdown_code_block(response.text.strip()) or prompt


def _strip_markdown_code_block(text: str) -> str:
    """Remove outer markdown fences if the model wrapped the result in a code block."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
