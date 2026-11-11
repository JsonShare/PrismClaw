"""PrismClaw 安全基础设施。

P1-SEC：SafetyGate 分层安全门禁系统。

架构：
  RuleGate (L0 静态规则) → RiskRouter (L1 风险分级) → SafetyGate (编排器)

  L0 RuleGate: 路径逃逸 / 敏感路径 / SSRF / workspace_tag / mcp_meta — deny 或 pass
  L1 RiskRouter: ToolSafetyMeta 查找 + side_effect_scope × data_classification × reversibility → SandboxTier + ConfirmationKind
  SafetyGate: 编排 L0 → L1 → 输出 GateDecision

子域：
  gate/      — 核心门控基础设施（RuleGate, RiskRouter, SafetyGate, ...）
  consent/   — 声明驱动同意管理
  breakglass/— 熔断/紧急访问机制
  mcp_guard/ — MCP 注册表安全防护 + 运行时网络观察
  dlp/       — 数据防泄漏
  crypto/    — 签名/加密/HSM
  audit/     — WORM 合规审计
"""
