"""effect_size_interpreter.py - 效应量解释器

提供各类效应量的通俗解释：
- Cohen's d: 0.2（小）/ 0.5（中）/ 0.8（大）
- OR/RR: 需要结合临床背景解释
- R²/η²: 解释为"方差解释比例"
- 标准化β: 用于回归中比较各因素影响力大小
"""


class EffectSizeInterpreter:
    """效应量解释器"""

    # Cohen's d 解释标准
    COHENS_D_SMALL = 0.2
    COHENS_D_MEDIUM = 0.5
    COHENS_D_LARGE = 0.8

    # η² 解释标准
    ETA_SQUARED_SMALL = 0.01
    ETA_SQUARED_MEDIUM = 0.06
    ETA_SQUARED_LARGE = 0.14

    # Cramér's V 解释标准
    CRAMERS_V_SMALL = 0.1
    CRAMERS_V_MEDIUM = 0.3
    CRAMERS_V_LARGE = 0.5

    # 标准化β 解释标准
    STD_BETA_SMALL = 0.1
    STD_BETA_MEDIUM = 0.3
    STD_BETA_LARGE = 0.5

    def interpret_cohens_d(self, d: float) -> str:
        """解释Cohen's d效应量

        Cohen's d是最常用的均值差异效应量指标。
        - |d| < 0.2: 微小效应，实际意义很小
        - 0.2 ≤ |d| < 0.5: 小效应
        - 0.5 ≤ |d| < 0.8: 中等偏大效应
        - |d| ≥ 0.8: 大效应

        临床意义补充：
        - d=0.2时，约57%的患者在实验组表现优于对照组
        - d=0.5时，约67%的患者在实验组表现优于对照组
        - d=0.8时，约71%的患者在实验组表现优于对照组
        """
        abs_d = abs(d)

        if abs_d < 0.2:
            # 计算重叠率（overlap）
            overlap = (1 - 0.47 * abs_d) * 100
            return f"微小效应（d={d:.2f}），两组数据重叠程度很高（约{overlap:.0f}%），实际意义有限"
        elif abs_d < 0.5:
            # 计算优于比例
            pct_better = (0.5 + 0.47 * abs_d) * 100
            return f"小效应（d={d:.2f}），约{pct_better:.0f}%的患者实验组优于对照组"
        elif abs_d < 0.8:
            # 计算优于比例
            pct_better = (0.5 + 0.47 * abs_d) * 100
            return f"中等偏大效应（d={d:.2f}），约{pct_better:.0f}%的患者实验组优于对照组"
        else:
            # 计算优于比例
            pct_better = (0.5 + 0.47 * abs_d) * 100
            return f"大效应（d={d:.2f}），约{min(pct_better, 79):.0f}%的患者实验组优于对照组"

    def interpret_eta_squared(self, eta2: float) -> str:
        """解释η²（eta-squared）效应量

        η²表示组间差异解释了总变异的比例。
        - η² < 0.01: 微小效应
        - 0.01 ≤ η² < 0.06: 小效应
        - 0.06 ≤ η² < 0.14: 中等效应
        - η² ≥ 0.14: 大效应

        示例：
        - η² = 0.12 表示组别差异解释了12%的总变异
        - 这意味着还有88%的变异由其他因素解释
        """
        if eta2 < 0.01:
            return f"微小效应（η²={eta2:.3f}），组间差异仅解释{eta2*100:.1f}%的变异"
        elif eta2 < 0.06:
            return f"小效应（η²={eta2:.3f}），组间差异解释{eta2*100:.1f}%的变异"
        elif eta2 < 0.14:
            return f"中等效应（η²={eta2:.3f}），组间差异解释{eta2*100:.1f}%的变异，剩余{(1-eta2)*100:.1f}%由其他因素解释"
        else:
            return f"大效应（η²={eta2:.3f}），组间差异解释{eta2*100:.1f}%的变异"

    def interpret_cramers_v(self, v: float) -> str:
        """解释Cramér's V效应量

        Cramér's V是分类变量关联强度的指标。
        - V < 0.1: 微小关联
        - 0.1 ≤ V < 0.3: 弱关联
        - 0.3 ≤ V < 0.5: 中等关联
        - V ≥ 0.5: 强关联
        """
        abs_v = abs(v)

        if abs_v < 0.1:
            return f"微小关联（V={v:.2f}），两组率差异的实际意义很小"
        elif abs_v < 0.3:
            return f"弱关联（V={v:.2f}），两组率存在一定差异"
        elif abs_v < 0.5:
            return f"中等关联（V={v:.2f}），两组率差异明显"
        else:
            return f"强关联（V={v:.2f}），两组率差异非常明显"

    def interpret_r_squared(self, r2: float, n_predictors: int = 1) -> str:
        """解释R²决定系数

        R²表示模型解释的变异比例。
        - R² < 0.1: 模型解释力很弱
        - 0.1 ≤ R² < 0.3: 模型解释力可接受
        - 0.3 ≤ R² < 0.5: 模型解释力较好
        - R² ≥ 0.5: 模型解释力很强

        调整R²考虑了预测变量数量的影响，更适合多变量模型。
        """
        if r2 < 0.1:
            return f"模型解释力很弱（R²={r2:.3f}），这些因素仅解释{r2*100:.1f}%的变异"
        elif r2 < 0.3:
            return f"模型解释力可接受（R²={r2:.3f}），这些因素共同解释{r2*100:.1f}%的变异"
        elif r2 < 0.5:
            return f"模型解释力较好（R²={r2:.3f}），这些因素共同解释{r2*100:.1f}%的变异"
        else:
            return f"模型解释力很强（R²={r2:.3f}），这些因素共同解释{r2*100:.1f}%的变异"

    def interpret_std_beta(self, std_beta: float) -> str:
        """解释标准化回归系数（β'）

        标准化系数用于比较不同预测变量的相对影响力。
        - |β'| < 0.1: 影响力很小
        - 0.1 ≤ |β'| < 0.3: 影响力较小
        - 0.3 ≤ |β'| < 0.5: 影响力中等
        - |β'| ≥ 0.5: 影响力较大

        注意：标准化系数的比较仅在相同量纲下才有意义。
        """
        abs_beta = abs(std_beta)

        if abs_beta < 0.1:
            return f"影响力很小（β'={std_beta:.2f}），该因素对结局的独立贡献有限"
        elif abs_beta < 0.3:
            return f"影响力较小（β'={std_beta:.2f}），该因素对结局有一定的独立贡献"
        elif abs_beta < 0.5:
            return f"影响力中等（β'={std_beta:.2f}），该因素对结局有明显的独立贡献"
        else:
            return f"影响力较大（β'={std_beta:.2f}），该因素是结局的重要预测因子"

    def interpret_or(self, or_value: float, reference_risk: float = None) -> tuple:
        """解释Odds Ratio（优势比）

        OR > 1: 暴露增加结局风险
        OR < 1: 暴露降低结局风险
        OR = 1: 无关联

        如果提供baseline risk，可以计算绝对风险差异。
        """
        if or_value > 1:
            risk_increase = (or_value - 1) * 100
            interpretation = f"危险因素，OR={or_value:.2f}表示风险增加{risk_increase:.1f}%"
            category = "risk_factor"
        elif or_value < 1:
            risk_decrease = (1 - or_value) * 100
            interpretation = f"保护因素，OR={or_value:.2f}表示风险降低{risk_decrease:.1f}%"
            category = "protective_factor"
        else:
            interpretation = f"无关联，OR={or_value:.2f}"
            category = "no_association"

        return interpretation, category

    def interpret_rr(self, rr_value: float) -> tuple:
        """解释Relative Risk（相对风险度）

        RR > 1: 暴露增加结局风险
        RR < 1: 暴露降低结局风险
        RR = 1: 无关联

        与OR相比，RR更易理解（直接是风险的倍数），但通常需要队列研究数据。
        """
        if rr_value > 1:
            risk_increase = (rr_value - 1) * 100
            interpretation = f"相对风险增加，RR={rr_value:.2f}表示风险增加{risk_increase:.1f}%"
            category = "risk_factor"
        elif rr_value < 1:
            risk_decrease = (1 - rr_value) * 100
            interpretation = f"相对风险降低，RR={rr_value:.2f}表示风险降低{risk_decrease:.1f}%"
            category = "protective_factor"
        else:
            interpretation = f"无相对风险，RR={rr_value:.2f}"
            category = "no_association"

        return interpretation, category

    def interpret_hr(self, hr_value: float) -> tuple:
        """解释Hazard Ratio（风险比）

        HR > 1: 暴露增加风险（事件更快发生）
        HR < 1: 暴露降低风险（事件更慢发生）
        HR = 1: 无效应

        常用于生存分析中。
        """
        if hr_value > 1:
            risk_increase = (hr_value - 1) * 100
            interpretation = f"风险增加，HR={hr_value:.2f}表示风险增加{risk_increase:.1f}%"
            category = "risk_factor"
        elif hr_value < 1:
            risk_reduction = (1 - hr_value) * 100
            interpretation = f"风险降低，HR={hr_value:.2f}表示风险降低{risk_reduction:.1f}%"
            category = "protective_factor"
        else:
            interpretation = f"无效应，HR={hr_value:.2f}"
            category = "no_association"

        return interpretation, category

    def interpret_correlation(self, r: float) -> str:
        """解释相关系数r

        - |r| < 0.1: 无相关
        - 0.1 ≤ |r| < 0.3: 弱相关
        - 0.3 ≤ |r| < 0.5: 中等相关
        - 0.5 ≤ |r| < 0.7: 强相关
        - |r| ≥ 0.7: 很强相关
        """
        abs_r = abs(r)

        if abs_r < 0.1:
            return f"无相关（r={r:.2f}）"
        elif abs_r < 0.3:
            return f"弱相关（r={r:.2f}），两变量间存在轻微的线性关系"
        elif abs_r < 0.5:
            return f"中等相关（r={r:.2f}），两变量间存在一定的线性关系"
        elif abs_r < 0.7:
            return f"强相关（r={r:.2f}），两变量间存在明显的线性关系"
        else:
            return f"很强相关（r={r:.2f}），两变量间存在强烈的线性关系"

    def get_effect_size_category(self, effect_size: float, effect_type: str) -> str:
        """获取效应量的分类（small/medium/large）"""
        if effect_type == "cohens_d":
            abs_d = abs(effect_size)
            if abs_d < 0.2:
                return "微小"
            elif abs_d < 0.5:
                return "小"
            elif abs_d < 0.8:
                return "中等偏大"
            else:
                return "大"
        elif effect_type == "eta_squared":
            if effect_size < 0.01:
                return "微小"
            elif effect_size < 0.06:
                return "小"
            elif effect_size < 0.14:
                return "中等"
            else:
                return "大"
        elif effect_type == "cramers_v":
            abs_v = abs(effect_size)
            if abs_v < 0.1:
                return "微小"
            elif abs_v < 0.3:
                return "弱"
            elif abs_v < 0.5:
                return "中等"
            else:
                return "强"
        else:
            return "未知"
