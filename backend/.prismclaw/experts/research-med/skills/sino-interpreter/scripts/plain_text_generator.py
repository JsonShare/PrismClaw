"""plain_text_generator.py - 核心Plain Language生成器

将统计结果（t值、p值、效应量、置信区间）转化为通俗中文解释。

核心原则：
1. 效应量优先于P值
2. 置信区间必须报告（95%CI）
3. 绝对效应量优先于相对效应量
4. 所有结论必须注明样本量和研究类型
"""

from typing import Dict, Any, Optional
from effect_size_interpreter import EffectSizeInterpreter


class PlainTextGenerator:
    """Plain Language文本生成器"""

    def __init__(self):
        self.effect_size_interpreter = EffectSizeInterpreter()

    def generate_ttest_interpretation(
        self,
        group1_name: str,
        group2_name: str,
        mean1: float,
        mean2: float,
        sd1: float,
        sd2: float,
        n1: int,
        n2: int,
        mean_diff: float,
        ci_low: float,
        ci_high: float,
        cohens_d: float,
        p_value: float,
        t_stat: float,
        df: int,
        test_method: str = "独立样本t检验"
    ) -> str:
        """生成t检验的Plain Language解释

        示例输出：
        新药组的平均得分（85.2±6.8）比安慰剂组（72.9±7.1）高了约12分。
        这个差异在统计学上是"显著的"（p=0.005），
        意味着这种差异不太可能是巧合造成的。
        """
        # 计算绝对差异
        abs_diff = abs(mean_diff)
        direction = "高" if mean_diff > 0 else "低"

        # 效应量解释
        effect_interpretation = self.effect_size_interpreter.interpret_cohens_d(cohens_d)

        # 计算置信区间宽度
        ci_width = ci_high - ci_low

        # 生成通俗解释
        lines = []

        # 核心发现
        lines.append(f"{group1_name}的平均得分（{mean1:.1f}±{sd1:.1f}）比{group2_name}（{mean2:.1f}±{sd2:.1f}）{direction}了约{abs_diff:.1f}分。")
        lines.append("")

        # 统计学意义（先效应量后p值）
        lines.append(f"效应量为Cohen's d = {cohens_d:.2f}，{effect_interpretation}。")
        lines.append("")

        # 置信区间解释
        lines.append(f"两组差异的95%置信区间为{ci_low:.1f}至{ci_high:.1f}分，")
        lines.append(f"说明真实的差异最可能在{ci_low:.1f}分到{ci_high:.1f}分之间。")
        lines.append("")

        # p值解释（用通俗语言）
        confidence = (1 - p_value) * 100
        lines.append(f"这个差异在统计学上是\"显著的\"（p={p_value:.3f}），")
        lines.append(f"意味着这种差异不太可能是巧合造成的。")
        lines.append("")
        lines.append(f"通俗来说：我们有{confidence:.1f}%的把握认为{goup1_name}真的比{goup2_name}{direction}，")
        lines.append("撇开运气成分，这个差异是真实存在的。")

        return "\n".join(lines)

    def generate_chi2_interpretation(
        self,
        group1_name: str,
        group2_name: str,
        rate1: float,
        rate2: float,
        n1: int,
        n2: int,
        rate_diff: float,
        ci_low: float,
        ci_high: float,
        cramers_v: float,
        p_value: float,
        chi2_stat: float,
        df: int,
        event_name: str = "事件",
        test_method: str = "卡方检验"
    ) -> str:
        """生成卡方检验的Plain Language解释

        示例输出：
        新药组的有效率（75%）比安慰剂组（52%）高了约23个百分点。
        这个差异有统计学意义（p=0.012）。
        """
        # 计算绝对差异
        abs_diff = abs(rate_diff) * 100
        direction = "高" if rate_diff > 0 else "低"

        # 效应量解释
        effect_interpretation = self.effect_size_interpreter.interpret_cramers_v(cramers_v)

        # 生成通俗解释
        lines = []

        # 核心发现
        lines.append(f"{group1_name}的{event_name}发生率（{rate1*100:.1f}%）比{group2_name}（{rate2*100:.1f}%）{direction}了约{abs_diff:.1f}个百分点。")
        lines.append("")

        # 效应量解释
        lines.append(f"效应量为Cramér's V = {cramers_v:.2f}，{effect_interpretation}。")
        lines.append("")

        # 置信区间解释
        lines.append(f"两组率差的95%置信区间为{ci_low*100:.1f}至{ci_high*100:.1f}个百分点，")
        lines.append(f"说明真实的率差最可能在{ci_low*100:.1f}到{ci_high*100:.1f}个百分点之间。")
        lines.append("")

        # p值解释
        confidence = (1 - p_value) * 100
        lines.append(f"这个差异有统计学意义（p={p_value:.3f}）。")
        lines.append("")
        lines.append(f"通俗来说：在我们观察的患者中，{group1_name}确实比{group2_name}更容易发生{event_name}，")
        lines.append(f"这个结论可靠的把握有{confidence:.1f}%。")

        return "\n".join(lines)

    def generate_anova_interpretation(
        self,
        group_means: Dict[str, tuple],  # {group_name: (mean, sd, n)}
        eta_squared: float,
        p_value: float,
        f_stat: float,
        df_between: int,
        df_within: int,
        test_method: str = "单因素ANOVA"
    ) -> str:
        """生成ANOVA的Plain Language解释"""
        effect_interpretation = self.effect_size_interpreter.interpret_eta_squared(eta_squared)

        lines = []
        lines.append("各组均值比较：")
        lines.append("")

        for group_name, (mean, sd, n) in group_means.items():
            lines.append(f"  - {group_name}（n={n}）：{mean:.2f} ± {sd:.2f}")

        lines.append("")
        lines.append(f"效应量为η² = {eta_squared:.3f}，{effect_interpretation}。")
        lines.append(f"F({df_between}, {df_within}) = {f_stat:.2f}, p = {p_value:.3f}。")

        return "\n".join(lines)

    def generate_logistic_interpretation(
        self,
        predictor_name: str,
        or_value: float,
        ci_low: float,
        ci_high: float,
        p_value: float,
        beta: float,
        reference_value: Optional[str] = None,
        event_name: str = "结局"
    ) -> str:
        """生成logistic回归的Plain Language解释

        示例输出：
        有高血压的人，心血管事件发生的风险是正常血压的人的2.34倍。
        这个结论的可靠程度很高（p<0.001），
        而且我们有很大把握说真实的风险倍数在1.47倍到3.72倍之间——
        即使考虑到抽样的误差，最保守估计也接近1.5倍。
        """
        lines = []

        # 确定是危险因素还是保护因素
        if or_value > 1:
            direction = "高"
            risk_increase = (or_value - 1) * 100
            lines.append(f"有{predictor_name}的人，{event_name}的风险是正常人的${or_value:.2f}倍。")
        else:
            direction = "低"
            risk_decrease = (1 - or_value) * 100
            lines.append(f"有{predictor_name}的人，{event_name}的风险是正常人的{or_value:.2f}倍，")
            lines.append(f"风险降低了{riisk_decrease:.1f}%。")

        lines.append("")

        # 置信区间解释（关键：说明即使最保守估计也有意义）
        if ci_low > 1:
            lines.append(f"这个结论的可靠程度很高（p={p_value:.3f}），")
            lines.append(f"而且我们有很大把握说真实的风险倍数在{ci_low:.2f}倍到{ci_high:.2f}倍之间——")
            lines.append(f"即使考虑到抽样的误差，最保守估计也接近{ci_low:.2f}倍。")
        elif ci_high < 1:
            lines.append(f"这个结论的可靠程度很高（p={p_value:.3f}），")
            lines.append(f"而且我们有很大把握说真实的风险降低幅度在{abs(ci_high)*100:.1f}%到{abs(ci_low)*100:.1f}%之间。")
        else:
            lines.append(f"这个关联有统计学意义（p={p_value:.3f}），")
            lines.append(f"但置信区间跨越1，需要谨慎解读。")

        return "\n".join(lines)

    def generate_linear_regression_interpretation(
        self,
        predictor_name: str,
        beta: float,
        ci_low: float,
        ci_high: float,
        std_beta: float,
        p_value: float,
        unit: str = "单位",
        outcome_name: str = "结局"
    ) -> str:
        """生成线性回归的Plain Language解释

        示例输出：
        每增加1个年龄单位，疗效评分平均降低0.15分。
        这个关联有统计学意义（p=0.032），
        标准化系数为β'=-0.12，表示年龄的影响力较小。
        """
        lines = []

        # 确定方向
        if beta > 0:
            direction = "增加"
        else:
            direction = "降低"

        # 核心发现
        lines.append(f"{predictor_name}每{unit}，{outcome_name}平均{direction}{abs(beta):.2f}个单位。")

        # 置信区间
        lines.append(f"95%置信区间为{ci_low:.2f}至{ci_high:.2f}，")
        lines.append(f"说明真实的效应最可能在{ci_low:.2f}到{ci_high:.2f}之间。")

        # 效应大小解释
        effect_interpretation = self.effect_size_interpreter.interpret_std_beta(std_beta)
        lines.append(f"")
        lines.append(f"标准化系数为β'={std_beta:.2f}，{effect_interpretation}。")

        # p值
        if p_value < 0.05:
            lines.append(f"这个关联有统计学意义（p={p_value:.3f}）。")
        else:
            lines.append(f"这个关联无统计学意义（p={p_value:.3f}）。")

        return "\n".join(lines)

    def generate_cox_interpretation(
        self,
        predictor_name: str,
        hr_value: float,
        ci_low: float,
        ci_high: float,
        p_value: float,
        median_survival_group1: Optional[float] = None,
        median_survival_group2: Optional[float] = None,
        event_name: str = "死亡"
    ) -> str:
        """生成Cox回归的Plain Language解释

        示例输出：
        接受手术治疗的患者，死亡风险比保守治疗组低了33%。
        换句话说：手术治疗组的患者存活率更高，
        这个差异不是偶然的（p=0.006）。
        """
        lines = []

        # 计算风险变化
        if hr_value < 1:
            risk_change = (1 - hr_value) * 100
            lines.append(f"{predictor_name}的{event_name}风险比对照组低了{risk_change:.1f}%。")
        else:
            risk_change = (hr_value - 1) * 100
            lines.append(f"{predictor_name}的{event_name}风险比对照组高了{risk_change:.1f}%。")

        lines.append("")

        # 中位生存期（如果提供）
        if median_survival_group1 and median_survival_group2:
            lines.append(f"对应地，{predictor_name}组的中位生存期为{median_survival_group1:.1f}个月，")
            lines.append(f"而对照组为{median_survival_group2:.1f}个月。")
            lines.append("")

        # 统计学意义
        lines.append(f"这个差异不是偶然的（p={p_value:.3f}）。")

        # 置信区间解释
        if ci_low < 1 and ci_high < 1:
            lower_bound = (1 - ci_high) * 100
            upper_bound = (1 - ci_low) * 100
            lines.append(f"即便考虑到研究的局限性，")
            lines.append(f"我们有把握说{predictor_name}带来的风险降低至少在{lower_bound:.1f}%以上，最多可能达到{upper_bound:.1f}%。")
        elif ci_low > 1 and ci_high > 1:
            lower_bound = (ci_low - 1) * 100
            upper_bound = (ci_high - 1) * 100
            lines.append(f"即便考虑到研究的局限性，")
            lines.append(f"我们有把握说{predictor_name}带来的风险增加至少在{lower_bound:.1f}%以上，最多可能达到{upper_bound:.1f}%。")

        return "\n".join(lines)

    def generate_confidence_interval_summary(
        self,
        effect_size: float,
        ci_low: float,
        ci_high: float,
        effect_type: str = "差异"
    ) -> str:
        """生成置信区间总结"""
        width = ci_high - ci_low
        lines = []
        lines.append(f"{effect_type}的95%置信区间为[{ci_low:.2f}, {ci_high:.2f}]，")
        lines.append(f"区间宽度为{width:.2f}，")
        if width < abs(effect_size) * 0.5:
            lines.append("置信区间较窄，说明估计精度较高。")
        elif width > abs(effect_size) * 2:
            lines.append("置信区间较宽，说明估计精度较低，解读需谨慎。")
        else:
            lines.append("置信区间宽度适中，估计精度一般。")
        return "\n".join(lines)
