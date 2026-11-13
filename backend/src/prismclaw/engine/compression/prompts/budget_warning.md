<!--
  budget_warning.md

  软预算耗尽警告。当 token_usage / effective_window 超过
  SoftBudgetConfig.warning_ratio（默认 0.9）时注入系统消息。
  超过 terminate_ratio（默认 0.95）时触发收尾动作（强制结束回合）。

  本模板渲染 {{ratio}} 占位符，由
  prismclaw.engine.compression._soft_budget.render_budget_warning
  负责替换；若改用自定义路径，新文件必须包含同一个 {{ratio}} 记号。
  运维可通过 CompactionConfig.soft_budget.warning_template 覆盖。
-->

<budget_warning ratio="{ratio:.0%}">
你已使用有效上下文窗口的 {ratio:.0%}，即将无法继续追加工具与对话历史。

收敛要求：
- 不再开新探索分支、不再调用工具；本轮只用自然语言交付。
- 优先回答用户原始问题，给出你已拿到的全部结论，不要说"因预算限制未能完成"——这是内部状态。
- 在回答末尾自然收尾：可以用一句话点明"还有 X 未做，如需可继续追问"，让用户决定后续方向。
- 不要输出任何关于预算、限额、窗口使用率的数字或技术术语。
</budget_warning>
