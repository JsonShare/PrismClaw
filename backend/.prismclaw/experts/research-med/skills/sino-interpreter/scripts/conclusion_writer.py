"""conclusion_writer.py - 结论撰写器（医学论文风格）

以医学论文"讨论"部分的风格撰写结论。

结构：主要发现 → 次要发现 → 局限性声明

禁止使用词汇：
- "证明"、"证实" → 用"提示"、"观察到"、"存在差异"替代
- "高度显著" → 用"有统计学意义"或"p<0.01"替代
"""


class ConclusionWriter:
    """结论撰写器"""

    # 禁止词汇及其替代词
    FORBIDDEN_WORDS = {
        "证明": "提示",
        "证实": "支持",
        "高度显著": "有统计学意义（p<0.01）",
        "显著提高": "平均提高",
        "显著降低": "平均降低",
        "显著差异": "差异有统计学意义",
    }

    def __init__(self):
        self._compile_substitution_patterns()

    def _compile_substitution_patterns(self):
        """编译替代模式"""
        import re
        self.patterns = [(re.compile(kw), repl) for kw, repl in self.FORBIDDEN_WORDS.items()]

    def sanitize_text(self, text: str) -> str:
        """替换禁止词汇"""
        for pattern, replacement in self.patterns:
            text = pattern.sub(replacement, text)
        return text

    def write_ttest_conclusion(
        self,
        results: dict,
        study_type: str = "探索性"
    ) -> str:
        """撰写t检验结论（论文讨论部分风格）

        参数：
            results: t检验结果字典
            study_type: 研究类型（探索性/确证性）
        """
        var_name = results.get("variable", "指标")
        group1 = results.get("group1_name", "组1")
        group2 = results.get("group2_name", "组2")
        mean1 = results.get("mean1", 0)
        mean2 = results.get("mean2", 0)
        mean_diff = results.get("mean_difference", {}).get("value", 0)
        ci_low = results.get("mean_difference", {}).get("ci", [0, 0])[0]
        ci_high = results.get("mean_difference", {}).get("ci", [0, 0])[1]
        cohens_d = results.get("statistics", {}).get("cohens_d", 0)
        p_value = results.get("statistics", {}).get("p_raw", 1)
        n1 = results.get("means", {}).get(group1, {}).get("n", 0)
        n2 = results.get("means", {}).get(group2, {}).get("n", 0)
        total_n = n1 + n2

        direction = "高于" if mean_diff > 0 else "低于"
        abs_diff = abs(mean_diff)

        lines = []

        # 主要发现
        lines.append("## 主要发现")
        lines.append("")
        lines.append(f"本研究发现，{group1}的{var_name}（{mean1:.1f}±{results.get('means', {}).get(group1, {}).get('sd', 0):.1f}）{direction}{group2}（{mean2:.1f}±{results.get('means', {}).get(group2, {}).get('sd', 0):.1f}），")
        lines.append(f"差值为{abs_diff:.1f}（95%CI: {ci_low:.1f}~{ci_high:.1f}）。")
        lines.append("")

        # 统计学意义
        if p_value < 0.001:
            p_text = "p<0.001"
        elif p_value < 0.01:
            p_text = f"p={p_value:.3f}"
        else:
            p_text = f"p={p_value:.3f}"

        lines.append(f"效应量Cohen's d为{cohens_d:.2f}，差异{p_text}。")

        # 效应量大小解释
        if abs(cohens_d) < 0.2:
            effect_comment = "但效应量较小，临床意义有限"
        elif abs(cohens_d) < 0.5:
            effect_comment = "效应量属中等水平，具有一定临床意义"
        elif abs(cohens_d) < 0.8:
            effect_comment = "效应量属中等偏大水平，具有较明显的临床意义"
        else:
            effect_comment = "效应量较大，临床意义明显"

        lines.append(f"{effect_comment}。")

        # 次要发现或亚组分析说明
        lines.append("")
        lines.append("## 结论")
        lines.append("")
        lines.append(f"本{study_type}研究结果表明，{group1}与{group2}在{var_name}方面存在差异，")
        lines.append(f"这一差异{'支持' if p_value < 0.05 else '提示需要进行进一步研究'}后续进一步验证。")

        # 局限性声明
        lines.append("")
        lines.append("## 局限性声明")
        lines.append("")
        lines.append(f"本研究为{study_type}研究，样本量为{total_n}例，")
        lines.append("结果的可推广性需要更大样本量的研究进一步验证。")
        lines.append("此外，未对混杂因素进行控制，结果解释需谨慎。")

        result = "\n".join(lines)
        return self.sanitize_text(result)

    def write_chi2_conclusion(
        self,
        results: dict,
        study_type: str = "探索性"
    ) -> str:
        """撰写卡方检验结论"""
        var_name = results.get("variable", "指标")
        group1 = results.get("group1_name", "组1")
        group2 = results.get("group2_name", "组2")
        rate1 = results.get("rates", {}).get(group1, {}).get("rate", 0)
        rate2 = results.get("rates", {}).get(group2, {}).get("rate", 0)
        n1 = results.get("rates", {}).get(group1, {}).get("n", 0)
        n2 = results.get("rates", {}).get(group2, {}).get("n", 0)
        cramers_v = results.get("statistics", {}).get("cramers_v", 0)
        p_value = results.get("statistics", {}).get("p_raw", 1)

        direction = "高于" if rate1 > rate2 else "低于"
        abs_diff = abs(rate1 - rate2) * 100

        lines = []

        # 主要发现
        lines.append("## 主要发现")
        lines.append("")
        lines.append(f"本研究发现，{group1}的{var_name}发生率（{rate1*100:.1f}%）{direction}{group2}（{rate2*100:.1f}%），")
        lines.append(f"差异为{abs_diff:.1f}个百分点。")
        lines.append("")

        # 统计学意义
        if p_value < 0.001:
            p_text = "p<0.001"
        else:
            p_text = f"p={p_value:.3f}"

        lines.append(f"效应量Cramér's V为{cramers_v:.2f}，差异{p_text}。")

        # 结论
        lines.append("")
        lines.append("## 结论")
        lines.append("")
        if p_value < 0.05:
            lines.append(f"本{study_type}研究结果表明，{group1}与{group2}在{var_name}方面存在差异，")
            lines.append(f"这一差异{'支持' if p_value < 0.01 else '提示需要进行进一步验证'}后续进一步研究。")
        else:
            lines.append(f"本{study_type}研究未发现{group1}与{group2}在{var_name}方面存在显著差异。")

        # 局限性声明
        lines.append("")
        lines.append("## 局限性声明")
        lines.append("")
        lines.append(f"本研究为{study_type}研究，")
        lines.append("结果的可推广性需要更大样本量的研究进一步验证。")

        result = "\n".join(lines)
        return self.sanitize_text(result)

    def write_regression_conclusion(
        self,
        results: dict,
        study_type: str = "探索性"
    ) -> str:
        """撰写回归分析结论"""
        outcome_var = results.get("outcome_var", "结局变量")
        predictors = results.get("significant_predictors", [])
        r_squared = results.get("model_fit", {}).get("r_squared", 0)
        adjusted_r_squared = results.get("model_fit", {}).get("adjusted_r_squared", 0)
        n = results.get("n", 0)

        lines = []

        # 主要发现
        lines.append("## 主要发现")
        lines.append("")
        lines.append(f"多因素分析结果显示，{len(predictors)}个因素与{outcome_var}独立相关。")
        lines.append("")

        for pred in predictors[:5]:  # 最多列出5个
            var_name = pred.get("variable", "变量")
            beta = pred.get("beta", 0)
            p_value = pred.get("p_value", 1)
            direction = "正向" if beta > 0 else "负向"

            if p_value < 0.001:
                p_text = "p<0.001"
            else:
                p_text = f"p={p_value:.3f}"

            lines.append(f"- {var_name}（β={beta:.3f}，{p_text}）：{direction}关联")

        # 模型解释力
        lines.append("")
        lines.append(f"模型解释力为R²={r_squared:.3f}（调整R²={adjusted_r_squared:.3f}），")
        lines.append(f"说明这些因素共同解释了{r_squared*100:.1f}%的{outcome_var}变异。")

        # 结论
        lines.append("")
        lines.append("## 结论")
        lines.append("")
        if len(predictors) > 0:
            lines.append(f"本{study_type}研究识别出{len(predictors)}个与{outcome_var}独立相关的因素，")
            lines.append("这些发现为后续研究提供了重要线索。")
        else:
            lines.append(f"本{study_type}研究未发现与{outcome_var}显著独立相关的因素。")

        # 局限性声明
        lines.append("")
        lines.append("## 局限性声明")
        lines.append("")
        lines.append(f"本研究为{study_type}研究，样本量为{n}例，")
        lines.append("可能存在未测量的混杂因素，结果解释需谨慎。")

        result = "\n".join(lines)
        return self.sanitize_text(result)

    def write_conclusion(
        self,
        results: dict,
        result_type: str
    ) -> str:
        """根据结果类型撰写结论"""
        study_type = results.get("study_type", "探索性")

        if result_type == "differential":
            method = results.get("method", "")
            if "t检验" in method or "t test" in method.lower():
                return self.write_ttest_conclusion(results, study_type)
            elif "卡方" in method or "chi" in method.lower():
                return self.write_chi2_conclusion(results, study_type)
            else:
                # 默认t检验风格
                return self.write_ttest_conclusion(results, study_type)

        elif result_type == "regression":
            return self.write_regression_conclusion(results, study_type)

        elif result_type == "survival":
            # Cox回归风格结论
            return self.write_regression_conclusion(results, study_type)

        else:
            return "无法确定结果类型进行结论撰写。"
