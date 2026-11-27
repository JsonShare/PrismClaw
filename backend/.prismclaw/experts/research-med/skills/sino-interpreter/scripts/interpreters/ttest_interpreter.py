"""ttest_interpreter.py - t检验结果解释器"""

from typing import Dict, Any
import sys
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from effect_size_interpreter import EffectSizeInterpreter


class TTestInterpreter:
    """t检验结果解释器"""

    def __init__(self):
        self.effect_size_interpreter = EffectSizeInterpreter()

    def interpret(self, results: Dict[str, Any]) -> str:
        """解释t检验结果

        参数：
            results: t检验结果字典，包含：
                - method: 检验方法
                - group1_name, group2_name: 组名
                - means: {组名: {mean, sd, n}}
                - mean_difference: {value, ci}
                - statistics: {t, df, p_raw, p_adjusted, cohens_d}
                - sample_size: 总样本量
                - study_type: 研究类型

        返回：
            Plain Language解释字符串
        """
        method = results.get("method", "独立样本t检验")
        group1 = results.get("group1_name", "组1")
        group2 = results.get("group2_name", "组2")
        means = results.get("means", {})
        stats = results.get("statistics", {})
        mean_diff_data = results.get("mean_difference", {})
        sample_size = results.get("sample_size", 0)
        study_type = results.get("study_type", "探索性")

        # 提取数据
        group1_data = means.get(group1, {"mean": 0, "sd": 0, "n": 0})
        group2_data = means.get(group2, {"mean": 0, "sd": 0, "n": 0})

        mean1 = group1_data.get("mean", 0)
        sd1 = group1_data.get("sd", 0)
        n1 = group1_data.get("n", 0)
        mean2 = group2_data.get("mean", 0)
        sd2 = group2_data.get("sd", 0)
        n2 = group2_data.get("n", 0)

        mean_diff = mean_diff_data.get("value", mean1 - mean2)
        ci = mean_diff_data.get("ci", [0, 0])
        ci_low, ci_high = ci[0], ci[1] if len(ci) > 1 else ci[0]

        t_stat = stats.get("t", 0)
        df = stats.get("df", 0)
        p_raw = stats.get("p_raw", 1)
        p_adjusted = stats.get("p_adjusted", p_raw)
        cohens_d = stats.get("cohens_d", 0)

        # 生成解释
        lines = []

        # 核心发现
        direction = "高" if mean_diff > 0 else "低"
        abs_diff = abs(mean_diff)

        lines.append(f"{group1}的均值（{mean1:.1f}±{sd1:.1f}）比{group2}（{mean2:.1f}±{sd2:.1f}）{direction}了约{abs_diff:.1f}。")
        lines.append("")

        # 效应量解释（核心）
        effect_interpretation = self.effect_size_interpreter.interpret_cohens_d(cohens_d)
        lines.append(f"效应量为Cohen's d = {cohens_d:.2f}，{effect_interpretation}。")
        lines.append("")

        # 置信区间解释
        lines.append(f"两组差异的95%置信区间为{ci_low:.1f}至{ci_high:.1f}，")
        lines.append(f"说明真实的差异最可能在{ci_low:.1f}到{ci_high:.1f}之间。")
        lines.append("")

        # p值解释
        if p_adjusted < 0.001:
            p_text = "p<0.001"
        else:
            p_text = f"p={p_adjusted:.3f}"

        if "校正" in method or "BH" in method or "Bonferroni" in method:
            lines.append(f"经多重比较校正后，差异{p_text}。")
        else:
            lines.append(f"差异{p_text}。")

        lines.append("")

        # 统计学意义通俗解释
        confidence = (1 - min(p_raw, 0.999)) * 100
        lines.append(f"通俗来说：我们有{confidence:.1f}%的把握认为{group1}真的比{group2}{direction}，")
        lines.append("撇开运气成分，这个差异是真实存在的。")

        # 研究类型说明
        if study_type == "确证性":
            lines.append("")
            lines.append("本结论基于确证性研究设计，具有较强的证据效力。")
        else:
            lines.append("")
            lines.append("本研究为探索性研究，结论需进一步验证。")

        return "\n".join(lines)

    def interpret_asymmetric(self, results: Dict[str, Any]) -> str:
        """解释非对称结果（如Welch's t检验）"""
        results_copy = results.copy()
        results_copy["method"] = "Welch's t检验（方差不齐校正）"
        return self.interpret(results_copy)

    def interpret_paired(self, results: Dict[str, Any]) -> str:
        """解释配对t检验结果"""
        method = "配对t检验"
        group1 = results.get("group1_name", "测量前")
        group2 = results.get("group2_name", "测量后")
        means = results.get("means", {})
        stats = results.get("statistics", {})
        mean_diff_data = results.get("mean_difference", {})
        sample_size = results.get("sample_size", 0)

        group1_data = means.get(group1, {"mean": 0, "sd": 0, "n": 0})
        group2_data = means.get(group2, {"mean": 0, "sd": 0, "n": 0})

        mean1 = group1_data.get("mean", 0)
        mean2 = group2_data.get("mean", 0)
        mean_diff = mean_diff_data.get("value", mean2 - mean1)
        ci = mean_diff_data.get("ci", [0, 0])
        ci_low, ci_high = ci[0], ci[1] if len(ci) > 1 else ci[0]
        cohens_d = stats.get("cohens_d", 0)
        p_raw = stats.get("p_raw", 1)

        lines = []

        direction = "增加" if mean_diff > 0 else "降低"
        abs_diff = abs(mean_diff)

        lines.append(f"配对测量结果显示，{group2}较{group1}平均{direction}了约{abs_diff:.1f}。")
        lines.append("")

        effect_interpretation = self.effect_size_interpreter.interpret_cohens_d(cohens_d)
        lines.append(f"效应量为Cohen's d = {cohens_d:.2f}，{effect_interpretation}。")
        lines.append("")

        if p_raw < 0.001:
            p_text = "p<0.001"
        else:
            p_text = f"p={p_raw:.3f}"

        lines.append(f"差异{p_text}，在配对设计中，这一差异具有统计学意义。")
        lines.append("")

        confidence = (1 - min(p_raw, 0.999)) * 100
        lines.append(f"通俗来说：我们有{confidence:.1f}%的把握认为这种变化是真实的，")
        lines.append(f"而非随机波动所致。")

        return "\n".join(lines)
