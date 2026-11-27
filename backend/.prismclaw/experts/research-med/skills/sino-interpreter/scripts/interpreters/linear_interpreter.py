"""linear_interpreter.py - 线性回归结果解释器"""

from typing import Dict, Any, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from effect_size_interpreter import EffectSizeInterpreter


class LinearRegressionInterpreter:
    """线性回归结果解释器"""

    def __init__(self):
        self.effect_size_interpreter = EffectSizeInterpreter()

    def interpret(self, results: Dict[str, Any]) -> str:
        """解释线性回归结果

        参数：
            results: 线性回归结果字典，包含：
                - outcome_var: 因变量名称
                - n: 样本量
                - predictors: [{variable, beta, std_beta, ci, p_value, vif}]
                - model_fit: {r_squared, adjusted_r_squared, f, df, p_value}
                - study_type: 研究类型

        返回：
            Plain Language解释字符串
        """
        outcome_var = results.get("outcome_var", "结局变量")
        n = results.get("n", 0)
        predictors = results.get("predictors", [])
        model_fit = results.get("model_fit", {})
        study_type = results.get("study_type", "探索性")

        # 分离显著和不显著的预测变量
        significant = [p for p in predictors if p.get("p_value", 1) < 0.05]
        not_significant = [p for p in predictors if p.get("p_value", 1) >= 0.05]
        in_model_not_sig = [p for p in predictors if 0.05 <= p.get("p_value", 1) < 0.1]

        # 模型拟合数据
        r_squared = model_fit.get("r_squared", 0)
        adj_r_squared = model_fit.get("adjusted_r_squared", r_squared)
        f_stat = model_fit.get("f", 0)
        f_df = model_fit.get("df", [0, 0])
        f_pvalue = model_fit.get("p_value", 1)

        lines = []

        # 模型概况
        lines.append(f"多因素线性回归模型分析{outcome_var}的影响因素，")
        lines.append(f"共纳入{n}例患者进行分析。")
        lines.append("")

        # 模型解释力
        model_interpretation = self.effect_size_interpreter.interpret_r_squared(r_squared, len(predictors))
        lines.append(f"模型解释力：{model_interpretation}。")
        lines.append("")

        # 效应量模型整体
        lines.append(f"R²={r_squared:.3f}（调整R²={adj_r_squared:.3f}），")
        lines.append(f"F({f_df[0] if isinstance(f_df, list) else f_df}, {f_df[1] if isinstance(f_df, list) else n-len(predictors)-1})={f_stat:.2f}, ")
        if f_pvalue < 0.001:
            lines.append(f"p<0.001。")
        else:
            lines.append(f"p={f_pvalue:.3f}。")
        lines.append("")

        # 显著影响因素
        if significant:
            lines.append("## 显著影响因素（p<0.05）")
            lines.append("")

            for pred in significant:
                var_name = pred.get("variable", "变量")
                beta = pred.get("beta", 0)
                ci = pred.get("ci", [0, 0])
                ci_low, ci_high = ci[0], ci[1] if len(ci) > 1 else ci[0]
                std_beta = pred.get("std_beta", 0)
                p_value = pred.get("p_value", 1)
                vif = pred.get("vif", 1)
                unit = pred.get("unit", "单位")

                direction = "增加" if beta > 0 else "降低"

                lines.append(f"**{var_name}**（β={beta:.3f}, 95%CI: {ci_low:.3f}~{ci_high:.3f}, p={p_value:.3f}）：")
                lines.append(f"  每{unit}{outcome_var}平均{direction}{abs(beta):.3f}个单位。")

                # 标准化系数解释
                effect_interpretation = self.effect_size_interpreter.interpret_std_beta(std_beta)
                lines.append(f"  标准化系数β'={std_beta:.3f}，{effect_interpretation}。")

                # 共线性警告
                if vif > 5:
                    lines.append(f"  注意：VIF={vif:.1f}，存在中等共线性问题，解释需谨慎。")
                elif vif > 10:
                    lines.append(f"  警告：VIF={vif:.1f}，存在严重共线性问题，结果不可靠。")

                lines.append("")

        # 进入模型但未达显著
        if in_model_not_sig:
            lines.append("## 进入模型但未达显著（0.05≤p<0.1）")
            lines.append("")
            for pred in in_model_not_sig:
                var_name = pred.get("variable", "变量")
                p_value = pred.get("p_value", 1)
                beta = pred.get("beta", 0)
                lines.append(f"**{var_name}**（p={p_value:.3f}，β={beta:.3f}）：")
                lines.append(f"  该因素已纳入多因素模型，但未达到显著水平，可能存在交互作用。")
                lines.append("")
            lines.append("")

        # 不显著因素
        if not_significant and len(not_significant) <= 5:
            lines.append("## 未进入模型的非显著因素")
            lines.append("")
            for pred in not_significant:
                var_name = pred.get("variable", "变量")
                p_value = pred.get("p_value", 1)
                lines.append(f"- {var_name}（p={p_value:.3f}）：与{outcome_var}无显著关联")
            lines.append("")

        # 结论
        lines.append("## 结论")
        lines.append("")
        if significant:
            top_pred = significant[0]
            top_name = top_pred.get("variable", "因素")
            top_beta = top_pred.get("beta", 0)
            top_direction = "正向" if top_beta > 0 else "负向"
            lines.append(f"本{study_type}研究表明，{top_name}是{outcome_var}的独立影响因素，")
            lines.append(f"与{outcome_var}呈{top_direction}关联（β={top_beta:.3f}, p={top_pred.get('p_value', 1):.3f}）。")
        else:
            lines.append(f"本{study_type}研究未发现与{outcome_var}显著独立相关的因素。")

        # 局限性
        lines.append("")
        lines.append("## 局限性声明")
        lines.append("")
        lines.append(f"本研究为{study_type}研究，可能存在未测量的混杂因素。")
        lines.append("线性回归假设因变量与自变量呈线性关系，这一假设需要验证。")

        return "\n".join(lines)

    def interpret_univariate(self, results: Dict[str, Any]) -> str:
        """解释单因素线性回归结果"""
        outcome_var = results.get("outcome_var", "结局变量")
        predictor = results.get("predictor_var", "预测变量")
        beta = results.get("beta", 0)
        ci = results.get("ci", [0, 0])
        std_beta = results.get("std_beta", 0)
        p_value = results.get("p_value", 1)
        r_squared = results.get("r_squared", 0)
        n = results.get("n", 0)

        direction = "增加" if beta > 0 else "降低"

        lines = []

        lines.append(f"单因素分析显示，{predictor}与{outcome_var}存在关联。")
        lines.append("")
        lines.append(f"每单位{predictor}，{outcome_var}平均{direction}{abs(beta):.3f}（95%CI: {ci[0]:.3f}~{ci[1]:.3f}）。")
        lines.append("")

        effect_interpretation = self.effect_size_interpreter.interpret_std_beta(std_beta)
        lines.append(f"标准化系数β'={std_beta:.3f}，{effect_interpretation}。")
        lines.append("")

        r2_interpretation = self.effect_size_interpreter.interpret_r_squared(r_squared, 1)
        lines.append(f"单因素R²={r_squared:.3f}，{r2_interpretation}。")
        lines.append("")

        if p_value < 0.001:
            p_text = "p<0.001"
        elif p_value < 0.01:
            p_text = f"p={p_value:.3f}"
        else:
            p_text = f"p={p_value:.3f}"

        lines.append(f"该关联{p_text}。")

        if p_value < 0.1:
            lines.append(f"因p<0.1，该变量进入多因素模型进行进一步分析。")
        else:
            lines.append(f"因p≥0.1，该变量未进入多因素模型。")

        return "\n".join(lines)
