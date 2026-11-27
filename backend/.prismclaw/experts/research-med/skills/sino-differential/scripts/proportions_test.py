#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
率比较检验模块

提供卡方检验、Fisher精确检验、McNemar检验等
"""
from typing import Dict, Union
import numpy as np
import pandas as pd
from scipy import stats


class ProportionsTests:
    """
    率比较检验类

    提供卡方检验、Fisher精确检验、McNemar检验等
    """

    def __init__(self, alpha: float = 0.05, confidence_level: float = 0.95):
        """
        初始化率比较检验器

        参数：
            alpha: 显著性水平
            confidence_level: 置信水平
        """
        self.alpha = alpha
        self.confidence_level = confidence_level

    def chi_square_test(
        self,
        contingency_table: Union[pd.DataFrame, np.ndarray],
        correction: bool = True
    ) -> Dict:
        """
        卡方检验（两样本率比较）

        参数：
            contingency_table: 列联表（2x2格式：行为组，列为事件）
            correction: 是否使用连续性校正（Yates校正）

        返回：
            Dict: 检验结果
        """
        if isinstance(contingency_table, pd.DataFrame):
            observed = contingency_table.values
        else:
            observed = np.array(contingency_table)

        # 支持 R×C 列联表（≥3 组率比较）：用 scipy.stats.chi2_contingency
        if observed.shape != (2, 2):
            if observed.ndim != 2 or observed.shape[0] < 2 or observed.shape[1] < 2:
                return {
                    'statistic': None,
                    'df': None,
                    'p_value': None,
                    'error': f'卡方检验需要至少2x2列联表，但收到的是{observed.shape}表'
                }
            n_total = observed.sum()
            chi2, p_value, dof, expected = stats.chi2_contingency(
                observed, correction=correction
            )
            return {
                'statistic': float(chi2),
                'df': int(dof),
                'p_value': float(p_value),
                'observed': observed.tolist(),
                'expected': expected.tolist(),
                'method': f'卡方检验（R×C表 {observed.shape[0]}×{observed.shape[1]}）'
                          + ('（含Yates校正）' if correction else ''),
                'interpretation': self._interpret_result(p_value, None, '卡方检验')
            }

        n_total = observed.sum()
        row_sums = observed.sum(axis=1)
        col_sums = observed.sum(axis=0)

        # 计算期望频数
        expected = np.outer(row_sums, col_sums) / n_total

        # 检查期望频数
        min_expected = expected.min()
        if min_expected < 1:
            return {
                'statistic': None,
                'df': None,
                'p_value': None,
                'warning': '存在期望频数<1，建议使用Fisher精确检验',
                'expected_frequencies': expected.tolist()
            }

        # 计算卡方统计量
        if correction and n_total >= 40:
            # Yates连续性校正
            chi2 = np.sum((np.abs(observed - expected) - 0.5)**2 / expected)
        else:
            chi2 = np.sum((observed - expected)**2 / expected)

        df = 1  # 2x2表的自由度为1
        p_value = 1 - stats.chi2.cdf(chi2, df)

        # 计算比例
        p1 = observed[0, 1] / row_sums[0] if row_sums[0] > 0 else 0  # 组1事件率
        p2 = observed[1, 1] / row_sums[1] if row_sums[1] > 0 else 0  # 组2事件率
        prop_diff = p1 - p2

        # 置信区间（比例差）
        se_prop = np.sqrt(p1 * (1 - p1) / row_sums[0] + p2 * (1 - p2) / row_sums[1])
        z_crit = stats.norm.ppf((1 + self.confidence_level) / 2)
        ci_lower = prop_diff - z_crit * se_prop
        ci_upper = prop_diff + z_crit * se_prop

        # phi相关系数（与卡方的关系）
        phi = np.sqrt(chi2 / n_total) if n_total > 0 else 0

        return {
            'statistic': float(chi2),
            'df': df,
            'p_value': float(p_value),
            'observed': observed.tolist(),
            'expected': expected.tolist(),
            'prop1': float(p1),
            'prop2': float(p2),
            'prop_diff': float(prop_diff),
            'prop_ci': (float(ci_lower), float(ci_upper)),
            'phi': float(phi),
            'method': '卡方检验' + ('（含Yates校正）' if correction else ''),
            'interpretation': self._interpret_result(p_value, prop_diff, '卡方检验')
        }

    def fisher_exact_test(
        self,
        contingency_table: Union[pd.DataFrame, np.ndarray]
    ) -> Dict:
        """
        Fisher精确检验（样本量<40或期望频数<5时使用）

        参数：
            contingency_table: 列联表（2x2格式）

        返回：
            Dict: 检验结果
        """
        if isinstance(contingency_table, pd.DataFrame):
            observed = contingency_table.values
        else:
            observed = np.array(contingency_table)

        # 确保是2x2表
        if observed.shape != (2, 2):
            return {
                'statistic': None,
                'p_value': None,
                'error': f'Fisher精确检验需要2x2列联表，但收到的是{observed.shape}表'
            }

        # scipy的fisher_exact
        odds_ratio, p_value = stats.fisher_exact(observed)

        # 计算比例
        row_sums = observed.sum(axis=1)
        p1 = observed[0, 1] / row_sums[0] if row_sums[0] > 0 else 0
        p2 = observed[1, 1] / row_sums[1] if row_sums[1] > 0 else 0
        prop_diff = p1 - p2

        return {
            'statistic': float(odds_ratio),
            'odds_ratio': float(odds_ratio),
            'p_value': float(p_value),
            'observed': observed.tolist(),
            'prop1': float(p1),
            'prop2': float(p2),
            'prop_diff': float(prop_diff),
            'method': 'Fisher精确检验',
            'interpretation': self._interpret_result(p_value, prop_diff, 'Fisher精确检验')
        }

    def mcnemar_test(
        self,
        contingency_table: Union[pd.DataFrame, np.ndarray],
        correction: bool = True
    ) -> Dict:
        """
        McNemar检验（配对样本率比较）

        参数：
            contingency_table: 配对列联表
                格式：
                      对照-阴性  对照-阳性
                处理-阴性     a           b
                处理-阳性     c           d
            correction: 是否使用连续性校正

        返回：
            Dict: 检验结果
        """
        if isinstance(contingency_table, pd.DataFrame):
            table = contingency_table.values
        else:
            table = np.array(contingency_table)

        if table.shape != (2, 2):
            return {
                'statistic': None,
                'p_value': None,
                'error': f'McNemar检验需要2x2配对列联表，但收到的是{table.shape}表'
            }

        a, b, c, d = table.flatten()

        # McNemar统计量
        if correction and (b + c) > 0:
            # 带连续性校正
            chi2 = (abs(b - c) - 1)**2 / (b + c)
        else:
            # 不带校正
            if b + c == 0:
                chi2 = 0
            else:
                chi2 = (b - c)**2 / (b + c)

        df = 1
        p_value = 1 - stats.chi2.cdf(chi2, df)

        # 比例差
        total = a + b + c + d
        prop_diff = (b - c) / (b + c) if (b + c) > 0 else 0

        return {
            'statistic': float(chi2),
            'df': df,
            'p_value': float(p_value),
            'table': table.tolist(),
            'discordant_b': int(b),
            'discordant_c': int(c),
            'prop_diff': float(prop_diff),
            'method': 'McNemar检验' + ('（含连续性校正）' if correction else ''),
            'interpretation': self._interpret_result(p_value, prop_diff, 'McNemar检验')
        }

    def _interpret_result(self, p_value: float, prop_diff: float, method: str) -> str:
        """
        解释检验结果

        参数：
            p_value: p值
            prop_diff: 比例差
            method: 检验方法名

        返回：
            str: 解释文本
        """
        # R×C 表无单一比例差，给出不依赖方向/百分比的通用解释
        if prop_diff is None:
            if p_value < 0.001:
                return f"各组率差异有高度统计学意义（{method}, p<0.001）"
            elif p_value < self.alpha:
                return f"各组率差异有统计学意义（{method}, p={p_value:.4f}<{self.alpha}）"
            else:
                return f"各组率差异无统计学意义（{method}, p={p_value:.4f}>={self.alpha}）"

        direction = "较高" if prop_diff > 0 else "较低"

        if p_value < 0.001:
            return f"率差异有高度统计学意义（{method}, p<0.001），处理组事件率{direction}约{abs(prop_diff)*100:.1f}个百分点"
        elif p_value < self.alpha:
            return f"率差异有统计学意义（{method}, p={p_value:.4f}<{self.alpha}），处理组事件率{direction}约{abs(prop_diff)*100:.1f}个百分点"
        else:
            return f"率差异无统计学意义（{method}, p={p_value:.4f}>={self.alpha}）"