"""分析管线域纯函数层 — 0 IO，0 LLM 直接调用。

四模块：
- prompts: 5 组 prompt + tool schema + build_*_prompt + 归一化函数
- audit_rules: 3 条审计规则（纯程序，0 LLM）
- synthesis: 简报合成辅助
- evidence: 医疗循证等级推断器
"""

from .evidence import medical_evidence_inferencer

__all__ = ["medical_evidence_inferencer"]
