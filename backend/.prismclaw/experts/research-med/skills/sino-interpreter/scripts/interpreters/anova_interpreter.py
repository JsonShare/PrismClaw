"""anova_interpreter.py - ANOVA结果解释器"""

from typing import Dict, Any, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from effect_size_interpreter import EffectSizeInterpreter


class AnovaInterpreter:
    """ANOVA结果解释器"""

    def __init__(self):
        self.effect_size_interpreter = EffectSizeInterpreter()

    def interpret(self, results: Dict[str, Any]) -> str:
        """解释ANOVA结果

        参数：
            results: ANOVA结果字典，包含：
                - method: 检验方法
                - variable: 变量名
                - groups: [{name, mean, sd, n}]
                - statistics: {f, df_between, df_within, p_raw, eta_squared, omega_squared}
                - post_hoc: [{group1, group2, mean_diff, p_adjusted, cohens_d}]
                - sample_size: 总样本量
                - study_type: 研究类型

        返回：
            Plain Language解释字符串
        """
        method = results.get("method", "单因素ANOVA")
        variable = results.get("variable", "指标")
        groups = results.get("groups", [])
        stats = results.get("statistics", {})
        post_hoc = results.get("post_hoc", [])
        sample_size = results.get("sample_size", 0)
        study_type = results.get("study_type", "探索性")

        # 提取数据
        f_stat = stats.get("f", 0)
        df_between = stats.get("df_between", 0)
        df_within = stats.get("df_within", 0)
        p_raw = stats.get("p_raw", 1)
        p_adjusted = stats.get("p_adjusted", p_raw)
        eta_squared = stats.get("eta_squared", 0)
        omega_squared = stats.get("omega_squared", eta_squared)

        lines = []

        # 总体发现
        lines.append(f"多组（{len(groups)}组）比较采用{method}进行。")
        lines.append("")

        # 各组描述
        lines.append("各组均值：")
        for g in groups:
            lines.append(f"  - {g.get('name', '组')}: {g.get('mean', 0):.2f} ± {g.get('sd', 0):.2f} (n={g.get('n', 0)})")
        lines.append("")

        # 效应量解释
        effect_interpretation = self.effect_size_interpreter.interpret_eta_squared(eta_squared)
        lines.append(f"效应量为η² = {eta_squared:.3f}，{effect_interpretation}。")
        lines.append("")

        # 统计量
        if p_adjusted < 0.001:
            p_text = "p<0.001"
        else:
            p_text = f"p={p_adjusted:.3f}"

        lines.append(f"F({df_between}, {df_within}) = {f_stat:.2f}, {p_text}。")
        lines.append("")

        # 主要发现
        if p_adjusted < 0.05:
            # 找出差异最大的两组
            if post_hoc and len(post_hoc) > 0:
                # 按效应量排序
                sorted_hoc = sorted(post_hoc, key=lambda x: abs(x.get("cohens_d", 0)), reverse=True)
                top_pair = sorted_hoc[0]

                g1 = top_pair.get("group1", "组1")
                g2 = top_pair.get("group2", "组2")
                diff = top_pair.get("mean_diff", 0)
                direction = "高于" if diff > 0 else "低于"
                top_effect = top_pair.get("cohens_d", 0)

                lines.append(f"组间差异有统计学意义。")
                lines.append(f"其中，{g1}与{g2}差异最大，{g1}的均值为{g2}{direction}约{abs(diff):.2f}。")
                lines.append("")

                effect_interpretation = self.effect_size_interpreter.interpret_cohens_d(top_effect)
                lines.append(f"主要组间差异的效应量为Cohen's d = {top_effect:.2f}，{effect_interpretation}。")
            else:
                lines.append("组间差异有统计学意义。")

            lines.append("")
            confidence = (1 - min(p_raw, 0.999)) * 100
            lines.append(f"通俗来说：我们有{confidence:.1f}%的把握认为至少有两组的均值存在真实差异。")
        else:
            lines.append("组间差异无统计学意义。")
            lines.append("通俗来说：我们没有足够的证据认为这些组的均值存在真实差异。")

        # 研究类型说明
        if study_type == "确证性":
            lines.append("")
            lines.append("本结论基于确证性研究设计，具有较强的证据效力。")
        else:
            lines.append("")
            lines.append("本研究为探索性研究，结论需进一步验证。")

        return "\n".join(lines)

    def interpret_repeated_measures(self, results: Dict[str, Any]) -> str:
        """解释重复测量ANOVA结果"""
        results_copy = results.copy()
        results_copy["method"] = "重复测量ANOVA"
        return self.interpret(results_copy)

    def interpret_two_way(self, results: Dict[str, Any]) -> str:
        """解释双因素ANOVA结果"""
        results_copy = results.copy()
        results_copy["method"] = "双因素ANOVA"
        return self.interpret(results_copy)
