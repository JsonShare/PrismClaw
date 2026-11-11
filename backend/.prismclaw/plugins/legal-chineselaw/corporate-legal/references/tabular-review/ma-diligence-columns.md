<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# M&A 尽调——标准列集

买方目标合同审查的默认模式。从这里开始，然后根据交易添加或删减列。这是起点，不是检查清单——购买协议的陈述和请求清单决定了什么实际重要。

```yaml
schema:
  name: "M&A Diligence — Standard"
  columns:
    - id: counterparty
      label: "对手方"
      type: verbatim
      prompt: "Name the contracting party other than the target entity, exactly as it appears."

    - id: agreement_type
      label: "协议类型"
      type: classify
      options: [msa, purchase_order, license_in, license_out, lease, services, supply, distribution, nda, joint_venture, loan, guaranty, employment, other]
      prompt: "What kind of agreement is this?"

    - id: effective_date
      label: "生效日期"
      type: date
      prompt: "When did this agreement become effective?"

    - id: term
      label: "期限"
      type: duration
      prompt: "What is the initial term?"

    - id: auto_renewal
      label: "自动续约"
      type: classify
      options: [none, annual, fixed_period, evergreen]
      prompt: "Does the agreement auto-renew? On what cycle?"

    - id: termination_for_convenience
      label: "便利终止"
      type: classify
      options: [none, either_party, target_only, counterparty_only]
      prompt: "Can either party terminate without cause? Who?"

    - id: termination_notice
      label: "终止通知期"
      type: duration
      prompt: "How much notice is required to terminate?"

    - id: change_of_control
      label: "控制权变更"
      type: classify
      options: [silent, consent_required, consent_not_unreasonably_withheld, automatic_termination, notice_only, counterparty_right_to_terminate]
      prompt: "Does the agreement address a change of control of the target? What triggers and what happens?"

    - id: assignment
      label: "转让"
      type: classify
      options: [silent, consent_required, consent_not_unreasonably_withheld, freely_assignable, assignable_to_affiliates, non_assignable]
      prompt: "Can the target assign this agreement? What restrictions apply?"

    - id: exclusivity
      label: "独家/竞业限制"
      type: classify
      options: [none, exclusive_supplier, exclusive_customer, non_compete, non_solicit, territory_restriction, most_favored_nation]
      prompt: "Does the agreement restrict either party from competing or contracting with others?"

    - id: liability_cap
      label: "责任上限"
      type: currency
      prompt: "Is there a cap on liability? What is the amount or multiplier?"

    - id: indemnification
      label: "赔偿"
      type: classify
      options: [none, mutual, target_indemnifies, counterparty_indemnifies, ip_only, third_party_claims_only]
      prompt: "Who indemnifies whom, and for what?"

    - id: governing_law
      label: "适用法律"
      type: verbatim
      prompt: "What jurisdiction's law governs?"

    - id: dispute_resolution
      label: "争议解决"
      type: classify
      options: [litigation, arbitration_binding, arbitration_nonbinding, mediation_first, silent]
      prompt: "How are disputes resolved?"

    - id: most_favored_nation
      label: "最惠国/定价保护"
      type: classify
      options: [none, mfn_pricing, price_matching, benchmarking_right]
      prompt: "Is there a most-favored-nation or pricing protection clause?"

    - id: minimum_commitments
      label: "最低采购/量承诺"
      type: currency
      prompt: "Are there minimum purchase, volume, or spend commitments?"

    - id: ip_ownership
      label: "IP 所有权"
      type: classify
      options: [each_owns_own, target_owns_work_product, counterparty_owns_work_product, joint, license_only, silent]
      prompt: "Who owns intellectual property created or used under the agreement?"

    - id: confidentiality_term
      label: "保密存续期"
      type: duration
      prompt: "How long do confidentiality obligations survive termination?"

    - id: insurance_requirements
      label: "保险要求"
      type: classify
      options: [none, general_liability, professional_liability, cyber, workers_comp, umbrella]
      prompt: "What insurance must be maintained?"

    - id: audit_rights
      label: "审计权"
      type: classify
      options: [none, counterparty_may_audit_target, target_may_audit_counterparty, mutual]
      prompt: "Does either party have audit rights?"

    - id: notices
      label: "通知要求"
      type: verbatim
      prompt: "What is the notice address and method for the target?"
```

## 按交易类型的常见附加列

- **科技/IP 密集型目标：** 源代码托管、开源限制、数据权利、模型训练权利、API 访问
- **医疗/生命科学：** BAA 存在、监管申报义务、FDA 通讯、临床试验义务
- **政府承包商：** 变更同意要求、流传条款、安全许可、FAR/DFARS 引用
- **房地产：** 续约选项、租金递增、CAM 条款、从属、禁反言要求
- **受监管金融：** 监管审批条件、资本要求、FINRA/SEC 申报触发条件

## 快速初次审查的常见删减列

对于时间紧迫的初步筛查，以下 6 列回答了 80% 的早期交易问题：counterparty（对手方）、effective_date（生效日期）、term（期限）、change_of_control（控制权变更）、assignment（转让）、termination_for_convenience（便利终止）。先运行这些，一旦交易团队确定了优先级再扩展模式。
