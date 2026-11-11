# 07 — 上下文压缩

## 职责概述

上下文压缩系统负责在 Agent 运行过程中管理 token 预算、组装上下文、压缩历史与工具结果，确保 LLM 始终在有效的上下文窗口内运行。它通过分层预算编排、语义保真校验、自适应压缩策略和预测性触发，在信息保留与空间效率之间取得平衡。

核心能力：
- **上下文组装**：意图驱动的检索配额分配 + 记忆格式化 + 证据图构建
- **预算编排**：静态/动态池硬隔离 + 值排序选择 + 溢出逐级披露（持久化→丢弃）
- **历史压缩**：LLM 摘要 + 分块 staging + 确定性 carry-forward + Fact Sheet 注入
- **工具结果压缩**：材质检测 + 语义压缩 + 磁盘持久化 + per-result LLM 摘要
- **语义保真**：PreservationContract + SemanticCapsule + 不变量验证
- **压缩编排**：circuit breaker + 预测性触发 + soft budget 降级
- **Section Retention**：preflight / snapshot / unchanged 三态生命周期管理

## 架构总览

**[查看交互式架构图](../diagrams/07-context-compression.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                     上下文压缩系统总览                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                DefaultContextEngine                        │   │
│  │  assemble() / compact() / ingest() / after_turn()         │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │ 委托                               │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               ContextServiceBundle                         │   │
│  │  一站式组装所有子服务                                        │   │
│  │                                                            │   │
│  │  ┌─────────────────────┐    ┌──────────────────────────┐  │   │
│  │  │ AssembleCoordinator │    │     BudgetOrchestrator    │  │   │
│  │  │ 上下文组装协调       │───→│ 静态池/动态池/结果池/    │  │   │
│  │  │ 意图驱动检索配额     │    │ 历史池 值排序+逐级披露   │  │   │
│  │  └─────────┬───────────┘    └──────────────────────────┘  │   │
│  │            │                                               │   │
│  │            ▼                                               │   │
│  │  ┌──────────────────────────────────────────────────────┐ │   │
│  │  │               Compaction 管线                         │ │   │
│  │  │                                                      │ │   │
│  │  │  ┌───────────────────┐  ┌──────────────────────────┐ │ │   │
│  │  │  │ Compression       │  │  HistoryCompactor        │ │ │   │
│  │  │  │ Orchestrator      │  │  LLM摘要+去重+标识符保持 │ │ │   │
│  │  │  │ CB+预测触发+SDD   │──→│  PreservationContract   │ │ │   │
│  │  │  └───────────────────┘  └──────────────────────────┘ │ │   │
│  │  │                                                      │ │   │
│  │  │  ┌───────────────────┐  ┌──────────────────────────┐ │ │   │
│  │  │  │ ResultCompaction  │  │  SummaryEngine           │ │ │   │
│  │  │  │ 材质检测+语义压缩 │  │  staged分块摘要+超时保护 │ │ │   │
│  │  │  │ per-result LLM摘要│  │  fallback无LLM摘要       │ │ │   │
│  │  │  └───────────────────┘  └──────────────────────────┘ │ │   │
│  │  └──────────────────────────────────────────────────────┘ │   │
│  │                                                            │   │
│  │  ┌───────────────────────┐  ┌──────────────────────────┐  │   │
│  │  │ Semantic Fidelity     │  │  SessionStateStore       │  │   │
│  │  │ PreservationContract  │  │  会话级状态持久化         │  │   │
│  │  │ SemanticCapsule       │  │  ContextBlock构建         │  │   │
│  │  │ 不变量验证             │  │  CompactionCheckpoint    │  │   │
│  │  └───────────────────────┘  └──────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## 核心组件

### DefaultContextEngine（`engine/compression/context_engine.py`）

上下文引擎的顶层入口，通过 DI 容器组装各子服务。

- **类**：`DefaultContextEngine`
- **核心方法**：
  - `assemble()` — 组装上下文（委托给 AssembleCoordinator）
  - `compact()` — 压缩历史（委托给 HistoryCompactor）
  - `ingest()` — 摄入消息（委托给 StateContextIngestor）
  - `after_turn()` — 回合后处理（状态重建 + 记忆 after_turn）
  - `consolidate_session()` — 合并会话
  - `get_bootstrap()` — 获取引导上下文
  - `get_l1_snapshot()` — 获取 L1 快照
  - `get_session_state()` — 获取会话状态
  - `set_result_compaction_config()` — 设置结果压缩配置
  - `set_session_store()` — 设置会话存储
- **构造方式**：`DefaultContextEngine` 直接实例化（`ContextEngineBuilder` / `DIContainer` 已移除，见 `context_engine.py` 头注释），子服务由 `build_context_service_bundle()` 一次组装

### ContextServiceBundle（`engine/compression/context_bundle.py`）

上下文引擎的子服务集合。

- **数据类**：`ContextServiceBundle`
  - `bootstrap_provider`：BootstrapProvider
  - `memory_retriever`：MemoryRetriever
  - `retrieval_audit_log`：RetrievalAuditLogger | None
  - `state_store`：SessionStateStore
  - `assemble_coordinator`：AssembleCoordinator
  - `summary_engine`：SummaryEngine
  - `history_compactor`：HistoryCompactor
  - `context_ingestor`：ContextIngestor
  - `edge_store`：Any
- **构建函数**：`build_context_service_bundle()` — 一站式组装所有子服务
- **Ingestor**：
  - `NoopContextIngestor` — 空实现
  - `StateContextIngestor` — 委托给 SessionStateStore.ingest_message()

### AssembleCoordinator（`engine/compression/assemble_coordinator.py`）

上下文组装协调器，编排完整的组装流程。

- **类**：`AssembleCoordinator`
- **核心方法**：
  - `assemble()` — 完整组装流程
  - `set_compaction_config()` — 设置压缩配置
  - `set_session_store()` — 设置会话存储
- **内部方法**：
  - `_ensure_session_state_loaded()` — 确保会话状态已加载
- **组装流程**：
  1. 加载持久化制品 + 历史压缩制品 + 持久化记忆状态
  2. 构建 anchor blocks（memory_block + state_block + history_block）
  3. 推断初始 TaskProfile
  4. 构建 EvidenceStrategy
  5. 检索 L2/L3 + archive 证据
  6. 构建 ContextGraph（evidence_runtime）
  7. 格式化记忆块
  8. BudgetOrchestrator 值排序选择
  9. 结果压缩（compact_context_blocks_for_turn）
  10. 动态预算应用
  11. ContextValidatorChain 验证
- **DynamicContextRouter**：意图驱动的配额分配
  - `allocate_quotas()` — 返回 (l2_quota, l3_quota, archive_limit)
  - analyze 模式：L2+3, archive+2, L3-1
  - execute/edit/create 模式：L3+3, L2-1, archive-1

### BudgetOrchestrator（`engine/compression/budget_orchestrator.py`）

预算编排器，管理静态/动态池的 token 分配与溢出处理。

- **类**：`BudgetOrchestrator`
- **核心方法**：
  - `select_non_static_blocks()` — 值排序选择非静态块
  - `apply_context_layout_budgets()` — 应用上下文布局预算
  - `apply_dynamic_budget()` — 应用动态预算
  - `assess_history_compaction()` — 评估历史压缩需求
- **数据类**：
  - `BudgetDecision`（stage, action, reason, zone, block_id, source, before_tokens, after_tokens）
  - `BudgetAuditTrail`（decisions 列表 + 统计方法）
  - `BudgetApplicationResult`（context_layout + audit_trail）
  - `HistoryCompactionAssessment`（should_compact, trigger_budget, threshold_override, ...）
- **静态池截断优先级**：tools → skills → workspace → repository_map → bootstrap → l1_snapshot → subagent_advisory → invoked_skill_attachments
- **动态池截断优先级**：hooks_prepend → hooks_append → memory_archives → memory_procedures → subagent_advisory → attachment_guidance → memory_facts → runtime_context
- **逐级披露**：`truncate_context_block_for_overflow()` — 持久化→替换为引用→丢弃
- **值排序**：`_context_selection_sort_key()` — (value_category, grounding_required, priority_rank, replaceable, tokens)

### HistoryCompactor（`engine/compression/history_compactor.py`）

历史压缩器，将超出预算的对话历史摘要化。

- **类**：`HistoryCompactor`
- **核心方法**：`compact()`
- **压缩流程**：
  1. 检查 abort_event
  2. `has_real_conversation()` — 过滤空对话
  3. `dedupe_duplicate_user_messages()` — 去重
  4. `build_compaction()` — 构建压缩策略（cut_point）
  5. `strip_tool_result_details()` — 剥离工具结果详情
  6. `filter_oversized_messages()` — 过滤超大消息
  7. `SummaryEngine.summarize_messages()` — LLM 摘要
  8. `extract_identifiers()` — 提取技术标识符（路径/哈希）
  9. 构建 PreservationContract + SemanticCapsule
  10. `validate_semantic_capsule()` — 语义保真验证
  11. 返回 CompactResult

### CompressionOrchestrator（`engine/compression/orchestrator.py`）

压缩编排器，管理压缩触发决策、circuit breaker 与预测性触发。

- **类**：`CompressionOrchestrator`
- **核心方法**：`prepare_turn()` — 回合前压缩准备
- **数据类**：`CompressionPreparation`（action, execution_mode, preflight, history_assessment, ...）
- **执行模式**：`must_block` / `may_defer`
- **Circuit Breaker**：
  - `_circuit_breaker_threshold` — 连续失败阈值（默认 3）
  - `_on_failure()` / `_on_recovery()` — 状态转换
  - `compression.orchestrator.circuit_breaker.failures/tripped/recovered` 指标
- **预测性压缩**（v4.0）：
  - `predict_compaction_trigger()` — 基于 p75 增量率预测触发
  - 三层阈值：Predictive（75%）< Emergency（90%）< Passive（100%）
  - `recent_history_tokens` 快照（最多 20 个）

### History Compaction（`engine/compression/history_compaction.py`）

历史压缩的完整执行逻辑，包含 cooldown、checkpoint、carry-forward。

- **核心函数**：
  - `try_compaction()` — 尝试压缩（含 circuit breaker + hook 通知 + PTL 重试）
  - `maybe_pretrim_history()` — 条件触发压缩（含 cooldown 保护）
  - `recover_from_context_overflow()` — 溢出恢复（force=True）
  - `predict_compaction_trigger()` — 预测性触发
- **数据类**：`CompactionOutcome`（ok, compacted, error_code, error_message, result）
- **Cooldown**：`_resolve_min_interval_entries()` — 最小间隔条目数
- **Carry-forward**（C-1）：`carry_forward_missing_sections()` — 确定性章节前向传递
  - `_CARRY_FORWARD_SECTION_NUMBERS = {7}` — 必须前传的章节号
  - `_VOIDED_MARKERS` — voided/superseded/cancelled/retracted/replaced
- **9-Section 格式**：`extract_sections()` / `_section_is_substantive()` / `_is_voided_section()`
- **Fact Sheet 注入**：`_query_messages_for_compaction()` — 压缩时注入不可变事实层
- **Autocompact Breaker**：`AutocompactBreaker`（is_open/increment/reset）

### Result Compaction（`engine/compression/result_compaction.py`）

工具结果压缩器，管理 per-result 的材质检测与压缩策略。

- **核心函数**：
  - `compact_tool_results_for_turn()` — 同步回合压缩
  - `compact_tool_results_for_turn_async()` — 异步回合压缩（含 LLM 摘要）
  - `compact_context_blocks_for_turn()` — ContextBlock 压缩
- **数据类**：
  - `ResultCompactionArtifact`（tool_call, result, material_type, strategies, ...）
  - `ResultCompactionTurn`（artifacts, aggregate_budget_chars, replacement_state）
  - `ContextBlockCompactionArtifact`（block, material_type, strategies, ...）
- **压缩策略链**：
  - `per_result_persist` — 磁盘持久化
  - `semantic_compress` — 语义压缩
  - `per_result_llm_summarize` — per-result LLM 摘要
  - `aggregate_budget_compact` — 聚合预算压缩
  - `reference_preview` — 引用预览
  - `frozen_reference_reuse` — 冻结引用复用
- **材质类型**（`ResultMaterialType`，Literal 类型别名）：command_output / retrieval_list / long_web_page / document_extraction / structured_dataset_preview / generic_text
- **ReplacementDecision**：每次压缩记录替换决策，支持冻结重放

### SummaryEngine（`engine/compression/summary_engine.py`）

摘要引擎，管理 LLM 摘要的 staged 执行与超时保护。

- **类**：`SummaryEngine`
- **核心方法**：`summarize_messages()`
- **内部方法**：`_run_summarizer_guarded()`
- **Staged 摘要**：
  - `split_into_chunks()` — 按 chunk_token_budget 分块
  - `summarize_in_stages()` — 多阶段摘要（每块独立摘要 → 合并摘要）
- **异常**：`CompactionTimeout` / `CompactionAborted`
- **回退**：`fallback_summary()` — 无 LLM 时的结构化摘要
- **原因码分类**：`classify_compaction_reason()` → CompactionReasonCode
  - compacted / no_compactable_entries / below_threshold / already_compacted_recently / summary_failed / timeout / aborted / provider_error_4xx / provider_error_5xx / unknown

### Semantic Fidelity（`engine/compression/semantic_fidelity.py`）

语义保真系统，确保压缩不丢失关键不变量。

- **核心函数**：
  - `build_preservation_contract()` — 构建保持契约
  - `build_semantic_capsule()` — 构建语义胶囊
  - `validate_semantic_capsule()` — 验证胶囊（检查 required invariants）
  - `fidelity_metadata()` — 保真元数据
- **数据结构**：PreservationContract + SemanticCapsule + CompressionValidationResult
- **不变量类型**：identifier / fact / constraint / decision / question
- **提取**：`_extract_identifiers()` — 正则提取路径和哈希
- **验证**：required invariants 必须出现在 summary 中

### Hardening（`engine/compression/hardening.py`）

压缩硬化，包含标识符保持指令、分块摘要与 carry-forward。

- **关键常量**：`IDENTIFIER_PRESERVATION_INSTRUCTIONS` — 标识符保持指令
- **关键常量**：`HARDENED_SUMMARIZER_SYSTEM_PROMPT` — 加固摘要提示
  - 包含 W8（用户事实必须存活）和 W12（已废弃需求必须作为 voided 存活）
- **核心函数**：
  - `has_real_conversation()` — 检测真实对话
  - `strip_tool_result_details()` — 剥离工具结果 details 字段
  - `filter_oversized_messages()` — 过滤超大消息
  - `split_into_chunks()` — 按 token 预算分块
  - `summarize_in_stages()` — 多阶段摘要
  - `carry_forward_missing_sections()` — 确定性章节前传（C-1）
  - `extract_sections()` — 提取 9-Section 编号段落
  - `sanity_check_token_estimate()` — token 估算合理性检查

### Section Retention（`engine/compression/retention.py`）

系统提示段保留策略，管理压缩前后的段生命周期。

- **数据类**：`SectionRetention`
  - `source`：preflight / snapshot / unchanged
  - `lifecycle_phase`：active / durable / None
  - `retain_all_dynamic_sections`：bool
  - `retained_dynamic_section_names`：tuple[str, ...]
- **工厂方法**：
  - `unchanged()` — 不变更
  - `active_from_preflight()` — 从 preflight 激活（保留所有动态段）
  - `durable_static()` — 持久静态（仅静态段）
  - `durable_with_dynamic_sections()` — 持久 + 指定动态段
- **核心函数**：`apply_section_retention()` — 应用保留策略

### TaskProfileInference（`engine/compression/task_profile_inference.py`）

任务画像推断，从对话内容推断执行模式与材质类型。

- **类**：`TaskProfileInference`
- **核心方法**：
  - `infer(prompt, messages, memory_blocks)` — 一次性推断 `TaskProfile`（含 execution_mode）
- **执行模式推断规则**（`infer` 内部）：
  - tool_turn_seen → "execute"
  - edit/modify/fix/... → "edit"
  - execute/run/apply/... → "execute"
  - create/write/draft/... → "create"
  - review/analyze/why/... → "analyze"
  - 含"?"且无文档/表格 → "answer"
  - requires_grounding → "analyze"
  - 默认 → "mixed"
- **材质检测**：tool_result / code / image / document / text / retrieval_result

### SessionStateStore（`engine/compression/state_store.py`）

会话状态存储，管理会话级的状态持久化与 ContextBlock 构建。

- **类**：`SessionStateStore`
- **核心方法**：
  - `get_state()` / `get_existing_state()` — 获取状态
  - `hydrate_state()` — 从外部补充状态
  - `ingest_message()` — 摄入消息
  - `rebuild_from_messages()` — 从消息重建
  - `build_memory_block()` — 构建记忆 ContextBlock（priority=critical, truncatable=False）
  - `build_context_block()` — 构建上下文 ContextBlock（priority=high, truncatable=True, delta diff）
- **辅助类**：`CompactionCheckpoint` / `take_checkpoint()` — 压缩检查点与回滚

### AssemblyStateBundle（`engine/compression/assembly_state.py`）

组装状态集，管理检索查询的构建。

- **数据类**：`AssemblyStateBundle`（anchor_blocks, merged_state_payload, retrieval_query, anchor_sources）
- **构建函数**：`build_assembly_state_bundle()`
- **检索查询构建**：`build_state_driven_retrieval_query()`
  - 从 task_spec + prompt + state_payload 构建
  - 包含：task_goal, task_mode, primary_goal, decisions, todos, constraints, open_questions, facts, recent_messages
  - `_MAX_SECTION_ITEMS = 4`、`_MAX_RECENT_MESSAGES = 3`

### Evidence Runtime（`engine/compression/evidence_runtime.py`）

证据图构建器，将检索结果组装为 ContextGraph。

- **核心函数**：`build_context_graph()`
- **图结构**：ContextGraph（packets + edges + metadata）
- **边类型**：anchors / supports / supersedes / derived_from / related_to
- **辅助函数**：`context_graph_metadata()` — 图摘要元数据

### Preservation（`engine/compression/preservation.py`）

压缩后技能附件保持与运行时状态同步。

- **核心函数**：
  - `sync_prompt_inputs_active_skill()` — 同步技能激活状态
  - `project_preservation_runtime()` — 投影保持运行时
  - `apply_section_retention_outcome()` — 应用段保留结果
  - `project_active_skill_invocation()` — 投影技能调用
  - `cleanup_preservation_runtime_on_terminal_exit()` — 终端退出清理
  - `build_invoked_skill_attachment()` — 构建技能附件
- **常量**：`INVOKED_SKILL_ATTACHMENT_MAX_CHARS = 24_000` — 技能附件字符上限

### BootstrapProvider（`engine/compression/bootstrap.py`）

引导上下文提供器，加载系统指令文件。

- **类**：`BootstrapProvider`
- **核心方法**：`get_bootstrap()`
- **默认文件列表**：/etc/prismclaw/AGENTS.md, ~/.prismclaw/AGENTS.md, .prismclaw/AGENTS.md, AGENTS.md, prismclaw.md, prismclaw.local.md 等
- **常量**：`BOOTSTRAP_FILE_WARN_BYTES = 10_240`

### Fact Sheet（`engine/compression/fact_sheet.py`）

结构化事实层，永不压缩、永不丢弃。

- **核心函数**：
  - `load_fact_sheet()` — 从 SessionTree 加载事实表
  - `save_fact_sheet()` — 增量保存事实表（只保存 diff）
  - `render_fact_sheet_text()` — 渲染为可读文本（含 P3-FUS-01 脱敏）
  - `extract_facts_from_message()` — LLM 提取结构化事实
- **VOID 语义**：值为 "VOID" 的键从事实表中移除
- **LLM 提取**：`_FACT_EXTRACTION_SYSTEM` 提示词 + JSON 输出约束

### Digest（`engine/compression/digest.py`）

确定性摘要，用于缓存键与变更检测。

- **核心函数**：
  - `canonicalize_for_digest()` — 规范化（排除时间戳等不稳定键）
  - `canonical_json_dumps()` — 规范化 JSON
  - `digest_hex()` — SHA 摘要
  - `build_prepared_turn_digest()` — 构建回合摘要
  - `build_tool_pool_id()` — 构建工具池 ID
  - `build_replacement_state_digest()` — 构建替换状态摘要

### Dedup（`engine/compression/dedup.py`）

消息去重器，在压缩前消除重复用户消息。

- **核心函数**：
  - `normalize_for_dedup()` — NFC 规范化 + 空白折叠 + 小写
  - `dedupe_duplicate_user_messages()` — 时间窗口去重
- **常量**：
  - `DEFAULT_WINDOW_SECONDS = 60.0` — 去重时间窗口
  - `DEFAULT_MIN_CHARS = 24` — 最小去重字符数

## 关键数据流

### 1. 上下文组装流（Assemble）

```
DefaultContextEngine.assemble()
  → AssembleCoordinator.assemble()
    → _ensure_session_state_loaded() (加载持久化状态)
    → SessionStateStore.build_memory_block() + build_context_block()
    → build_assembly_state_bundle() (构建检索查询)
    → TaskProfileInference.infer() (推断任务画像)
    → MemoryRetriever.build_evidence_strategy() + retrieve_evidence()
    → build_context_graph() (构建证据图)
    → MemoryFormatter.format_blocks_from_packets()
    → BudgetOrchestrator.select_non_static_blocks() (值排序)
    → compact_context_blocks_for_turn() (结果压缩)
    → BudgetOrchestrator.apply_dynamic_budget() (动态预算)
    → ContextValidatorChain.validate_and_transform()
    → 返回 AssembleResult
```

### 2. 历史压缩流（Compact）

```
DefaultContextEngine.compact()
  → HistoryCompactor.compact()
    → dedupe_duplicate_user_messages()
    → build_compaction() (计算 cut_point)
    → strip_tool_result_details() + filter_oversized_messages()
    → SummaryEngine.summarize_messages()
      → split_into_chunks() → summarize_in_stages()
    → extract_identifiers() (技术标识符保持)
    → build_preservation_contract() + build_semantic_capsule()
    → validate_semantic_capsule() (语义保真验证)
    → 返回 CompactResult
```

### 3. 压缩编排流（CompressionOrchestrator）

```
CompressionOrchestrator.prepare_turn()
  → preflight (prepare_turn_callable)
  → _build_compaction_policy() (构建压缩策略)
  → BudgetOrchestrator.assess_history_compaction() (评估)
  → [预测性触发] predict_compaction_trigger()
    → 三层: Predictive(75%) < Emergency(90%) < Passive(100%)
  → [Circuit Breaker] 连续失败 ≥ threshold → 跳过
  → maybe_pretrim_history() (条件触发)
    → [Cooldown] min_interval_entries 检查
    → try_compaction() (含 PTL 重试 + hook 通知)
    → [Carry-forward] carry_forward_missing_sections()
    → [Soft Budget] evaluate_soft_budget()
  → 返回 CompressionPreparation
```

### 4. 工具结果压缩流

```
compact_tool_results_for_turn() / compact_tool_results_for_turn_async()
  → 对每个 result:
    → _materialize_single_result() (持久化决策)
    → _semantic_compact_text() (语义压缩)
    → [async] run_per_result_fork_agent() (per-result LLM 摘要)
  → 聚合预算检查
  → _aggressively_compact() (超预算时激进压缩)
    → _persist_reference() 或 truncate_tool_result()
  → _attach_compaction_metadata()
  → ReplacementDecision 记录
  → 返回 ResultCompactionTurn
```

## 对外接口

| 接口 | 来源 | 说明 |
|------|------|------|
| `DefaultContextEngine.assemble()` | `context_engine.py` | 上下文组装 |
| `DefaultContextEngine.compact()` | `context_engine.py` | 历史压缩 |
| `DefaultContextEngine.ingest()` | `context_engine.py` | 消息摄入 |
| `DefaultContextEngine.after_turn()` | `context_engine.py` | 回合后处理 |
| `DefaultContextEngine.consolidate_session()` | `context_engine.py` | 会话合并 |
| `AssembleCoordinator.assemble()` | `assemble_coordinator.py` | 组装协调 |
| `BudgetOrchestrator.assess_history_compaction()` | `budget_orchestrator.py` | 历史压缩评估 |
| `CompressionOrchestrator.prepare_turn()` | `orchestrator.py` | 压缩准备 |
| `HistoryCompactor.compact()` | `history_compactor.py` | 历史压缩 |
| `SummaryEngine.summarize_messages()` | `summary_engine.py` | LLM 摘要 |
| `compact_tool_results_for_turn()` | `result_compaction.py` | 工具结果压缩 |
| `compact_context_blocks_for_turn()` | `result_compaction.py` | ContextBlock 压缩 |
| `build_preservation_contract()` | `semantic_fidelity.py` | 保真契约 |
| `validate_semantic_capsule()` | `semantic_fidelity.py` | 语义保真验证 |
| `carry_forward_missing_sections()` | `hardening.py` | 章节前传 |
| `BootstrapProvider.get_bootstrap()` | `bootstrap.py` | 引导上下文 |
| `load_fact_sheet()` | `fact_sheet.py` | 加载事实表 |
| `extract_facts_from_message()` | `fact_sheet.py` | LLM 事实提取 |

## 依赖关系

- **LLM Client**：SummaryEngine 摘要、per-result LLM 摘要、Fact Sheet 提取
- **MemoryStore**：记忆检索（L2/L3/L4）
- **SessionStore**：会话级持久化（SessionTree + CompactionEntry）
- **WorkspaceStore**：引导文件加载
- **EdgeStore**：证据图构建
- **Redis**：记忆检索缓存、归档缓存
- **CompressionRuntimeState**：运行时状态（circuit breaker、预测快照、breaker 状态）
- **ContextValidatorChain**：上下文安全验证
- **SandboxPolicy**：MCP sandbox 集成
- **jieba**：中文分词（Attribution）

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `threshold` | DEFAULT_THRESHOLD | 压缩触发阈值（占上下文窗口比例） |
| `keep_recent_tokens` | DEFAULT_KEEP_RECENT_TOKENS | 保留最近 token 数 |
| `compaction_timeout_s` | DEFAULT_COMPACTION_SAFETY_TIMEOUT_S | 压缩安全超时 |
| `chunk_token_budget` | 8000 | 分块摘要 token 预算 |
| `system_budget` | — | 静态池 token 预算 |
| `dynamic_budget` | — | 动态池 token 预算 |
| `result_budget` | — | 结果池 token 预算 |
| `history_budget` | — | 历史池 token 预算 |
| `circuit_breaker_threshold` | 3 | 连续失败阈值 |
| `predictive_horizon` | 3 | 预测性触发前瞻轮次 |
| `predictive_min_samples` | 4 | 预测最小样本数 |
| `emergency_ratio` | 0.90 | 紧急触发比率 |
| `min_interval_entries` | 0 | 压缩冷却间隔（条目数） |
| `enable_autocompact_breaker` | True | 自动压缩断路器开关 |
| `enable_soft_budget` | True | 软预算降级开关 |
| `invoked_skill_attachment_max_chars` | 24000 | 技能附件字符上限 |
| `bootstrap_file_warn_bytes` | 10240 | 引导文件大小警告阈值 |
| `archive_cache_max_entries` | 200 | 归档缓存最大条目数 |
