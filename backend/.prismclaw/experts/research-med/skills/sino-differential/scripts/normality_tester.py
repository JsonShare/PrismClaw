#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
正态性检验模块

提供Shapiro-Wilk检验和Kolmogorov-Smirnov检验
"""
from typing import Dict, List, Union
import numpy as np
from scipy import stats


class NormalityTester:
    """
    正态性检验器

    使用Shapiro-Wilk检验（n<50）或Kolmogorov-Smirnov检验（n≥50）
    """

    def __init__(self, alpha: float = 0.05):
        """
        初始化正态性检验器

        参数：
            alpha: 显著性水平，默认0.05
        """
        self.alpha = alpha

    def test_normality(self, data: Union[List, np.ndarray], n: int = None) -> Dict:
        """
        执行正态性检验

        参数：
            data: 数据数组
            n: 样本量，如果为None则从data自动计算

        返回：
            Dict: 包含检验结果的字典
                - is_normal: bool, 是否通过正态性检验
                - statistic: 检验统计量
                - p_value: p值
                - method: 使用的检验方法
                - interpretation: 解释
        """
        if n is None:
            n = len(data)

        data = np.array(data)

        # 去除缺失值
        data = data[~np.isnan(data)]
        n_valid = len(data)

        if n_valid < 3:
            return {
                'is_normal': False,
                'statistic': None,
                'p_value': None,
                'method': '样本量不足',
                'interpretation': '样本量太小，无法进行正态性检验',
                'n_valid': n_valid
            }

        # 根据样本量选择检验方法
        if n_valid < 50:
            return self._shapiro_wilk(data)
        else:
            return self._kolmogorov_smirnov(data)

    def _shapiro_wilk(self, data: np.ndarray) -> Dict:
        """
        Shapiro-Wilk检验（n<50）

        参数：
            data: 有效数据数组

        返回：
            Dict: 检验结果
        """
        statistic, p_value = stats.shapiro(data)

        is_normal = p_value > self.alpha

        interpretation = self._interpret_result(is_normal, p_value, 'Shapiro-Wilk')

        return {
            'is_normal': is_normal,
            'statistic': float(statistic),
            'p_value': float(p_value),
            'method': 'Shapiro-Wilk检验',
            'interpretation': interpretation
        }

    def _kolmogorov_smirnov(self, data: np.ndarray) -> Dict:
        """
        Kolmogorov-Smirnov检验（n≥50）

        参数：
            data: 有效数据数组

        返回：
            Dict: 检验结果
        """
        # 与正态分布比较
        mu = np.mean(data)
        sigma = np.std(data, ddof=1)
        statistic, p_value = stats.kstest(data, 'norm', args=(mu, sigma))

        is_normal = p_value > self.alpha

        interpretation = self._interpret_result(is_normal, p_value, 'Kolmogorov-Smirnov')

        return {
            'is_normal': is_normal,
            'statistic': float(statistic),
            'p_value': float(p_value),
            'method': 'Kolmogorov-Smirnov检验',
            'interpretation': interpretation
        }

    def _interpret_result(self, is_normal: bool, p_value: float, method: str) -> str:
        """
        解释检验结果

        参数：
            is_normal: 是否通过正态性检验
            p_value: p值
            method: 检验方法名

        返回：
            str: 解释文本
        """
        if is_normal:
            return f"数据满足正态性假设（{method}, p={p_value:.4f}>0.05）"
        else:
            return f"数据不满足正态性假设（{method}, p={p_value:.4f}<0.05），建议使用非参数方法"

    def test_multiple_groups(self, groups: List[np.ndarray]) -> Dict:
        """
        检验多组数据的正态性

        参数：
            groups: 多个数据组

        返回：
            Dict: 每组的检验结果和总体评估
        """
        results = {}
        all_normal = True

        for i, group_data in enumerate(groups):
            group_name = f"组{i+1}"
            result = self.test_normality(group_data)
            results[group_name] = result
            if not result['is_normal']:
                all_normal = False

        return {
            'group_results': results,
            'all_normal': all_normal,
            'summary': '所有组都满足正态性' if all_normal else '至少有一组不满足正态性'
        }