# 04 — 沙箱安全 (Sandbox & Security)

> 源码目录：`backend/src/prismclaw/engine/security/`

## 职责概述

沙箱安全系统为 PrismClaw 提供多层防御体系，覆盖工具调用的全生命周期安全管控。从进程级沙箱隔离（bwrap）到语义防火墙，从静态规则门禁到动态风险分级，从 PHI 数据防泄漏到 WORM 合规审计，构成纵深防御架构。核心设计原则是 **fail-closed**：未注册工具走最严分类，未配置安全组件默认拒绝，不存在"跳过所有检查"的路径。

## 架构总览

**[查看交互式架构图](../diagrams/04-sandbox-security.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                   Sandbox & Security 总览                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              SafetyGate (安全管线编排器)                     │  │
│  │  evaluate(): 编排 L0→L1→Mode→Consent→Confirm→Tenant       │  │
│  └──┬──────────┬──────────┬──────────┬───────────────────────┘  │
│     │          │          │          │                           │
│     ▼          ▼          ▼          ▼                           │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐                       │
│  │RuleGa│  │RiskRo│  │Consent│  │Confir│                        │
│  │te    │  │uter  │  │Gate  │  │mGate │                        │
│  │L0    │  │L1    │  │PHI   │  │L2    │                        │
│  │静态  │  │分级  │  │同意  │  │确认  │                        │
│  └──────┘  └──────┘  └──────┘  └──────┘                        │
│     │          │          │          │                           │
│     ▼          ▼          ▼          ▼                           │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  │
│  │Bwrap │  │DLP   │  │Consent│  │FourEy│  │Break │  │MCP   │  │
│  │Policy│  │Scnr  │  │Store │  │esRtr │  │Glass │  │Guard │  │
│  │沙箱  │  │防泄漏│  │TTL   │  │4-eyes│  │熔断  │  │注册  │  │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘  │
│                                                                 │
│  决策链: RuleGate(L0) → RiskRouter(L1) → WorkspaceMode          │
│          → ConsentGate → ConfirmGateway(L2) → TenantOverride    │
│          → BwrapPolicy / DLP / Audit / BreakGlass / MCP         │
└─────────────────────────────────────────────────────────────────┘
```

### 核心子目录

| 子目录 | 职责 |
|--------|------|
| `sandbox/` | 进程级沙箱隔离（bwrap / passthrough） |
| `gate/` | 安全门禁编排（SafetyGate、RuleGate、RiskRouter、ConfirmGateway） |
| `gate/routing/` | 路由层（RuleGate、RiskRouter、ConfirmGateway、FourEyesRouter） |
| `audit/` | 审计日志（AuditLogger、WORM 审计） |
| `dlp/` | 数据防泄漏（DLP 扫描器、脱敏、编码预处理） |
| `consent/` | 同意管理（ConsentGate） |
| `breakglass/` | 熔断机制（BreakGlassManager） |
| `mcp_guard/` | MCP 注册表安全防护与运行时网络观察 |
| `crypto/` | 密码学（HSM 签名、密钥轮换） |

## 核心组件

### 1. BwrapPolicy (`sandbox/bwrap.py`)

基于 Linux bubblewrap 的进程级沙箱策略，替代不可安装的 SrtPolicy。

**核心方法：**

| 方法 | 作用 |
|------|------|
| `wrap_bash_command(cmd, ctx)` | 包装 bash 命令，返回 `(bwrap_path, [args..., /bin/sh, -c, cmd])` |
| `wrap_mcp_stdio(params, server_name, sandbox_config)` | 包装 MCP stdio 进程 |
| `validate_path(path, tier, workspace_root)` | 路径校验（工作区内允许、系统只读允许、其余拒绝） |
| `check_network_access(tier)` | STRICT tier 禁止网络 |

**`_build_bwrap_args()`** — per-user bwrap 参数生成：

| 参数类型 | 说明 |
|----------|------|
| `--ro-bind /usr /usr` | 系统目录只读挂载 |
| `--bind workspace_root /workspace` | 用户工作区读写挂载 |
| `--tmpfs /tmp` | 隔离临时目录 |
| `--unshare-pid` | 进程隔离 |
| `--die-with-parent` | 父进程退出时子进程退出 |
| `--unshare-net` / `--share-net` | STRICT 断网 / 其他共享 |
| `--clearenv` + `--setenv` 白名单 | 环境变量清零+白名单注入 |

**硬编码安全底线 `_HARDCODED_DENY_BIND`：** `/root`, `/home`, `/etc/ssh`, `/etc/shadow`, `/etc/ssl/private`, `/var/lib/docker`, `/var/lib/kubelet`

### 2. SandboxPolicy 协议 (`sandbox/policy.py`)

**`SandboxPolicy`** (Protocol) — 沙箱策略接口：

| 方法 | 签名 |
|------|------|
| `wrap_bash_command` | `(cmd, ctx) -> (executable, args)` |
| `wrap_mcp_stdio` | `(params, server_name, config) -> params` |

支持的 backend：`"bwrap"` | `"none"`

### 3. SandboxState (`sandbox/state.py`)

**`resolve_sandbox_state()`** — 根据配置解析运行时 SandboxPolicy：

| 配置 | 结果 |
|------|------|
| `enforce=False` | `_PassthroughPolicy`（开发模式，无隔离） |
| `enforce=True` | `BwrapPolicy`（生产模式，bwrap 不可用时抛 `SandboxStartupError`） |

**`health_advisory()`** — 沙箱健康检查报告

**`_PassthroughPolicy`** — 开发模式直通策略，不做任何隔离。

### 4. 环境变量剥离 (`sandbox/env_strip.py`)

**`build_clean_env()`** — 构建容器安全环境变量字典：

| 层级 | 逻辑 |
|------|------|
| 硬编码 deny floor | `_is_hardcoded_deny()` — 永不泄漏（如 `LD_PRELOAD`） |
| operator allowlist | `DEFAULT_ALLOWLIST = {"PATH", "HOME", "LANG", "TERM"}` |
| base_env 覆盖 | 显式传入的键值对（仍受 deny floor 检查） |

设计原则：**operator 只能收窄，不能放宽**。allowlist 中命中 deny floor 的条目在配置解析时即被拒绝。

### 5. SafetyGate (`gate/gate.py`)

安全管线核心编排器，替代旧的 `approval_gate.py`。

**`evaluate()`** — 编排顺序：

```
Step 0:   get_tool_safety_meta(tool_name) — 获取工具安全元数据
Step 0.5: TenantPolicyStore — 策略覆盖检查（预留：当前未接线，tenant_id 恒为空）
Step 1:   RuleGate.check() — L0 静态规则
Step 2:   RiskRouter.assess() — L1 风险分级
Step 3:   WorkspaceMode 检查 — chat 模式下写类工具 deny
Step 3.5: ConsentGate.check() — PHI/法律工作区同意检查
Step 4:   ConfirmGateway — ConfirmationKind.IRREVERSIBLE 确认
Step 4.5: TenantPolicyOverride — 策略覆盖 tier/confirmation（预留，同上）
```

**`GateDecision`** 输出：

| 字段 | 类型 | 说明 |
|------|------|------|
| `allowed` | `bool` | 是否允许 |
| `sandbox_tier` | `SandboxTier`（定义于 `schema/content/workspace.py`） | STANDARD / TIGHT / STRICT |
| `confirmation` | `ConfirmationKind`（定义于 schema 包） | NONE / SOFT_CONFIRM / IRREVERSIBLE |
| `confirmation_request` | `ConfirmationRequest \| None` | 确认请求详情 |
| `reason` | `str` | 决策原因 |
| `risk_assessment` | `RiskAssessment \| None` | 风险评估详情 |

**chat 模式允许的 scope：** `LOCAL_READ` + `INTERNAL_READ`

### 6. RuleGate (`gate/routing/rule_gate.py`)

L0 静态规则门禁，5 条规则：

| 规则 | 检测逻辑 | 结果 |
|------|---------|------|
| 路径逃逸 | 路径含 `..` 或绝对路径不在 workspace 内 | deny |
| 敏感路径 | `/etc/passwd`, `~/.ssh`, `/proc` 等 | deny |
| SSRF | URL 指向 `169.254.169.254` / 私有 IP / loopback | deny |
| workspace_tag | PHI + EXTERNAL_WRITE → deny；PHI + bash → 升级 TIGHT | deny / tier_override |
| MCP 未注册 | `get_tool_safety_meta()` 返回 fail-closed 元数据 | deny（由 RiskRouter 处理） |

敏感路径检测包含精确匹配、前缀匹配、`~/` 展开和敏感扩展名匹配（`.dcm`, `.pem`, `.key` 等）。

### 7. RiskRouter (`gate/routing/risk_router.py`)

L1 风险分级路由，基于 `ToolSafetyMeta` 三维交叉：

```
side_effect_scope × data_classification × reversibility
    → SandboxTier + ConfirmationKind
```

**`SideEffectScope`** 枚举：

| 值 | 含义 | chat 模式 |
|----|------|----------|
| `LOCAL_READ` | 纯读 | 允许 |
| `LOCAL_WORKSPACE` | 读写 | 拒绝 |
| `INTERNAL_READ` | 内部读 | 允许 |
| `INTERNAL_WRITE` | 内部写 | 拒绝 |
| `EXTERNAL_WRITE` | 外部写 | 拒绝 |
| `EXTERNAL_IRREVERSIBLE` | 外部不可逆 | 拒绝 |

**`DataClassification`** 枚举：`PUBLIC` / `INTERNAL` / `PRIVILEGED` / `PROTECTED_HEALTH` / `PERSONAL_SENSITIVE`

**`Reversibility`** 枚举：`REVERSIBLE` / `CONDITIONAL` / `IRREVERSIBLE`

**fail-closed 默认值 `_FAIL_CLOSED_META`：** `EXTERNAL_IRREVERSIBLE` + `PROTECTED_HEALTH` + `IRREVERSIBLE` + `four_eyes_required=True`

**LLM hint 路径：** bash 危险命令（`rm`, `mkfs.`, `dd`, `shred`, `format`）自动升级为 TIGHT + IRREVERSIBLE

### 8. ConfirmGateway (`gate/routing/confirm_gateway.py`)

三级 UX 确认网关，替代旧的 `approval_gate.py` ask 决策：

| 级别 | 触发条件 | 机制 |
|------|---------|------|
| `SOFT_BATCH` | 写操作但非不可逆 | 同模板 3 合 1，1 次点击确认 |
| `HARD_MFA` | IRREVERSIBLE | FIDO2/WebAuthn 二次认证 |
| `HARD_4EYES` | PHI + IRREVERSIBLE / workspace_tag.four_eyes_required | 第二人审批 |

**signed_payload 防篡改：**

```
signed_payload = SHA256(tool_name + args_canonical + timestamp + secret)
验证时校验 payload 完整性 + 时间窗口（默认 300s）
```

**MFA 生产就绪检查：** 生产环境 + `NullMfaProvider` → fail-closed 拒绝

### 9. FourEyesRouter (`gate/routing/four_eyes_router.py`)

4-eyes 工作流路由：

- **`should_force_four_eyes()`** — 判断是否强制 4-eyes
- **`upgrade_to_four_eyes()`** — 将请求升级为 `HARD_4EYES`
- **`assign_approver()`** — 指派第二审批人

审批人指派优先级：
1. `four_eyes_delegate_ids` 预配置 → round-robin
2. `approver_dept` 交叉部门指派
3. 默认占位符

硬性校验：`requester_id != assignee_id`

### 10. WorkspaceTag (`gate/policies.py`)

工作区安全标签，三层校验：

| 字段 | 说明 |
|------|------|
| `kind` | `WorkspaceKind` 枚举（GENERAL / LEGAL_CASE / MEDICAL_GENERAL / MEDICAL_PHI / OFFICE） |
| `data_residency` | 数据驻留区域（医疗/法律必须指定） |
| `four_eyes_required` | 强制 4-eyes 审批 |
| `approver_dept` | 指定审批部门 |
| `four_eyes_delegate_ids` | 预配置审批人列表 |

**`WorkspaceTagValidator`** 三层校验：
1. kind 必须在枚举内
2. 医疗/法律场景必须指定 `data_residency`
3. PHI 工作区必须指定 `owner_id`

### 11. TenantPolicyOverride (`gate/policies.py`)

策略覆盖能力（预留）：`TenantPolicyStore` 尚未在 assembly 接线，`tenant_id` 当前恒为空，不参与实际决策。启用后**只能收紧、不能放宽**：

| 字段 | 说明 |
|------|------|
| `tool_deny_list` | 工具黑名单 |
| `confirm_policy_overrides` | ConfirmationKind 覆盖（只能升级） |
| `sandbox_tier_overrides` | SandboxTier 覆盖（只能升级） |
| `allowed_domains_override` | 网络域名白名单 |
| `denied_domains_extra` | 额外域名黑名单 |

### 12. SessionAggregateGuard (`gate/policies.py`)

Session 级聚合防护，基于 `AggregateStoreProtocol` 的滑动窗口计数器：

| 计数器 | soft 阈值 | hard 阈值 | 窗口 | 升档 |
|--------|----------|----------|------|------|
| `phi_rows_exported_1h` | 25 | 50 | 1h | TIGHT |
| `emails_sent_1h` | 20 | 40 | 1h | TIGHT |
| `prescriptions_submitted_24h` | 15 | 30 | 24h | STRICT |
| `network_bytes_out_1h` | 50MB | 100MB | 1h | TIGHT |

**`AggregateAction`** 枚举：`ALLOW` / `WARN`(80%) / `ESCALATE`(soft) / `BLOCK`(hard)

**后端支持：** `RedisAggregateStore`（生产，Pipeline INCRBY + EXPIRE） / `InMemoryAggregateStore`（开发）

### 13. DLP Scanner (`dlp/scanner.py`)

出站响应 DLP 扫描器，四步扫描：

```
1. DLP 编码预处理（URL/Base64/QP/NFKC）
2. Safe Harbor 正则扫描 + 逐行统计 + PHI 字节统计
3. k-匿名检查（行数 ≥ 30 → BLOCK）
4. PHI 字节占比检查（≥ 30% → BLOCK）
```

**`DlpDecision`** 输出：`ALLOW` / `MASK`（脱敏后放行） / `BLOCK`

**`scan_for_proxy()`** — C Plane Proxy inline DLP 扫描，区分出站（30% 阈值）和入站（50% 阈值）。

### 14. DLP Sanitizer (`dlp/sanitizer.py`)

**`sanitize_tool_args()`** — 工具参数 PHI 脱敏：
- `_PHI_FIELDS` 集合：`name`, `ssn`, `email`, `phone`, `dob`, `mrn` 等
- 命中字段替换为 `[REDACTED]`

**`dlp_preprocess()`** — 编码预处理完整流水线：
1. Unicode NFKC 标准化
2. Base64 解码（可读文本检测）
3. URL 递归解码（最多 3 层）
4. Quoted-Printable 解码
5. 再次 NFKC 标准化

**`PhiLlmScanner`** — L2 LLM PHI 残留扫描器：
- GPU 检测（`nvidia-smi`，≥14GB = T4 级）
- GPU 不可用时自动 fallback 到 `PhiLlmFallbackScanner`（仅 L3 Safe Harbor）
- WORM 审计 `dlp_llm_fallback_active`

### 15. ConsentGate (`consent/consent_gate.py`)

声明驱动同意管理，HIPAA 164.508 / GDPR Art.6 合规：

**`check()`** — 检查是否已有有效同意：
- 未声明用途 → deny `no_declared_purpose`
- 已声明但缺少同意 → deny `consent_not_found` + `missing_purposes`
- 全部同意 → allow

**`grant()`** — 授予同意（含 TTL）
**`revoke()`** — 撤销同意
**`get_purpose_consistency_prompt()`** — 生成 LLM 上下文注入的目的一致性提示

**`ConsentRecord`** 字段：`consent_id`, `session_id`, `user_id`, `purpose`, `tool_name`, `granted`, `scope`, `workspace_id`, `expires_at`

### 16. BreakGlassManager (`breakglass/breakglass_manager.py`)

Emergency Access 熔断机制：

**`request()`** — 处理 Break-Glass 请求：
- 硬性校验：`requester_id != approver_id`
- Token 时间窗口：默认 30min，最长 2h
- 同部门告警
- WORM 审计 `breakglass_created`
- P4-SEC-01：批准后立即启动 `BreakGlassRecorder` 录屏

**`validate()`** — 验证 token，支持 scoping：
- `workspace_ids` — 工作区范围限定
- `tool_pattern` — 工具名 glob 匹配
- `sandbox_tier_cap` — 沙箱级别上限

**`revoke()`** — 撤销 token + 停止录屏 + 上传索引

### 17. McpRegistryGuard (`mcp_guard/mcp_registry_guard.py`)

MCP 注册表安全防护：

**`check()`** — 三层检查：

| 层 | 规则 | 结果 |
|----|------|------|
| 1 | SSE/HTTP transport | deny（容器化完成前不信任） |
| 2 | 未注册 server | fail-closed（`EXTERNAL_IRREVERSIBLE` + `PROTECTED_HEALTH`） |
| 3 | Ed25519 签名校验（P4-SEC-02 SignerProtocol） | TRUSTED / UNVERIFIED |

**`McpSecurityMeta`** — MCP 安全元数据，含 `runtime_network_expect`（none / limited / full）

### 18. McpRuntimeNetworkObserver (`mcp_guard/mcp_runtime_network_observer.py`)

MCP 运行时网络行为观察器：

- **`observe_traffic()`** — 比较实际流量与声明的 `runtime_network_expect`
- **3-strike 自动拉黑** — 违规 3 次自动拉黑 24h
- **`NetworkViolationKind`** — `UNDECLARED_DOMAIN` / `UNDECLARED_PORT` / `UNDECLARED_PROTOCOL`

### 19. WORM Audit (`audit/worm_audit.py`)

Write-Once-Read-Many 合规审计，满足 HIPAA 164.312(b) 和 GDPR Art.30：

**核心机制：**

| 机制 | 说明 |
|------|------|
| 哈希链 | 每条记录包含前一条的 SHA256，不可篡改 |
| 异步写入 | fire-and-forget + 内存缓冲队列（默认 1000） |
| 队列满降级 | 本地文件追加 |
| S3 Object Lock | Compliance Mode 锁定 7 年 |
| P4 fanout | S3 + syslog-ng TLS + 本地缓冲多副本 |

**Sink 实现：**

| Sink | 说明 |
|------|------|
| `InMemoryWormSink` | 内存（开发/测试） |
| `LocalFileWormSink` | 本地文件 JSONL 追加 |
| `S3ObjectLockSink` | S3 Object Lock Compliance Mode |
| `SyslogNgSink` | syslog-ng TLS 远端（RFC5424 格式） |
| `FanoutWormSink` | 并行写入多后端，≥1 成功即视为成功 |

**`build_default_fanout_sink()`** — 生产可用的 FanoutWormSink 装配入口

### 20. AuditLogger (`audit/audit_logger.py`)

结构化 JSON 审计日志：

- **`log_decision()`** — 审批决策审计
- **`log_tier_change()`** — 权限层变更审计

### 21. Cryptographic Signing (`crypto/hsm_signer.py`)

**`SignerProtocol`** — 签名/验签接口

| 实现 | 说明 |
|------|------|
| `LocalEd25519Signer` | 本地 Ed25519（开发/测试），90 天轮换 + 7 天兼容窗口 |
| `HsmPkcs11Signer` | HSM PKCS#11（生产），`PRISMCLAW_PKCS11_LIB` 环境变量配置 |

**`KeyRotationJob`** — 密钥轮换：
- 主密钥创建超过 90 天 → 自动轮换
- 旧密钥标记为前任钥（7 天兼容窗口）
- 兼容窗口结束后旧密钥过期清理

**`KeyVersion`** 数据类：`version`, `public_key_hex`, `created_at`, `rotated_at`, `expires_at`

### 22. KeyRotationCronJob (`crypto/key_rotation.py`)

定期密钥轮换 Cron Job 入口，支持 APScheduler / systemd timer / K8s CronJob 调度。

### 23. Constraint Guard (`gate/constraint_guard.py`)

R4 Constraint 硬护栏检测缝隙：

**`detect_constraint_breaches()`** — 消费 review verdict 的 `acceptance_evaluation`：
- 约束声明但未满足 → `ConstraintBreach`
- 干净完成（`verdict == "complete"`）→ 不报告（零误报）
- 不做 NLP 猜测，仅消费已有信号

### 24. Governance (`gate/governance.py`)

**`SemanticFirewall`** — V3 语义防火墙，检测 prompt injection 模式。

**`ContextValidatorChain`** — 上下文预算验证 + 语义防火墙：
- 预算超 1.5x → `CircuitBreakerException`（保留 `required_state_anchor` 块）
- prompt injection 检测 → 注入 `[SECURITY ADVISORY]` 前缀

**Governance 构建函数：**

| 函数 | 作用 |
|------|------|
| `build_initial_task_spec()` | 构建 TaskSpec（含 preflight_probe） |
| `refine_task_spec()` | 用 TaskProfile 精化 TaskSpec |
| `build_turn_semantics()` | 构建轮次语义 |
| `build_task_context_contract()` | 构建任务上下文合约 |
| `build_review_requirements()` | 构建审查需求（required_evidence 唯一来源） |
| `build_clarify_decision()` | 构建澄清决策 |
| `make_ledger_event()` | 构建决策账本事件 |

### 25. Preflight & Recovery Probes (`gate/probes.py`)

**`run_preflight_probe()`** — 用户消息发出前的 workspace 探测：

- 提及路径解析与存在性检查
- Workspace tree 扫描（文件/目录计数，上限 400/120）
- Git dirty 检测
- 关键路径识别（`pyproject.toml`, `docker-compose.yml` 等）
- Python 依赖扫描（AST import 分析，上限 8 个文件）
- 信号生成（`critical_path`, `cross_module_span`, `git_dirty_critical_paths` 等）

**`run_recovery_probe()`** — Recovery 模式下的 workspace 状态检查：

- 与 preflight baseline 对比（文件数/目录数/关键路径/dirty paths）
- `workspace_surface_changed` — workspace 缩小
- `dirty_workspace_drift` — 新增 dirty paths
- `critical_workspace_drift` — 关键路径缺失
- 状态：`ready` / `degraded` / `blocked`

## 关键数据流

### SafetyGate 工具调用评估流程

```
tool_name + arguments + workspace_tag
    ↓
Step 0:   get_tool_safety_meta() → ToolSafetyMeta (fail-closed)
Step 0.5: TenantPolicyStore → tool_deny_list check
    ↓
Step 1:   RuleGate.check() → 5 条静态规则
    ↓ (allowed)
Step 2:   RiskRouter.assess() → SandboxTier + ConfirmationKind
    ↓
Step 3:   WorkspaceMode check (chat 模式写操作 deny)
    ↓
Step 3.5: ConsentGate.check() (PHI/法律工作区)
    ↓
Step 4:   ConfirmGateway (SOFT_BATCH / HARD_MFA / HARD_4EYES)
    ↓
Step 4.5: TenantPolicyOverride (tier/confirmation 只收紧)
    ↓
GateDecision (allowed + sandbox_tier + confirmation)
```

### DLP 扫描流程

```
出站响应文本
    ↓
dlp_preprocess(): NFKC → Base64 → URL → QP → NFKC
    ↓
Safe Harbor 正则扫描 + 逐行统计 + PHI 字节统计
    ↓
k-匿名检查 (≥30 行含 PHI → BLOCK)
    ↓
PHI 字节占比 (≥30% → BLOCK)
    ↓
脱敏检查 (有 PHI → MASK)
    ↓
DlpDecision (ALLOW / MASK / BLOCK)
```

### WORM 审计写入流程

```
安全事件 → WormAuditWriter.log() (fire-and-forget)
    ↓
asyncio.Queue (maxsize=1000)
    ↓
Queue 满或未初始化 → 本地文件 fallback
    ↓
_write_loop() → FanoutWormSink.write()
    ↓
并行写入: LocalFileWormSink + S3ObjectLockSink + SyslogNgSink
    ↓
≥1 sink 成功 → 成功
```

### Break-Glass 流程

```
BreakGlassRequest (session_id, requester_id, reason, duration)
    ↓
requester_id != approver_id (硬性校验)
    ↓
Token 创建 (最长 2h)
    ↓
BreakGlassRecorder.start_recording()
    ↓
调度 auto_stop (duration 后)
    ↓
WORM 审计: breakglass_created
    ↓
validate(): workspace_ids + tool_pattern + sandbox_tier_cap scoping
```

## 对外接口

| 接口 | 函数/类 | 说明 |
|------|---------|------|
| 安全评估 | `SafetyGate.evaluate()` | 工具调用安全决策 |
| 沙箱策略 | `resolve_sandbox_state()` | 沙箱策略解析 |
| 命令包装 | `BwrapPolicy.wrap_bash_command()` | bash 命令沙箱包装 |
| MCP 包装 | `BwrapPolicy.wrap_mcp_stdio()` | MCP 进程沙箱包装 |
| 环境清理 | `build_clean_env()` | 容器安全环境变量 |
| DLP 扫描 | `DlpScanner.scan()` | 出站响应 PHI 扫描 |
| Proxy 扫描 | `DlpScanner.scan_for_proxy()` | C Plane inline 扫描 |
| 同意检查 | `ConsentGate.check()` | PHI 访问同意验证 |
| Break-Glass | `BreakGlassManager.request/validate/revoke()` | 紧急访问熔断 |
| MCP 防护 | `McpRegistryGuard.check()` | MCP server 注册表检查 |
| 网络观察 | `McpRuntimeNetworkObserver.observe_traffic()` | MCP 运行时网络监控 |
| WORM 审计 | `WormAuditWriter.log()` | 合规审计记录 |
| WORM 查询 | `WormAuditWriter.query()` | 审计记录查询 |
| 完整性验证 | `WormAuditWriter.verify_integrity()` | 哈希链完整性校验 |
| Fanout 装配 | `build_default_fanout_sink()` | 生产 WORM 多副本装配 |
| 密钥轮换 | `KeyRotationCronJob.run()` | 密钥轮换检查 |
| 约束检测 | `detect_constraint_breaches()` | Constraint 硬护栏 |
| Preflight | `run_preflight_probe()` | 执行前 workspace 探测 |
| Recovery | `run_recovery_probe()` | Recovery workspace 状态检查 |
| 语义防火墙 | `ContextValidatorChain.validate_and_transform()` | 上下文预算 + injection 检测 |
| 聚合防护 | `SessionAggregateGuard.check()` | Session 级计数器阈值检查 |
| 审计日志 | `AuditLogger.log_decision/log_tier_change()` | 结构化审计日志 |

## 依赖关系

```
security/
  ├── gate/gate.py → gate/routing/rule_gate.py, gate/routing/risk_router.py,
  │                   gate/routing/confirm_gateway.py, gate/routing/four_eyes_router.py,
  │                   gate/policies.py, consent/consent_gate.py
  ├── gate/routing/rule_gate.py → gate/policies.py, gate/routing/risk_router.py
  ├── gate/routing/risk_router.py → (schema types)
  ├── gate/routing/confirm_gateway.py → gate/policies.py (MfaProvider)
  ├── gate/routing/four_eyes_router.py → gate/policies.py (WorkspaceTag)
  ├── gate/policies.py → gate/types.py (AggregateStoreProtocol)
  ├── gate/governance.py → gate/probes.py, gate/intent_classifier.py
  ├── gate/constraint_guard.py → (schema types)
  ├── sandbox/bwrap.py → sandbox/policy.py
  ├── sandbox/state.py → sandbox/bwrap.py, sandbox/policy.py
  ├── sandbox/env_strip.py → (schema/security/deny_names)
  ├── dlp/scanner.py → dlp/sanitizer.py, (schema/shared/phi_sanitizer)
  ├── dlp/sanitizer.py → (standalone)
  ├── consent/consent_gate.py → gate/types.py (ConsentStoreProtocol)
  ├── breakglass/breakglass_manager.py → breakglass/breakglass_recorder.py,
  │                                       gate/types.py (BreakGlassStoreProtocol)
  ├── mcp_guard/mcp_registry_guard.py → crypto/hsm_signer.py, gate/routing/risk_router.py
  ├── mcp_guard/mcp_runtime_network_observer.py → (standalone)
  ├── audit/worm_audit.py → (standalone)
  ├── audit/audit_logger.py → (schema types)
  ├── crypto/hsm_signer.py → (schema/agent/security SignerProtocol)
  └── crypto/key_rotation.py → crypto/hsm_signer.py
```

## 配置项

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `sandbox.enforce` | `SandboxConfig` | — | 是否启用 bwrap 沙箱 |
| `default_env_allowlist` | `SandboxConfig` | — | 沙箱环境变量白名单 |
| `PRISMCLAW_SANDBOX_PROXY` | env var | — | 沙箱网络代理 |
| k-匿名行数阈值 | `DlpScanner` | 30 | 含 PHI 行数阻断阈值 |
| PHI 字节占比阈值 | `DlpScanner` | 0.3 (30%) | PHI 字节占比阻断阈值 |
| 入站 PHI 占比阈值 | `DlpScanner` | 0.5 (50%) | 入站 PHI 占比阻断阈值 |
| SOFT_BATCH 阈值 | `ConfirmGateway` | 3 | 同模板合并确认数 |
| signed_payload 容差 | `verify_signed_payload()` | 300s | 签名时间窗口 |
| Break-Glass 最长时长 | `BreakGlassManager` | 7200s (2h) | Token 最长有效期 |
| WORM 队列大小 | `WormAuditWriter` | 1000 | 异步写入缓冲队列 |
| S3 保留天数 | `S3ObjectLockSink` | 7 年 | Object Lock 保留期 |
| strike 上限 | `McpRuntimeNetworkObserver` | 3 | MCP 违规拉黑阈值 |
| 拉黑时长 | `McpRuntimeNetworkObserver` | 86400s (24h) | MCP 拉黑有效期 |
| 密钥轮换间隔 | `KeyRotationJob` | 90 天 | 主密钥轮换周期 |
| 兼容窗口 | `KeyRotationJob` | 7 天 | 前任钥验签有效期 |
| `PRISMCLAW_DEPLOYMENT_ENV` | env var | development | 部署环境 |
| `PRISMCLAW_MFA_VERIFY_URL` | env var | — | MFA 断言验证端点 |
| `PRISMCLAW_MFA_SHARED_SECRET` | env var | — | MFA HMAC 密钥 |
| `PRISMCLAW_PKCS11_LIB` | env var | — | HSM PKCS#11 库路径 |
