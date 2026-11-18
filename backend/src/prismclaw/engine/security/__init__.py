"""PrismClaw 安全基础设施。

工具级安全：
  RuleGate (L0 静态规则) — 路径逃逸 / 敏感路径 / SSRF 等 deny 或 pass
  SandboxTier + WorkspaceTag + ToolSafetyMeta — 沙箱档位与工具安全元数据
  任务级确认由任务状态机承担（awaiting_* + confirmation_decision），
  不存在独立的工具级确认网关。

子域：
  gate/      — 核心门控基础设施（RuleGate、WorkspaceTag、聚合防护、probes）
  consent/   — 声明驱动同意管理
  breakglass/— 熔断/紧急访问机制
  mcp_guard/ — MCP 注册表安全防护 + 运行时网络观察
  dlp/       — 数据防泄漏
  crypto/    — 签名/加密/HSM
"""
