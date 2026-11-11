from __future__ import annotations

import asyncio
import logging
import os

from litellm import aembedding

logger = logging.getLogger(__name__)

# 单次 embedding 请求的有界超时（秒）。litellm/openai 默认超时很长、且会自动
# 重试；一旦 embeddings 服务慢/挂起，会把「记忆检索」阶段无限期堵住，进而让整个
# agent 回合卡死在检索上——连工具执行都进不去，前端因此看不到任何执行过程
# （日志表现为 retrieve_evidence entering search 之后再无结果、openai client 反复
# _sleep_for_retry）。这里强制一个上界：超时抛 TimeoutError，被上游
# `_search_mixin.search()` / `_search_l4_artifacts_core()` 的 except 捕获后降级为
# 纯文本检索，回合得以继续推进。可用环境变量 PRISMCLAW_EMBED_TIMEOUT_S 覆盖。
try:
    _DEFAULT_EMBED_TIMEOUT_S = float(os.environ.get("PRISMCLAW_EMBED_TIMEOUT_S", "15"))
except (TypeError, ValueError):  # 非法环境变量不致命，回退默认值
    _DEFAULT_EMBED_TIMEOUT_S = 15.0
if _DEFAULT_EMBED_TIMEOUT_S <= 0:
    _DEFAULT_EMBED_TIMEOUT_S = 15.0


class EmbeddingClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: str,
        dimensions: int,
        llm_provider: str | None = None,
        timeout_s: float | None = None,
        cache_size: int = 512,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._dimensions = dimensions
        self._llm_provider = llm_provider
        self._timeout_s = (
            timeout_s if timeout_s is not None and timeout_s > 0 else _DEFAULT_EMBED_TIMEOUT_S
        )
        # 查询向量是查询文本的纯函数。记忆检索每轮用相同的 task_goal 重新 embed
        # （见 memory_retriever.retrieve_evidence / _search_mixin.search），当 embeddings
        # 端点间歇性慢/5xx 时，重复 embed 会反复命中超时→整段检索降级为「无证据」。
        # 这里按文本缓存向量：成功一次后即复用，消除重复的网络调用与超时降级。
        # 上限防止 upsert 写入的大量唯一文本无限占用内存（FIFO 淘汰最旧项）。
        self._embed_cache: dict[str, list[float]] = {}
        self._embed_cache_cap = max(0, int(cache_size))

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def _request(self, texts: list[str]):
        """带硬超时的 embedding 请求：外层 asyncio.wait_for 是权威上界，
        即使 litellm 内部重试也不会超过 timeout_s。"""
        return await asyncio.wait_for(
            aembedding(
                model=self._model,
                input=texts,
                api_key=self._api_key,
                api_base=self._api_base,
                custom_llm_provider=self._llm_provider,
                encoding_format="float",
                timeout=self._timeout_s,
            ),
            timeout=self._timeout_s,
        )

    async def embed(self, text: str) -> list[float]:
        cached = self._embed_cache.get(text)
        if cached is not None:
            return list(cached)
        response = await self._request([text])
        vec = response.data[0]["embedding"]
        # 仅缓存成功结果；瞬时失败不污染后续调用。
        if self._embed_cache_cap > 0:
            self._embed_cache[text] = list(vec)
            if len(self._embed_cache) > self._embed_cache_cap:
                self._embed_cache.pop(next(iter(self._embed_cache)))
        return list(vec)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._request(list(texts))
        vecs = [item["embedding"] for item in response.data]
        # 批量结果同样按文本填充缓存，保持与 embed 一致的复用语义。
        if self._embed_cache_cap > 0:
            for text, vec in zip(texts, vecs):
                self._embed_cache[text] = list(vec)
                if len(self._embed_cache) > self._embed_cache_cap:
                    self._embed_cache.pop(next(iter(self._embed_cache)))
        return [list(v) for v in vecs]
