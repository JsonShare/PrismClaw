<!--
  budget_warning.md

  软预算耗尽警告。当 ``token_usage / effective_window`` 超过
  ``SoftBudgetConfig.warning_ratio``（默认 0.9）时注入系统消息。
  超过 ``terminate_ratio``（默认 0.95）时触发收尾动作（强制结束回合）；
  协调器会在警告中一并给出该比例，让模型理解紧迫程度。

  本模板渲染 ``{ratio}`` 占位符，由
  ``prismclaw.engine.compression._soft_budget.render_budget_warning``
  负责替换；若改用自定义路径，新文件必须包含同一个 ``{ratio}`` 记号。

  运维可通过 ``CompactionConfig.soft_budget.warning_template`` 覆盖。
-->

<budget_warning ratio="{ratio:.0%}">
你已使用有效上下文窗口的 {ratio:.0%}。请完成当前步骤，返回结果后停止。不要开始新的探索；除非绝对必要，不要调用更多工具。
</budget_warning>
