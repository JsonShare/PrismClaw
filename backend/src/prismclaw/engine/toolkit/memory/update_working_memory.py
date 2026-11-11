from __future__ import annotations

import logging
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult

logger = logging.getLogger(__name__)


class UpdateWorkingMemoryTool:
    name = "update_working_memory"
    description = (
        "向按会话隔离的短期工作记忆写入一个键值对。"
        "可用它在当前会话内记住事实、决策或上下文。"
        "所有条目合计上限 1024 字符，超出时会淘汰最早的条目。"
        "条目在连续 7 天无活动后失效。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "该记忆条目的简短标识"},
            "value": {"type": "string", "description": "要记住的值"},
        },
        "required": ["key", "value"],
        "additionalProperties": False,
    }
    side_effect = True
    # Writes to per-session Redis memory only — never touches the workspace,
    # and the memory subsystem has its own size/TTL guards, so it is
    # concurrency-safe and allowed in read-only chat mode.
    tool_class = "memory-write-safe"
    effect_class = "local_mutation"
    visibility_scope = "main_only"

    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "prismclaw:wm:",
        max_chars: int = 1024,
        ttl_seconds: int = 604800,
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._max_chars = max_chars
        self._ttl_seconds = ttl_seconds

    def _hash_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def _order_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}:order"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        try:
            key = args.get("key", "")
            value = args.get("value", "")
            if not key or not isinstance(key, str):
                return error_result(
                    call_id, "update_working_memory：'key' 必须是非空字符串"
                )
            if not isinstance(value, str):
                return error_result(call_id, "update_working_memory：'value' 必须是字符串")

            hash_key = self._hash_key(context.session_id)
            order_key = self._order_key(context.session_id)

            existing = await self._redis.hget(hash_key, key)
            await self._redis.hset(hash_key, key, value)

            if existing is None:
                await self._redis.rpush(order_key, key)

            await self._evict_if_needed(hash_key, order_key)

            await self._redis.expire(hash_key, self._ttl_seconds)
            await self._redis.expire(order_key, self._ttl_seconds)

            return text_result(call_id, f"已将 '{key}' 写入工作记忆")
        except Exception as exc:
            return error_result(call_id, f"update_working_memory：{exc}")

    async def _evict_if_needed(self, hash_key: str, order_key: str) -> None:
        all_fields = await self._redis.hgetall(hash_key)
        total_chars = sum(len(k) + len(v) for k, v in all_fields.items())

        while total_chars > self._max_chars:
            oldest = await self._redis.lpop(order_key)
            if oldest is None:
                break
            if oldest in all_fields:
                total_chars -= len(oldest) + len(all_fields[oldest])
                del all_fields[oldest]
                await self._redis.hdel(hash_key, oldest)
