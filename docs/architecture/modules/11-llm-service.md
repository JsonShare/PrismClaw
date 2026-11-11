# LLM 服务

## 职责概述

LLM 服务模块封装了与大型语言模型（LLM）的所有交互，包括：

- 流式/非流式 LLM 调用（基于 litellm）
- 模型到 provider 的解析与凭据解析
- 错误分类与重试逻辑
- 上下文 payload 传输（Prompt Cache 断点等）
- SOP（标准操作流程）提取、去重与技能晋升
- Prompt 优化
- Session 导出

## 架构总览

**[查看交互式架构图](../diagrams/11-llm-service.html)**

```
┌──────────────────────────────────────────────────────────────────┐
│                     LLM 服务调用链架构                            │
│                                                                  │
│  ┌─────────────────┐                                            │
│  │     Factory     │  per-tenant LLMStreamFn 管理               │
│  │  (factory.py)   │                                            │
│  └────────┬────────┘                                            │
│           │ get_for_tenant()                                     │
│           ▼                                                      │
│  ┌──────────────────────┐                                       │
│  │  ProviderResolver    │  模型→provider 匹配                   │
│  │  (provider_resolver) │  凭据解析 + 错误分类                  │
│  └──────────┬───────────┘                                       │
│             │ resolve_credentials()                              │
│             ▼                                                    │
│  ┌──────────────────────┐                                       │
│  │   StreamFn           │  litellm 流式/非流式调用               │
│  │  (stream_fn.py)      │  重试 + 降级 + 工具参数消毒           │
│  └──────────┬───────────┘                                       │
│             │                                                    │
│             ▼                                                    │
│  ┌──────────────────────┐                                       │
│  │  PromptOptimizer     │  LLM 驱动提示词重写（温度 0.3）       │
│  │ (prompt_optimizer)   │  依赖 StreamFn 调用 LLM               │
│  └──────────────────────┘                                       │
│                                                                  │
│  ┌──────────────────────────────────────┐                       │
│  │              附属管道                │                       │
│  │  ┌──────────────┐  ┌─────────────┐   │                       │
│  │  │SOP Extraction│  │Skill        │   │                       │
│  │  │(后台提取管道)│  │Graduation   │   │                       │
│  │  │              │──│(L3→SKILL.md)│   │                       │
│  │  └──────────────┘  └─────────────┘   │                       │
│  └──────────────────────────────────────┘                       │
└──────────────────────────────────────────────────────────────────┘
```

## 核心组件

### LiteLLMStreamFn

```python
# llm/stream_fn.py
class LiteLLMStreamFn:
    # litellm-backed 实现
    async def stream()    → AsyncIterator[LLMStreamChunk]
    async def complete()  → LLMResponse

# 数据类型
@dataclass
class LLMUsage:           # input_tokens, output_tokens, cache_read
@dataclass
class LLMStreamChunk:     # delta text / tool_call / reasoning
@dataclass
class LLMResponse:        # 完整响应 + usage
class ProviderPreparedRequest:  # 预处理请求
```

关键特性：
- **重试逻辑**：3 次尝试，瞬态错误自动重试，非流式降级回退
- **上下文 payload 传输**：`ProviderRequestAdapter` 支持 none/structured_extension/legacy_bridge 三种模式
- **工具调用参数消毒**：`_sanitize_tool_call_arguments_json()` 修复畸形 JSON
- **Qwen thinking 禁用**：非 DashScope 的 OpenAI 兼容端点自动关闭 thinking 模式

### ProviderResolver

```python
# llm/provider_resolver.py
def resolve_provider_for_model()  # 模型→provider 匹配
    # 匹配策略:
    # 1. models dict 精确匹配
    # 2. 前缀匹配
    # 3. 首段匹配
    # 4. 单 provider 回退
    # 5. unknown_prefix_policy 处理

def classify_error()              # 异常→LLMErrorCode 映射
    # CONTEXT_OVERFLOW / RATE_LIMIT / AUTH_ERROR / CONNECTION_ERROR / TIMEOUT

def resolve_credentials()         # 返回 (api_key, api_base, provider_name, llm_provider)

def model_supports_input()        # 检查模型是否支持特定输入类型
def messages_have_user_image_content()  # 检测消息是否包含图像
def format_vision_capable_models()      # 格式化视觉模型列表
def is_anthropic_model()                # 判断是否为 Anthropic 模型
```

### LLMStreamFnFactory

```python
# llm/factory.py
class LLMStreamFnFactory:
    # per-tenant LLMStreamFn 管理
    def get_for_tenant()      # 获取或创建 tenant 的 stream fn
    def register_tenant_fn()  # 注册自定义 stream fn
```

### PromptOptimizer

```python
# prompt_optimizer.py
class PromptOptimizer:
    # 通过 LLM 重写用户提示词，提升清晰度
    # temperature = 0.3
    # _strip_markdown_code_block(): 移除外围代码围栏
```

### SOP 提取管道

```python
# sop_extraction.py
async def extract_sop_background()   # 完整管道: Redis 候选 → session tree → LLM 提取 → 去重 → L3 写入
def maybe_spawn_extraction()         # 条件式后台启动，SETNX 分布式锁
async def extract_sops_sync()        # 同步变体（/extract 命令用）

def _is_duplicate()                  # FTS5 搜索 + Jaccard 确认去重
async def _call_llm_with_retry()     # 结构化工具调用优先，文本 JSON 降级，2 次尝试
```

```python
# _sop_parse.py
def _build_segments()          # 遍历 SessionTree 构建候选 turn 的文本段
def _parse_llm_output()        # 处理 markdown 围栏、prose 中的 JSON、对象包装器
def _validate_sop()            # schema 验证 + 危险模式检测（PII/credentials/external URLs）
def _jaccard_overlap()         # 基于 jieba.cut_for_search 的 token 重叠度
def _tool_result_preview()     # 2000 字符预览，支持持久化输出
```

```python
# _sop_types.py
class ExtractionResult:
    spawned: bool
    skip_reason: str | None
    llm_returned_count: int
    written: int
    skipped_duplicate: int
    skipped_invalid: int
    error: str | None
    rejection_reasons: list[str]

def format_extraction_result_zh()  # 中文用户面向的提取结果消息
```

### 技能晋升

```python
# skill_graduation.py
async def graduate_single_sop()          # L3 SOP → SKILL.md 晋升
    # 自动生成生命周期管理
    # 备份前一版本

def generate_skill_md_template()         # 纯模板生成
async def generate_skill_md_enrich()     # LLM 丰富 SKILL.md（降级到模板）

def parse_sop_content()                  # 解析 "name\ndescription\nprocedure" 格式
```

### 错误码体系

```python
# schema/llm/errors.py
class LLMErrorCode:
    CONTEXT_OVERFLOW    # 上下文溢出
    RATE_LIMIT          # 速率限制
    AUTH_ERROR          # 认证错误
    CONNECTION_ERROR    # 连接错误
    TIMEOUT             # 超时
```

## 关键数据流

### LLM 流式调用流

```
AgentInstance / ChatService
  → LiteLLMStreamFn.stream(messages, tools, ...)
    → resolve_provider_for_model(model)
      → models dict 精确匹配 / 前缀匹配 / 首段匹配 / 单 provider 回退
    → resolve_credentials(provider, model)
      → (api_key, api_base, provider_name, llm_provider)
    → ProviderRequestAdapter.prepare(messages, ...)
      → 注入上下文 payload（Prompt Cache 断点）
    → litellm.acompletion(stream=True, ...)
    → _convert_chunk() 转换每个 chunk
    → _extract_usage() 提取 usage 信息
  → yield LLMStreamChunk
```

### 重试与降级流

```
LiteLLMStreamFn.stream()
  → 第 1 次尝试: stream=True
    → 瞬态错误 (RATE_LIMIT/CONNECTION_ERROR/TIMEOUT)
      → 第 2 次尝试: stream=True
        → 第 3 次尝试: stream=False（非流式降级）
    → 非瞬态错误 (CONTEXT_OVERFLOW/AUTH_ERROR)
      → 直接抛出
```

### SOP 提取流

```
maybe_spawn_extraction()
  → SETNX 分布式锁（Redis）
  → extract_sop_background()
    → Redis 候选列表
    → 遍历 SessionTree（_build_segments）
    → _call_llm_with_retry()
      → 优先: 结构化工具调用
      → 降级: 文本 JSON 解析（_parse_llm_output）
    → _validate_sop()
      → schema 验证 + 危险模式检测
    → _is_duplicate()
      → FTS5 搜索 → Jaccard 确认（_jaccard_overlap，jieba 分词）
    → 写入 L3 SOP 文件
    → ExtractionResult 统计
```

### 技能晋升流

```
graduate_single_sop(l3_sop_path)
  → parse_sop_content()（解析 name/description/procedure）
  → generate_skill_md_enrich()
    → LLM 丰富（降级到 generate_skill_md_template）
  → 备份前一版本 SKILL.md
  → 写入新 SKILL.md
```

### Provider 解析流

```
resolve_provider_for_model(model)
  1. models dict 精确匹配 → 返回
  2. 遍历前缀匹配 → 返回
  3. 首段（第一个 `-` 前的部分）匹配 → 返回
  4. 仅一个 provider → 单 provider 回退
  5. unknown_prefix_policy 处理
     → "raise" → 抛出错误
     → "fallback" → 回退到默认 provider
```

## 对外接口

| 接口 | 类型 | 说明 |
|------|------|------|
| `LiteLLMStreamFn.stream()` | 方法 | 流式 LLM 调用 |
| `LiteLLMStreamFn.complete()` | 方法 | 非流式 LLM 调用 |
| `resolve_provider_for_model()` | 函数 | 模型→provider 解析 |
| `classify_error()` | 函数 | 异常→错误码分类 |
| `resolve_credentials()` | 函数 | 凭据解析 |
| `LLMStreamFnFactory.get_for_tenant()` | 方法 | per-tenant stream fn |
| `PromptOptimizer` | 类 | LLM 提示词重写 |
| `extract_sop_background()` | 函数 | SOP 后台提取管道 |
| `maybe_spawn_extraction()` | 函数 | 条件式提取启动 |
| `extract_sops_sync()` | 函数 | 同步 SOP 提取 |
| `graduate_single_sop()` | 函数 | L3→SKILL.md 晋升 |
| `LLMError` / `LLMErrorCode` | 类 | 错误类型与码 |

## 依赖关系

```
LLM 服务模块
  ├── litellm                    — LLM 统一调用层
  ├── jieba                      — Jaccard 去重分词
  ├── persistence.session        — SessionTree 加载
  ├── persistence.lock           — SETNX 分布式锁
  ├── infra.redis                — 候选追踪、分布式锁
  ├── infra.settings             — LLM/模型配置
  ├── schema.session             — SessionTree, SessionStore
  ├── schema.config              — 模型/Provider 配置
  ├── engine.skills              — 技能运行时（SOP 晋升目标）
  └── engine.security            — PII/凭据检测（SOP 验证）
```

## 配置项

| 配置 | 位置 | 说明 | 默认值 |
|------|------|------|--------|
| LiteLLM 重试次数 | `stream_fn.py` | 瞬态错误重试 | `3` |
| PromptOptimizer temperature | `prompt_optimizer.py` | 重写温度 | `0.3` |
| `_call_llm_with_retry` 次数 | `sop_extraction.py` | SOP 提取 LLM 重试 | `2` |
| `_tool_result_preview` 长度 | `_sop_parse.py` | 工具结果预览字符数 | `2000` |
| `resolve_provider_for_model` unknown_prefix_policy | `provider_resolver.py` | 未知前缀策略 | — |
| models dict | 配置 | 模型→provider 映射 | — |
| provider prefixes | 配置 | 前缀→provider 映射 | — |
