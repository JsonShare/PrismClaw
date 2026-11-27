#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计方法自动选择器

根据数据特征自动选择最合适的统计方法，实现显式决策树逻辑
"""
from typing import Tuple, Dict, List, Optional


class MethodSelector:
    """
    统计方法自动选择器

    核心决策树逻辑：
    Step 1: 识别变量类型
    ├── 连续变量 → Step 2a（均值比较）
    └── 分类变量 → Step 2b（率比较）

    Step 2a: 连续变量方法选择
    ├── 组数 = 2 → Step 3a
    └── 组数 > 2 → Step 3b

    Step 3a: 两组均值比较
    ├── 正态性检验通过 & 方差齐性 → 独立样本t检验
    ├── 正态性检验通过 & 方差不齐 → Welch's t检验
    ├── 正态性检验不通过 或 样本量<30 → Mann-Whitney U检验
    └── 配对设计 → 配对t检验 或 Wilcoxon符号秩检验

    Step 3b: 多组均值比较
    ├── 正态性 & 方差齐性 → 单因素ANOVA
    ├── 正态性通过 & 方差不齐 → Brown-Forsythe检验
    ├── 正态性不通过 或 样本量<30 → Kruskal-Wallis检验
    └── 事后比较：SNK法/Dunnett法

    Step 2b: 分类变量方法选择
    ├── 期望频数≥5 → 卡方检验
    ├── 期望频数<5（样本量≥40）→ 连续性校正卡方
    └── 样本量<40 或 期望频数<1 → Fisher精确检验
    """

    def __init__(self):
        """初始化方法选择器"""
        self.decision_log = []

    def select_method(
        self,
        var_type: str,
        n_groups: int,
        normality: bool,
        variance_homogeneous: bool,
        n: int,
        design: str = 'independent'
    ) -> Tuple[str, str]:
        """
        根据数据特征选择最合适的统计方法

        参数：
            var_type: 变量类型，'continuous' 或 'categorical'
            n_groups: 分组数量
            normality: 是否通过正态性检验
            variance_homogeneous: 方差是否齐性
            n: 最小分组样本量
            design: 设计类型，'independent', 'paired', 'repeated'

        返回：
            Tuple[str, str]: (方法名, 选择原因)
        """
        self.decision_log = []

        if var_type == 'continuous':
            return self._select_for_continuous(
                n_groups=n_groups,
                normality=normality,
                variance_homogeneous=variance_homogeneous,
                n=n,
                design=design
            )
        else:
            return self._select_for_categorical(
                n_groups=n_groups,
                n=n
            )

    def _select_for_continuous(
        self,
        n_groups: int,
        normality: bool,
        variance_homogeneous: bool,
        n: int,
        design: str
    ) -> Tuple[str, str]:
        """
        连续变量方法选择逻辑

        参数：
            n_groups: 分组数量
            normality: 是否通过正态性检验
            variance_homogeneous: 方差是否齐性
            n: 最小分组样本量
            design: 设计类型

        返回：
            Tuple[str, str]: (方法名, 选择原因)
        """
        # 决策节点1: 设计类型
        if design in ['paired', 'repeated']:
            if n < 30 and not normality:
                self.decision_log.append("配对/重复测量设计 + 小样本 + 非正态 → Wilcoxon符号秩检验")
                return 'wilcoxon', "由于样本量较小(n<30)且数据不满足正态性假设，选择非参数的Wilcoxon符号秩检验"
            else:
                self.decision_log.append("配对/重复测量设计 + 正态 → 配对t检验")
                return 'paired_ttest', "配对设计且数据满足正态性假设，选择配对t检验"

        # 决策节点2: 分组数量
        if n_groups == 2:
            return self._select_two_group_method(normality, variance_homogeneous, n)
        else:
            return self._select_multi_group_method(normality, variance_homogeneous, n)

    def _select_two_group_method(
        self,
        normality: bool,
        variance_homogeneous: bool,
        n: int
    ) -> Tuple[str, str]:
        """
        两组均值比较方法选择

        参数：
            normality: 是否通过正态性检验
            variance_homogeneous: 方差是否齐性
            n: 最小分组样本量

        返回：
            Tuple[str, str]: (方法名, 选择原因)
        """
        # 决策节点3: 小样本强制非参数
        if n < 30:
            if normality:
                # 小样本但正态，可以考虑t检验
                if variance_homogeneous:
                    self.decision_log.append("两组 + 小样本(n<30) + 正态 + 方差齐 → 独立样本t检验")
                    return 'independent_ttest', "样本量较小(n<30)，但数据满足正态性和方差齐性假设，选择独立样本t检验"
                else:
                    self.decision_log.append("两组 + 小样本(n<30) + 正态 + 方差不齐 → Welch校正t检验")
                    return 'welch_ttest', "样本量较小(n<30)且方差不齐，选择Welch校正t检验"
            else:
                self.decision_log.append("两组 + 小样本(n<30) + 非正态 → Mann-Whitney U检验")
                return 'mann_whitney_u', "样本量较小(n<30)且数据不满足正态性假设，选择非参数的Mann-Whitney U检验"

        # 大样本 (n >= 30)
        if normality and variance_homogeneous:
            self.decision_log.append("两组 + 大样本(n≥30) + 正态 + 方差齐 → 独立样本t检验")
            return 'independent_ttest', "数据满足正态性和方差齐性假设，选择独立样本t检验"
        elif normality and not variance_homogeneous:
            self.decision_log.append("两组 + 大样本(n≥30) + 正态 + 方差不齐 → Welch校正t检验")
            return 'welch_ttest', "数据满足正态性但不满足方差齐性，选择Welch校正t检验"
        else:
            # 大样本时根据中心极限定理，t检验仍然稳健
            # 但如果非正态严重（如严重偏态），仍建议非参数
            if not normality:
                self.decision_log.append("两组 + 大样本(n≥30) + 非正态 → Mann-Whitney U检验")
                return 'mann_whitney_u', "数据不满足正态性假设，选择非参数的Mann-Whitney U检验"

        # 默认返回t检验
        return 'independent_ttest', "使用独立样本t检验"

    def _select_multi_group_method(
        self,
        normality: bool,
        variance_homogeneous: bool,
        n: int
    ) -> Tuple[str, str]:
        """
        多组均值比较方法选择

        参数：
            normality: 是否通过正态性检验
            variance_homogeneous: 方差是否齐性
            n: 最小分组样本量

        返回：
            Tuple[str, str]: (方法名, 选择原因)
        """
        # 决策节点4: 小样本强制非参数
        if n < 30:
            if normality:
                if variance_homogeneous:
                    self.decision_log.append("多组 + 小样本(n<30) + 正态 + 方差齐 → 单因素ANOVA")
                    return 'oneway_anova', "样本量较小(n<30)但满足参数检验条件，选择单因素ANOVA"
                else:
                    self.decision_log.append("多组 + 小样本(n<30) + 正态 + 方差不齐 → Brown-Forsythe检验")
                    return 'brown_forsythe', "方差不齐且样本量较小，选择稳健的Brown-Forsythe检验"
            else:
                self.decision_log.append("多组 + 小样本(n<30) + 非正态 → Kruskal-Wallis检验")
                return 'kruskal_wallis', "样本量较小(n<30)且数据不满足正态性假设，选择非参数的Kruskal-Wallis检验"

        # 大样本 (n >= 30)
        if normality and variance_homogeneous:
            self.decision_log.append("多组 + 大样本(n≥30) + 正态 + 方差齐 → 单因素ANOVA")
            return 'oneway_anova', "数据满足正态性和方差齐性假设，选择单因素ANOVA"
        elif normality and not variance_homogeneous:
            self.decision_log.append("多组 + 大样本(n≥30) + 正态 + 方差不齐 → Brown-Forsythe检验")
            return 'brown_forsythe', "数据满足正态性但不满足方差齐性，选择Brown-Forsythe检验"
        else:
            self.decision_log.append("多组 + 大样本(n≥30) + 非正态 → Kruskal-Wallis检验")
            return 'kruskal_wallis', "数据不满足正态性假设，选择非参数的Kruskal-Wallis检验"

    def _select_for_categorical(
        self,
        n_groups: int,
        n: int
    ) -> Tuple[str, str]:
        """
        分类变量（率比较）方法选择

        参数：
            n_groups: 分组数量
            n: 总样本量

        返回：
            Tuple[str, str]: (方法名, 选择原因)
        """
        # 决策节点5: 样本量/组数决定方法
        # Fisher 精确检验仅支持 2×2 表；≥3 组（R×C）一律走卡方检验
        if n_groups > 2:
            self.decision_log.append("分类 + 多组(n_groups>2) → 卡方检验(R×C)")
            return 'chi_square', "组数>2（R×C列联表），Fisher精确检验不适用，选择卡方检验"

        if n < 40:
            self.decision_log.append("分类 + 小样本(n<40) → Fisher精确检验")
            return 'fisher_exact', "样本量较小(n<40)，选择Fisher精确检验"

        # n >= 40
        # 注意：这里简化了期望频数的检查，实际应用中需要根据列联表计算
        self.decision_log.append("分类 + 大样本(n≥40) → 卡方检验")
        return 'chi_square', "样本量足够(n≥40)，选择卡方检验"

    def build_decision_tree(self) -> Dict:
        """
        构建并返回决策树结构（用于可视化和调试）

        返回：
            Dict: 决策树结构
        """
        return {
            'step_1_var_type': {
                'continuous': {
                    'step_2_n_groups': {
                        2: {
                            'step_3_conditions': {
                                'normality_true_variance_true': 'independent_ttest',
                                'normality_true_variance_false': 'welch_ttest',
                                'normality_false_or_small_n': 'mann_whitney_u'
                            }
                        },
                        '>2': {
                            'step_3_conditions': {
                                'normality_true_variance_true': 'oneway_anova',
                                'normality_true_variance_false': 'brown_forsythe',
                                'normality_false_or_small_n': 'kruskal_wallis'
                            }
                        }
                    }
                },
                'categorical': {
                    'step_2_n': {
                        '<40': 'fisher_exact',
                        '>=40': 'chi_square'
                    }
                }
            }
        }

    def explain_decision(self) -> List[str]:
        """
        获取决策解释

        返回：
            List[str]: 决策过程说明
        """
        return self.decision_log.copy()