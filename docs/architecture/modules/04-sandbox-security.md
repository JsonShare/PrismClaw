# 04 — 沙箱安全 (Sandbox & Security)

> 源码目录：`backend/src/prismclaw/engine/security/`

## 职责概述

沙箱安全系统为 PrismClaw 提供多层防御体系，覆盖工具调用的全生命周期安全管控。从进程级沙箱隔离（bwrap）到语义防火墙，从静态规则门禁到动态风险分级，从 PHI 数据防泄漏到同意与熔断机制，构成纵深防御架构。核心设计原则是 **fail-closed**：未注册工具走最严分类，未配置安全组件默认拒绝，不存在"跳过所有检查"的路径。

## 架构总览

**[查看交互式架构图](../diagrams/04-sandbox-security.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                   Sandbox & Security 总览                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │        工具调用前置检查（doom-loop 冻结 + pre-tool hooks）    │  │
│  │        RuleGate.check(): L0 静态规则 deny/pass              │  │
│  └──┬──────────┬──────────┬───────────────────────────────────┘  │
│     │          │          │                                       │
│     ▼          ▼          ▼                                       │
│  ┌──────┐  ┌──────┐  ┌──────┐                                    │
│  │RuleGa│  │Consent│ │Hook  │                                     │
│  │te    │  │Gate  │  │Dispatch│                                  │
│  │L0    │  │PHI   │  │插件   │                                   │
│  │静态  │  │同意  │  │拦截   │                                   │
│  └──────┘  └──────┘  └──────┘                                    │
│     │          │          │                                       │
│     ▼          ▼          ▼                                       │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐              │
│  │Bwrap │  │DLP   │  │Consent│  │Break │  │MCP   │              │
│  │Policy│  │Scnr  │  │Store │  │Glass │  │Guard │               │
│  │沙箱  │  │防泄漏│  │TTL   │  │熔断  │  │注册  │               │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘              │
│                                                                 │
│  决策链: RuleGate(L0) → 任务级确认（awaiting_* / decision）       │
│          → BwrapPolicy / DLP / BreakGlass / MCP                 │
└─────────────────────────────────────────────────────────────────┘
```

### 核心子目录

| 子目录 | 职责 |
|--------|------|
| `sandbox/` | 进程级沙箱隔离（bwrap / passthrough） |
| `gate/` | 安全门禁基础设施（WorkspaceTag、聚合防护、probes、constraint） |
| `gate/routing/` | 路由层（RuleGate、ToolSafetyMeta） |
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

### 5. 工具调用前置检查

工具调用前的检查为：

```
1. doom-loop 冻结签名检查（确定性决策）
2. pre-tool hooks（插件）— deny 直接阻断
3. RuleGate.check() — L0 静态规则（在 executor 中调用）
```

需要用户确认的场景由**任务级确认状态机**承担：`awaiting_risk_confirmation` /
`awaiting_confirmation` + `confirmation_decision` 写入任务状态，前端确认条解析后继续执行。


### 6. RuleGate (`gate/routing/rule_gate.py`)

L0 静态规则门禁，5 条规则：

| 规则 | 检测逻辑 | 结果 |
|------|---------|------|
| 路径逃逸 | 路径含 `..` 或绝对路径不在 workspace 内 | deny |
| 敏感路径 | `/etc/passwd`, `~/.ssh`, `/proc` 等 | deny |
| SSRF | URL 指向 `169.254.169.254` / 私有 IP / loopback | deny |
| workspace_tag | allow_external_write=False + EXTERNAL_WRITE → deny；bash/python → 统一 TIGHT | deny / tier_override |
| MCP 未注册 | `get_tool_safety_meta()` 返回 fail-closed 元数据 | deny |

敏感路径检测包含精确匹配、前缀匹配、`~/` 展开和敏感扩展名匹配（`.dcm`, `.pem`, `.key` 等）。

### 7. ToolSafetyMeta (`gate/routing/risk_router.py`)

工具安全元数据，基于三维声明：

```
side_effect_scope × data_classification × reversibility
```

**`SideEffectScope`** 枚举：

| 值 | 含义 |
|----|------|
| `LOCAL_READ` | 纯读 |
| `LOCAL_WORKSPACE` | 读写 |
| `INTERNAL_READ` | 内部读 |
| `INTERNAL_WRITE` | 内部写 |
| `EXTERNAL_WRITE` | 外部写 |
| `EXTERNAL_IRREVERSIBLE` | 外部不可逆 |

**`DataClassification`** 枚举：`PUBLIC` / `INTERNAL` / `SENSITIVE`

**`Reversibility`** 枚举：`REVERSIBLE` / `CONDITIONAL` / `IRREVERSIBLE`

**fail-closed 默认值 `_FAIL_CLOSED_META`：** `EXTERNAL_IRREVERSIBLE` + `SENSITIVE` + `IRREVERSIBLE`

`_TIER_ORDER`（STANDARD < TIGHT < STRICT）供 breakglass 校验 tier 上限使用。

### 8. WorkspaceTag (`gate/policies.py`)

工作区安全标签：

| 字段 | 说明 |
|------|------|
| `workspace_id` | 工作区标识（必填） |
| `owner_id` | 所有者（必填） |
| `data_residency` | 数据驻留区域（CN / EU / US） |
| `allow_external_write` | 是否允许外部写（默认 False） |
| `require_consent` | 敏感用途工具是否需要用户同意（默认 True） |
| `restrict_chat_sensitive_data` | 是否隐藏敏感数据工具（默认 True） |
| `sandbox_tier` | 容器安全等级 |
| `template_id` | 容器模板标识 |

**`WorkspaceTagValidator`** 校验：`workspace_id`、`owner_id` 必填。

### 9. SessionAggregateGuard (`gate/policies.py`)

Session 级聚合防护，基于 `AggregateStoreProtocol` 的滑动窗口计数器：

| 计数器 | soft 阈值 | hard 阈值 | 窗口 | 升档 |
|--------|----------|----------|------|------|
| `phi_rows_exported_1h` | 25 | 50 | 1h | TIGHT |
| `emails_sent_1h` | 20 | 40 | 1h | TIGHT |
| `prescriptions_submitted_24h` | 15 | 30 | 24h | STRICT |
| `network_bytes_out_1h` | 50MB | 100MB | 1h | TIGHT |

**`AggregateAction`** 枚举：`ALLOW` / `WARN`(80%) / `ESCALATE`(soft) / `BLOCK`(hard)

**后端支持：** `RedisAggregateStore`（生产，Pipeline INCRBY + EXPIRE） / `InMemoryAggregateStore`（开发）

### 10. DLP Scanner (`dlp/scanner.py`)

出站响应 DLP 扫描器，四步扫描：

```
1. DLP 编码预处理（URL/Base64/QP/NFKC）
2. Safe Harbor 正则扫描 + 逐行统计 + PHI 字节统计
3. k-匿名检查（行数 ≥ 30 → BLOCK）
4. PHI 字节占比检查（≥ 30% → BLOCK）
```

**`DlpDecision`** 输出：`ALLOW` / `MASK`（脱敏后放行） / `BLOCK`

**`scan_for_proxy()`** — C Plane Proxy inline DLP 扫描，区分出站（30% 阈值）和入站（50% 阈值）。

### 11. DLP Sanitizer (`dlp/sanitizer.py`)

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

### 12. ConsentGate (`consent/consent_gate.py`)

声明驱动同意管理，HIPAA 164.508 / GDPR Art.6 合规：

**`check()`** — 检查是否已有有效同意：
- 未声明用途 → deny `no_declared_purpose`
- 已声明但缺少同意 → deny `consent_not_found` + `missing_purposes`
- 全部同意 → allow

**`grant()`** — 授予同意（含 TTL）
**`revoke()`** — 撤销同意
**`get_purpose_consistency_prompt()`** — 生成 LLM 上下文注入的目的一致性提示

**`ConsentRecord`** 字段：`consent_id`, `session_id`, `user_id`, `purpose`, `tool_name`, `granted`, `scope`, `workspace_id`, `expires_at`

### 13. BreakGlassManager (`breakglass/breakglass_manager.py`)

Emergency Access 熔断机制：

**`request()`** — 处理 Break-Glass 请求：
- 硬性校验：`requester_id != approver_id`
- Token 时间窗口：默认 30min，最长 2h
- 同部门告警
- P4-SEC-01：批准后立即启动 `BreakGlassRecorder` 录屏

**`validate()`** — 验证 token，支持 scoping：
- `workspace_ids` — 工作区范围限定
- `tool_pattern` — 工具名 glob 匹配
- `sandbox_tier_cap` — 沙箱级别上限

**`revoke()`** — 撤销 token + 停止录屏 + 上传索引

### 14. McpRegistryGuard (`mcp_guard/mcp_registry_guard.py`)

MCP 注册表安全防护：

**`check()`** — 三层检查：

| 层 | 规则 | 结果 |
|----|------|------|
| 1 | SSE/HTTP transport | deny（容器化完成前不信任） |
| 2 | 未注册 server | fail-closed（`EXTERNAL_IRREVERSIBLE` + `PROTECTED_HEALTH`） |
| 3 | Ed25519 签名校验（P4-SEC-02 SignerProtocol） | TRUSTED / UNVERIFIED |

**`McpSecurityMeta`** — MCP 安全元数据，含 `runtime_network_expect`（none / limited / full）

### 15. McpRuntimeNetworkObserver (`mcp_guard/mcp_runtime_network_observer.py`)

MCP 运行时网络行为观察器：

- **`observe_traffic()`** — 比较实际流量与声明的 `runtime_network_expect`
- **3-strike 自动拉黑** — 违规 3 次自动拉黑 24h
- **`NetworkViolationKind`** — `UNDECLARED_DOMAIN` / `UNDECLARED_PORT` / `UNDECLARED_PROTOCOL`

### 16. Cryptographic Signing (`crypto/hsm_signer.py`)

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

### 17. KeyRotationCronJob (`crypto/key_rotation.py`)

定期密钥轮换 Cron Job 入口，支持 APScheduler / systemd timer / K8s CronJob 调度。

### 18. Constraint Guard (`gate/constraint_guard.py`)

R4 Constraint 硬护栏检测缝隙：

**`detect_constraint_breaches()`** — 消费 review verdict 的 `acceptance_evaluation`：
- 约束声明但未满足 → `ConstraintBreach`
- 干净完成（`verdict == "complete"`）→ 不报告（零误报）
- 不做 NLP 猜测，仅消费已有信号

### 19. Governance (`gate/governance.py`)

**`SemanticFirewall`** — V3 语义防火墙，检测 prompt injection 模式。

**`ContextValidatorChain`** — 上下文预算验证 + 语义防火墙：
- 预算超 1.5x → `CircuitBreakerException`（保留 `required_state_anchor` 块）
- prompt injection 检测 → 注入 `[SECURITY ADVISORY]` 前缀

**Governance 构建函数：**

| 函数 | 作用 |
|------|------|
| `build_initial_task_spec()` | 构建 TaskSpec（含 preflight_probe） |
| `assess_turn()` | 单轮任务评估（轮次/目标/风险/路径/写意图） |
| `make_ledger_event()` | 构建决策账本事件 |

### 20. Preflight & Recovery Probes (`gate/probes.py`)

**`run_preflight_probe()`** — 用户消息发出前的 workspace 探测：

- 提及路径解析与存在性检查
- Workspace tree 单次遍历（文件/目录/代码文件计数上限 400/120）与关键路径识别（`pyproject.toml`, `docker-compose.yml` 等）
- Python 依赖扫描（AST import 分析，上限 8 个文件）
- 信号生成（`critical_path`, `cross_module_span`, `workspace_tree_broad` 等）
- 不读取 Git 状态；`baseline_risk` 只由 high 级信号决定

**`run_recovery_probe()`** — Recovery 模式下的 workspace 状态检查：

- 复用 preflight 的 `existing_paths` 作为检查目标（无基线时回退任务描述文本）
- `critical_workspace_drift` — 关键路径缺失 → `blocked`
- `referenced_paths_missing` — 依赖路径缺失 → `degraded`
- 状态：`ready` / `degraded` / `blocked`

## 关键数据流

### 工具调用前置检查流程

```
tool_name + arguments + workspace_tag
    ↓
doom-loop 冻结签名检查
    ↓ (未冻结)
pre-tool hooks（插件）→ deny 直接阻断
    ↓ (allowed)
RuleGate.check() → 5 条静态规则
    ↓ (allowed)
工具执行（沙箱内）
    ↓
如需用户确认 → 任务级确认状态机（awaiting_* + confirmation_decision）
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
validate(): workspace_ids + tool_pattern + sandbox_tier_cap scoping
```

## 对外接口

| 接口 | 函数/类 | 说明 |
|------|---------|------|
| 安全评估 | `RuleGate.check()` | 工具调用静态规则检查 |
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
| 密钥轮换 | `KeyRotationCronJob.run()` | 密钥轮换检查 |
| 约束检测 | `detect_constraint_breaches()` | Constraint 硬护栏 |
| Preflight | `run_preflight_probe()` | 执行前 workspace 探测 |
| Recovery | `run_recovery_probe()` | Recovery workspace 状态检查 |
| 语义防火墙 | `ContextValidatorChain.validate_and_transform()` | 上下文预算 + injection 检测 |
| 聚合防护 | `SessionAggregateGuard.check()` | Session 级计数器阈值检查 |

## 依赖关系

```
security/
  ├── gate/routing/rule_gate.py → gate/policies.py, gate/routing/risk_router.py
  ├── gate/routing/risk_router.py → (schema types)
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
| Break-Glass 最长时长 | `BreakGlassManager` | 7200s (2h) | Token 最长有效期 |
| strike 上限 | `McpRuntimeNetworkObserver` | 3 | MCP 违规拉黑阈值 |
| 拉黑时长 | `McpRuntimeNetworkObserver` | 86400s (24h) | MCP 拉黑有效期 |
| 密钥轮换间隔 | `KeyRotationJob` | 90 天 | 主密钥轮换周期 |
| 兼容窗口 | `KeyRotationJob` | 7 天 | 前任钥验签有效期 |
| `PRISMCLAW_DEPLOYMENT_ENV` | env var | development | 部署环境 |
| `PRISMCLAW_PKCS11_LIB` | env var | — | HSM PKCS#11 库路径 |
