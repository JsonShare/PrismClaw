"""logistic_interpreter.py - logistic回归结果解释器"""

from typing import Dict, Any, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from effect_size_interpreter import EffectSizeInterpreter


class LogisticRegressionInterpreter:
    """logistic回归结果解释器"""

    def __init__(self):
        self.effect_size_interpreter = EffectSizeInterpreter()

    def interpret(self, results: Dict[str, Any]) -> str:
        """解释logistic回归结果

        参数：
            results: logistic回归结果字典，包含：
                - outcome_var: 因变量名称（事件名称）
                - n: 样本量
                - n_events: 事件数
                - predictors: [{variable, or_value, ci, beta, p_value}]
                - model_fit: {hl_p, auc, r_squared, adjusted_r_squared}
                - study_type: 研究类型

        返回：
            Plain Language解释字符串
        """
        outcome_var = results.get("outcome_var", "结局事件")
        n = results.get("n", 0)
        n_events = results.get("n_events", 0)
        predictors = results.get("predictors", [])
        model_fit = results.get("model_fit", {})
        study_type = results.get("study_type", "探索性")

        # 分离显著和不显著的预测变量
        significant = [p for p in predictors if p.get("p_value", 1) < 0.05]
        not_significant = [p for p in predictors if p.get("p_value", 1) >= 0.05]
        in_model_not_sig = [p for p in predictors if 0.05 <= p.get("p_value", 1) < 0.1]

        # 模型拟合数据
        hl_p = model_fit.get("hl_p", model_fit.get("hosmer_lemeshow_p", 1))
        auc = model_fit.get("auc", model_fit.get("c_stat", 0))
        pseudo_r2 = model_fit.get("pseudo_r2", model_fit.get("r_squared", 0))

        lines = []

        # 模型概况
        lines.append(f"多因素logistic回归模型分析{outcome_var}的影响因素，")
        lines.append(f"共纳入{n}例患者，其中{n_events}例发生{outcome_var}（{(n_events/n)*100:.1f}%）。")
        lines.append("")

        # 模型拟合度
        if hl_p < 0.05:
            lines.append(f"注意：Hosmer-Lemeshow检验p={hl_p:.3f}<0.05，模型拟合度可能欠佳。")
        else:
            lines.append(f"Hosmer-Lemeshow检验p={hl_p:.3f}，模型拟合良好。")
        lines.append("")

        # 模型区分度
        if auc < 0.7:
            lines.append(f"模型区分度：AUC={auc:.3f}，区分能力较弱。")
        elif auc < 0.8:
            lines.append(f"模型区分度：AUC={auc:.3f}，区分能力可接受。")
        elif auc < 0.9:
            lines.append(f"模型区分度：AUC={auc:.3f}，区分能力较好。")
        else:
            lines.append(f"模型区分度：AUC={auc:.3f}，区分能力很强。")
        lines.append("")

        # 显著影响因素
        if significant:
            lines.append("## 显著影响因素（OR值，p<0.05）")
            lines.append("")

            for pred in significant:
                var_name = pred.get("variable", "变量")
                or_value = pred.get("or_value", 1)
                ci = pred.get("ci", [0, 0])
                ci_low, ci_high = ci[0], ci[1] if len(ci) > 1 else ci[0]
                beta = pred.get("beta", 0)
                p_value = pred.get("p_value", 1)
                reference = pred.get("reference", "")
                unit = pred.get("unit", "")

                lines.append(f"**{var_name}**（OR={or_value:.2f}, 95%CI: {ci_low:.2f}~{ci_high:.2f}, p={p_value:.3f}）：")

                # OR值解释
                if or_value > 1:
                    risk_increase = (or_value - 1) * 100
                    lines.append(f"  有{var_name}的人群，{outcome_var}风险是无该因素人群的{or_value:.2f}倍，")
                    lines.append(f"  风险增加{risk_increase:.1f}%。")
                    if reference:
                        lines.append(f"  （参照组：{reference}）")
                else:
                    risk_decrease = (1 - or_value) * 100
                    lines.append(f"  有{var_name}的人群，{outcome_var}风险是无该因素人群的{or_value:.2f}倍，")
                    lines.append(f"  风险降低{risk_decrease:.1f}%。")
                    if reference:
                        lines.append(f"  （参照组：{reference}）")

                # 置信区间临床意义
                if ci_low > 1:
                    lines.append(f"  即使考虑抽样误差，最保守估计风险仍增加{(ci_low-1)*100:.1f}%以上。")
                elif ci_high < 1:
                    lines.append(f"  即使考虑抽样误差，最保守估计风险仍降低{(1-ci_high)*100:.1f}%以上。")
                else:
                    lines.append(f"  置信区间跨越1，结论需谨慎解读。")

                lines.append("")

        # 进入模型但未达显著
        if in_model_not_sig:
            lines.append("## 进入模型但未达显著（0.05≤p<0.1）")
            lines.append("")
            for pred in in_model_not_sig:
                var_name = pred.get("variable", "变量")
                p_value = pred.get("p_value", 1)
                or_value = pred.get("or_value", 1)
                lines.append(f"**{var_name}**（p={p_value:.3f}，OR={or_value:.2f}）：")
                lines.append(f"  该因素已纳入多因素模型，但未达到显著水平。")
                lines.append("")
            lines.append("")

        # 不显著因素
        if not_significant and len(not_significant) <= 5:
            lines.append("## 未进入模型的非显著因素")
            lines.append("")
            for pred in not_significant:
                var_name = pred.get("variable", "变量")
                p_value = pred.get("p_value", 1)
                or_value = pred.get("or_value", 1)
                lines.append(f"- {var_name}（p={p_value:.3f}，OR={or_value:.2f}）：与{outcome_var}无显著关联")
            lines.append("")

        # 结论
        lines.append("## 结论")
        lines.append("")
        if significant:
            top_pred = significant[0]
            top_name = top_pred.get("variable", "因素")
            top_or = top_pred.get("or_value", 1)

            if top_or > 1:
                lines.append(f"本{study_type}研究表明，{top_name}是{outcome_var}的重要危险因素，")
                lines.append(f"有{top_name}者风险是无该因素者的{top_or:.2f}倍（p={top_pred.get('p_value', 1):.3f}）。")
            else:
                lines.append(f"本{study_type}研究表明，{top_name}是{outcome_var}的保护因素，")
                lines.append(f"有{top_name}者风险降低{(1-top_or)*100:.1f}%（p={top_pred.get('p_value', 1):.3f}）。")
        else:
            lines.append(f"本{study_type}研究未发现与{outcome_var}显著独立相关的因素。")

        # 局限性
        lines.append("")
        lines.append("## 局限性声明")
        lines.append("")
        lines.append(f"本研究为{study_type}研究，可能存在未测量的混杂因素。")
        lines.append("logistic回归基于事件发生的比值比解释，风险倍数的绝对值需结合基线风险理解。")

        return "\n".join(lines)

    def interpret_univariate(self, results: Dict[str, Any]) -> str:
        """解释单因素logistic回归结果"""
        outcome_var = results.get("outcome_var", "结局事件")
        predictor = results.get("predictor_var", "预测变量")
        or_value = results.get("or_value", 1)
        ci = results.get("ci", [0, 0])
        p_value = results.get("p_value", 1)
        n = results.get("n", 0)

        lines = []

        lines.append(f"单因素分析显示，{predictor}与{outcome_var}存在关联。")
        lines.append("")

        if or_value > 1:
            risk_increase = (or_value - 1) * 100
            lines.append(f"有{predictor}的人群，{outcome_var}风险是无该因素人群的{or_value:.2f}倍，")
            lines.append(f"风险增加{risk_increase:.1f}%。")
        else:
            risk_decrease = (1 - or_value) * 100
            lines.append(f"有{predictor}的人群，{outcome_var}风险是无该因素人群的{or_value:.2f}倍，")
            lines.append(f"风险降低{risk_decrease:.1f}%。")

        lines.append(f"95%CI: {ci[0]:.2f}~{ci[1]:.2f}。")

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
