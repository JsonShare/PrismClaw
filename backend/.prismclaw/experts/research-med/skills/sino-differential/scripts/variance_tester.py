#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方差齐性检验模块

提供Levene检验和Brown-Forsythe检验
"""
from typing import Dict, List, Tuple, Union
import numpy as np
from scipy import stats


class VarianceTester:
    """
    方差齐性检验器

    使用Levene检验（默认）和Brown-Forsythe检验（更稳健）
    """

    def __init__(self, alpha: float = 0.05):
        """
        初始化方差齐性检验器

        参数：
            alpha: 显著性水平，默认0.05
        """
        self.alpha = alpha

    def test_variance_homogeneity(self, *groups: Union[List, np.ndarray]) -> Dict:
        """
        执行方差齐性检验（Levene检验）

        参数：
            *groups: 多个数据组

        返回：
            Dict: 包含检验结果的字典
                - is_homogeneous: bool, 方差是否齐性
                - statistic: 检验统计量
                - p_value: p值
                - method: 使用的检验方法
                - interpretation: 解释
        """
        if len(groups) < 2:
            return {
                'is_homogeneous': True,
                'statistic': None,
                'p_value': None,
                'method': '单组，无需方差齐性检验',
                'interpretation': '只有一组数据，无需进行方差齐性检验'
            }

        # 去除缺失值并转换为numpy数组
        valid_groups = []
        for group in groups:
            group = np.array(group)
            group = group[~np.isnan(group)]
            if len(group) > 0:
                valid_groups.append(group)

        if len(valid_groups) < 2:
            return {
                'is_homogeneous': False,
                'statistic': None,
                'p_value': None,
                'method': '数据不足',
                'interpretation': '有效数据不足，无法进行方差齐性检验'
            }

        # Levene检验（默认使用中位数，更稳健）
        statistic, p_value = stats.levene(*valid_groups)

        is_homogeneous = p_value > self.alpha

        interpretation = self._interpret_result(is_homogeneous, p_value)

        return {
            'is_homogeneous': is_homogeneous,
            'statistic': float(statistic),
            'p_value': float(p_value),
            'df': (len(valid_groups) - 1, sum(len(g) for g in valid_groups) - len(valid_groups)),
            'method': 'Levene检验',
            'interpretation': interpretation,
            'group_sizes': [len(g) for g in valid_groups],
            'group_variances': [float(np.var(g, ddof=1)) for g in valid_groups]
        }

    def brown_forsythe(self, *groups: Union[List, np.ndarray]) -> Dict:
        """
        Brown-Forsythe检验（更稳健的方差齐性检验）

        参数：
            *groups: 多个数据组

        返回：
            Dict: 包含检验结果的字典
        """
        if len(groups) < 2:
            return {
                'is_homogeneous': True,
                'statistic': None,
                'p_value': None,
                'method': '单组，无需方差齐性检验',
                'interpretation': '只有一组数据，无需进行方差齐性检验'
            }

        # 去除缺失值
        valid_groups = []
        for group in groups:
            group = np.array(group)
            group = group[~np.isnan(group)]
            if len(group) > 0:
                valid_groups.append(group)

        if len(valid_groups) < 2:
            return {
                'is_homogeneous': False,
                'statistic': None,
                'p_value': None,
                'method': '数据不足',
                'interpretation': '有效数据不足，无法进行方差齐性检验'
            }

        # Brown-Forsythe检验使用每个组与自身中位数的偏差的绝对值
        # scipy.levene 的 center='median' 就是 Brown-Forsythe 检验
        statistic, p_value = stats.levene(*valid_groups, center='median')

        is_homogeneous = p_value > self.alpha

        interpretation = self._interpret_result(is_homogeneous, p_value, method='Brown-Forsythe')

        return {
            'is_homogeneous': is_homogeneous,
            'statistic': float(statistic),
            'p_value': float(p_value),
            'df': (len(valid_groups) - 1, sum(len(g) for g in valid_groups) - len(valid_groups)),
            'method': 'Brown-Forsythe检验',
            'interpretation': interpretation,
            'group_sizes': [len(g) for g in valid_groups],
            'group_medians': [float(np.median(g)) for g in valid_groups]
        }

    def _interpret_result(
        self,
        is_homogeneous: bool,
        p_value: float,
        method: str = 'Levene检验'
    ) -> str:
        """
        解释检验结果

        参数：
            is_homogeneous: 方差是否齐性
            p_value: p值
            method: 检验方法名

        返回：
            str: 解释文本
        """
        if is_homogeneous:
            return f"方差齐性假设成立（{method}, p={p_value:.4f}>0.05）"
        else:
            return f"方差不齐（{method}, p={p_value:.4f}<0.05），建议使用Welch校正或Brown-Forsythe检验"

    def variance_ratio(self, *groups: Union[List, np.ndarray]) -> Tuple[float, str]:
        """
        计算最大与最小方差之比

        参数：
            *groups: 多个数据组

        返回：
            Tuple[float, str]: (方差比, 解释)
        """
        valid_groups = []
        for group in groups:
            group = np.array(group)
            group = group[~np.isnan(group)]
            if len(group) > 0:
                valid_groups.append(group)

        if len(valid_groups) < 2:
            return 1.0, '无法计算方差比（数据不足）'

        variances = [np.var(g, ddof=1) for g in valid_groups]
        max_var = max(variances)
        min_var = min(variances)

        if min_var == 0:
            ratio = float('inf')
            interpretation = f"存在零方差组，方差比无意义"
        else:
            ratio = max_var / min_var
            interpretation = f"最大方差/最小方差 = {ratio:.2f}"

        return float(ratio), interpretation