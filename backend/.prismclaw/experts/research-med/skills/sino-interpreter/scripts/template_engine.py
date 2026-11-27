"""template_engine.py - 模板引擎（可配置的Plain Language模板）

提供可配置的Plain Language解释模板。
用户可以自定义模板格式和内容。
"""

from typing import Dict, Any, Optional
from datetime import datetime


class TemplateEngine:
    """Plain Language模板引擎"""

    DEFAULT_TEMPLATES = {
        "differential": {
            "ttest": """# 结果解释：{variable}组间比较

## 统计结果摘要
- **分析方法**：{test_method}
- **{group1_name}（n={n1}）**：{mean1:.1f} ± {sd1:.1f}
- **{group2_name}（n={n2}）**：{mean2:.1f} ± {sd2:.1f}
- **均值差**：{mean_diff:.1f}（95%CI: {ci_low:.1f}~{ci_high:.1f}）
- **统计量**：t={t_stat:.2f}, df={df}, p={p_value:.3f}
- **效应量**：Cohen's d = {cohens_d:.2f}

## 通俗解释
{plain_text}

## 临床意义
基于这项分析，{group1_name}在{variable}方面展现出{clinical_interpretation}。
{additional_clinical_notes}

## 向患者解释（可选）
面向患者时，可以这样解释：
{patient_facing_text}
""",
            "chi2": """# 结果解释：{variable}组间率比较

## 统计结果摘要
- **分析方法**：{test_method}
- **{group1_name}（n={n1}）**：{rate1:.1%}
- **{group2_name}（n={n2}）**：{rate2:.1%}
- **率差**：{rate_diff:.1%}（95%CI: {ci_low:.1%}~{ci_high:.1%}）
- **统计量**：χ²={chi2_stat:.2f}, df={df}, p={p_value:.3f}
- **效应量**：Cramér's V = {cramers_v:.2f}

## 通俗解释
{plain_text}

## 临床意义
{clinical_interpretation}

## 向患者解释（可选）
面向患者时，可以这样解释：
{patient_facing_text}
""",
            "anova": """# 结果解释：{variable}多组比较

## 统计结果摘要
- **分析方法**：{test_method}
- **组数**：{n_groups}
- **效应量**：η² = {eta_squared:.3f}
- **统计量**：F={f_stat:.2f}, df={df_between},{df_within}, p={p_value:.3f}

{group_means_table}

## 通俗解释
{plain_text}

## 临床意义
{clinical_interpretation}
"""
        },
        "regression": {
            "linear": """# 结果解释：{outcome_var}回归分析

## 模型概况
- **因变量**：{outcome_var}
- **样本量**：{n}
- **进入模型的变量数**：{n_predictors}
- **模型解释力**：R²={r_squared:.3f}（调整R²={adjusted_r_squared:.3f}）

## 显著影响因素
{significant_predictors_table}

## 通俗解释
{plain_text}

## 临床意义
{clinical_interpretation}
""",
            "logistic": """# 结果解释：{outcome_var} logistic回归分析

## 模型概况
- **因变量**：{outcome_var}
- **样本量**：{n}
- **事件数**：{n_events}
- **模型拟合**：Hosmer-Lemeshow p={hl_p:.3f}
- **模型区分度**：AUC={auc:.3f}

## 显著影响因素
{significant_predictors_table}

## 通俗解释
{plain_text}

## 临床意义
{clinical_interpretation}
"""
        },
        "survival": {
            "cox": """# 结果解释：{outcome_var}生存分析

## 模型概况
- **结局事件**：{event_name}
- **样本量**：{n}
- **事件数**：{n_events}
- **比例风险假设**：{ph_assumption}

## 显著影响因素
{significant_predictors_table}

## 通俗解释
{plain_text}

## 临床意义
{clinical_interpretation}
"""
        }
    }

    def __init__(self, custom_templates: Optional[Dict] = None):
        """初始化模板引擎

        参数：
            custom_templates: 自定义模板字典
        """
        self.templates = self.DEFAULT_TEMPLATES.copy()
        if custom_templates:
            self._merge_templates(custom_templates)

    def _merge_templates(self, custom_templates: Dict):
        """合并自定义模板"""
        for category, templates in custom_templates.items():
            if category in self.templates:
                self.templates[category].update(templates)
            else:
                self.templates[category] = templates

    def render(
        self,
        result_type: str,
        plain_text: str,
        conclusion: str,
        raw_results: Dict[str, Any]
    ) -> str:
        """渲染完整解释模板"""
        if result_type not in self.templates:
            return plain_text  # fallback

        # 根据具体分析方法选择子模板
        method = raw_results.get("method", "").lower()

        if result_type == "differential":
            if "t检验" in method or "t test" in method:
                template_key = "ttest"
            elif "卡方" in method or "chi" in method:
                template_key = "chi2"
            elif "anova" in method:
                template_key = "anova"
            else:
                template_key = "ttest"  # 默认
        elif result_type == "regression":
            if "logistic" in method:
                template_key = "logistic"
            else:
                template_key = "linear"
        elif result_type == "survival":
            template_key = "cox"
        else:
            return plain_text

        template = self.templates.get(result_type, {}).get(template_key)
        if not template:
            return plain_text

        # 填充模板变量
        context = self._build_context(result_type, plain_text, conclusion, raw_results)

        try:
            return template.format(**context)
        except KeyError as e:
            # 如果模板变量缺失，回退到plain_text
            return plain_text

    def _build_context(
        self,
        result_type: str,
        plain_text: str,
        conclusion: str,
        raw_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """构建模板上下文"""
        context = {
            "plain_text": plain_text,
            "conclusion": conclusion,
            "generated_date": datetime.now().strftime("%Y-%m-%d"),
        }

        # 添加通用变量
        context["n"] = raw_results.get("n", raw_results.get("total_n", 0))
        context["study_type"] = raw_results.get("study_type", "探索性")

        if result_type == "differential":
            self._add_differential_context(context, raw_results)
        elif result_type == "regression":
            self._add_regression_context(context, raw_results)
        elif result_type == "survival":
            self._add_survival_context(context, raw_results)

        return context

    def _add_differential_context(self, context: Dict, results: Dict):
        """添加差异分析相关上下文"""
        context["variable"] = results.get("variable", "指标")
        context["test_method"] = results.get("method", "统计检验")
        context["group1_name"] = results.get("group1_name", results.get("group1", "组1"))
        context["group2_name"] = results.get("group2_name", results.get("group2", "组2"))

        means = results.get("means", {})
        group1_data = means.get(context["group1_name"], {})
        group2_data = means.get(context["group2_name"], {})

        context["mean1"] = group1_data.get("mean", 0)
        context["sd1"] = group1_data.get("sd", 0)
        context["n1"] = group1_data.get("n", 0)
        context["mean2"] = group2_data.get("mean", 0)
        context["sd2"] = group2_data.get("sd", 0)
        context["n2"] = group2_data.get("n", 0)

        mean_diff = results.get("mean_difference", {})
        context["mean_diff"] = mean_diff.get("value", 0)
        ci = mean_diff.get("ci", [0, 0])
        context["ci_low"] = ci[0]
        context["ci_high"] = ci[1]

        stats = results.get("statistics", {})
        context["t_stat"] = stats.get("t", stats.get("f", 0))
        context["df"] = stats.get("df", stats.get("df_between", 0))
        context["p_value"] = stats.get("p_raw", stats.get("p_adjusted", 1))
        context["cohens_d"] = stats.get("cohens_d", 0)

        # 临床解释
        effect_interpretation = self._interpret_effect_size(context["cohens_d"], "cohens_d")
        context["clinical_interpretation"] = effect_interpretation
        context["additional_clinical_notes"] = ""

        # 患者面向文本
        context["patient_facing_text"] = self._generate_patient_text(results)

    def _add_regression_context(self, context: Dict, results: Dict):
        """添加回归分析相关上下文"""
        context["outcome_var"] = results.get("outcome_var", "结局变量")
        context["n_predictors"] = len(results.get("predictors", []))

        model_fit = results.get("model_fit", {})
        context["r_squared"] = model_fit.get("r_squared", 0)
        context["adjusted_r_squared"] = model_fit.get("adjusted_r_squared", 0)

        # 构建显著影响因素表格
        predictors = results.get("predictors", [])
        significant = [p for p in predictors if p.get("p_value", 1) < 0.05]

        table_rows = []
        for p in significant[:10]:  # 最多10个
            var = p.get("variable", "变量")
            beta = p.get("beta", 0)
            ci = p.get("ci", [0, 0])
            pval = p.get("p_value", 1)
            table_rows.append(f"| {var} | {beta:.3f} | [{ci[0]:.2f}, {ci[1]:.2f}] | {pval:.3f} |")

        context["significant_predictors_table"] = "| 变量 | β | 95%CI | p值 |\n|--------|----|--------|-----|\n" + "\n".join(table_rows) if table_rows else "无显著影响因素"

        context["plain_text"] = results.get("interpretation", context.get("plain_text", ""))

    def _add_survival_context(self, context: Dict, results: Dict):
        """添加生存分析相关上下文"""
        context["event_name"] = results.get("event_name", "结局事件")
        context["n_events"] = results.get("n_events", 0)
        context["ph_assumption"] = results.get("ph_assumption", "待验证")

        self._add_regression_context(context, results)

    def _interpret_effect_size(self, effect_size: float, effect_type: str) -> str:
        """解释效应量的临床意义"""
        if effect_type == "cohens_d":
            abs_d = abs(effect_size)
            if abs_d < 0.2:
                return "与组间差异较小"
            elif abs_d < 0.5:
                return "与组间差异中等"
            elif abs_d < 0.8:
                return "与组间差异较大"
            else:
                return "与组间差异很大"
        return ""

    def _generate_patient_text(self, results: Dict) -> str:
        """生成患者面向的解释文本"""
        # 这是一个简化版本，实际应该根据具体结果生成
        return "根据研究结果，您所在的治疗组与对照组之间存在一定的差异。具体解释请咨询您的主治医生。"

    def add_template(self, category: str, template_key: str, template: str):
        """添加或更新模板

        参数：
            category: 模板类别（differential/regression/survival）
            template_key: 模板键（ttest/chi2/linear等）
            template: 模板字符串
        """
        if category not in self.templates:
            self.templates[category] = {}
        self.templates[category][template_key] = template

    def get_template(self, category: str, template_key: str) -> Optional[str]:
        """获取模板"""
        return self.templates.get(category, {}).get(template_key)
