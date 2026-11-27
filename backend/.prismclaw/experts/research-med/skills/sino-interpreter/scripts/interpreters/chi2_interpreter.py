"""chi2_interpreter.py - 卡方检验结果解释器"""

from typing import Dict, Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from effect_size_interpreter import EffectSizeInterpreter


class Chi2Interpreter:
    """卡方检验结果解释器"""

    def __init__(self):
        self.effect_size_interpreter = EffectSizeInterpreter()

    def interpret(self, results: Dict[str, Any]) -> str:
        """解释卡方检验结果

        参数：
            results: 卡方检验结果字典，包含：
                - method: 检验方法
                - group1_name, group2_name: 组名
                - groups: [组名列表]
                - rates: {组名: {rate, n, ci}}
                - rate_difference: {value, ci}
                - statistics: {chi2, df, p_raw, cramers_v}
                - event_name: 事件名称
                - sample_size: 总样本量
                - study_type: 研究类型

        返回：
            Plain Language解释字符串
        """
        method = results.get("method", "卡方检验")
        groups = results.get("groups", [])
        group1 = results.get("group1_name", groups[0] if len(groups) > 0 else "组1")
        group2 = results.get("group2_name", groups[1] if len(groups) > 1 else "组2")
        rates = results.get("rates", {})
        stats = results.get("statistics", {})
        rate_diff_data = results.get("rate_difference", {})
        event_name = results.get("event_name", "结局")
        sample_size = results.get("sample_size", 0)
        study_type = results.get("study_type", "探索性")

        # 提取数据
        group1_data = rates.get(group1, {"rate": 0, "n": 0})
        group2_data = rates.get(group2, {"rate": 0, "n": 0})

        rate1 = group1_data.get("rate", 0)
        rate2 = group2_data.get("rate", 0)
        n1 = group1_data.get("n", 0)
        n2 = group2_data.get("n", 0)

        rate_diff = rate_diff_data.get("value", rate1 - rate2)
        ci = rate_diff_data.get("ci", [0, 0])
        ci_low, ci_high = ci[0], ci[1] if len(ci) > 1 else ci[0]

        chi2 = stats.get("chi2", stats.get("chi2_stat", 0))
        df = stats.get("df", 1)
        p_raw = stats.get("p_raw", 1)
        p_adjusted = stats.get("p_adjusted", p_raw)
        cramers_v = stats.get("cramers_v", 0)

        # 生成解释
        lines = []

        # 核心发现
        direction = "高" if rate_diff > 0 else "低"
        abs_diff = abs(rate_diff) * 100

        lines.append(f"{group1}的{event_name}发生率（{rate1*100:.1f}%）比{group2}（{rate2*100:.1f}%）{direction}了约{abs_diff:.1f}个百分点。")
        lines.append("")

        # 效应量解释
        effect_interpretation = self.effect_size_interpreter.interpret_cramers_v(cramers_v)
        lines.append(f"效应量为Cramér's V = {cramers_v:.2f}，{effect_interpretation}。")
        lines.append("")

        # 置信区间解释
        lines.append(f"两组率差的95%置信区间为{ci_low*100:.1f}至{ci_high*100:.1f}个百分点，")
        lines.append(f"说明真实的率差最可能在{ci_low*100:.1f}到{ci_high*100:.1f}个百分点之间。")
        lines.append("")

        # p值解释
        if p_adjusted < 0.001:
            p_text = "p<0.001"
        else:
            p_text = f"p={p_adjusted:.3f}"

        if "校正" in method:
            lines.append(f"经多重比较校正后，率差{p_text}。")
        else:
            lines.append(f"率差{p_text}。")

        lines.append("")

        # 统计学意义通俗解释
        confidence = (1 - min(p_raw, 0.999)) * 100
        lines.append(f"通俗来说：在我们观察的患者中，{group1}确实比{group2}更容易发生{event_name}，")
        lines.append(f"这个结论可靠的把握有{confidence:.1f}%。")

        # 研究类型说明
        if study_type == "确证性":
            lines.append("")
            lines.append("本结论基于确证性研究设计，具有较强的证据效力。")
        else:
            lines.append("")
            lines.append("本研究为探索性研究，结论需进一步验证。")

        return "\n".join(lines)

    def interpret_fisher(self, results: Dict[str, Any]) -> str:
        """解释Fisher精确检验结果"""
        results_copy = results.copy()
        results_copy["method"] = "Fisher精确检验（样本量较小）"
        return self.interpret(results_copy)

    def interpret_mcnemar(self, results: Dict[str, Any]) -> str:
        """解释McNemar检验（配对率比较）结果"""
        method = "McNemar检验（配对率比较）"
        group1 = results.get("group1_name", "第一次测量")
        group2 = results.get("group2_name", "第二次测量")
        rates = results.get("rates", {})
        stats = results.get("statistics", {})
        stats["chi2"] = stats.get("chi2", 0)  # McNemar使用卡方统计量
        p_raw = stats.get("p_raw", 1)

        group1_data = rates.get(group1, {"rate": 0, "n": 0})
        group2_data = rates.get(group2, {"rate": 0, "n": 0})

        rate1 = group1_data.get("rate", 0)
        rate2 = group2_data.get("rate", 0)
        rate_diff = rate2 - rate1

        lines = []

        direction = "增加" if rate_diff > 0 else "降低"
        abs_diff = abs(rate_diff) * 100

        lines.append(f"配对数据分析显示，{group2}的{event_name if 'event_name' in results else '结局'}发生率（{rate2*100:.1f}%）较{group1}（{rate1*100:.1f}%）{direction}了约{abs_diff:.1f}个百分点。")
        lines.append("")

        if p_raw < 0.001:
            p_text = "p<0.001"
        else:
            p_text = f"p={p_raw:.3f}"

        lines.append(f"这一变化{p_text}，在配对设计中具有统计学意义。")
        lines.append("")

        confidence = (1 - min(p_raw, 0.999)) * 100
        lines.append(f"通俗来说：我们有{confidence:.1f}%的把握认为这种变化是真实存在的，")
        lines.append("而非随机波动所致。")

        return "\n".join(lines)
