#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卡方检验样本量计算模块

基于效应量w、df、α=0.05、power=0.8计算所需样本量
"""
from typing import Dict
import numpy as np
from scipy import stats


class Chi2PowerCalculator:
    """
    卡方检验样本量计算器

    用于两样本率比较的样本量计算
    基于非中心卡方分布的非中心参数λ
    """

    def __init__(
        self,
        alpha: float = 0.05,
        power: float = 0.8,
        effect_size: float = 0.3,
        design_type: str = 'superiority'
    ):
        """
        初始化卡方检验样本量计算器

        参数：
            alpha: 显著性水平（双侧）
            power: 统计效能（1-β）
            effect_size: Cohen's w 效应量（用于卡方检验）
            design_type: 设计类型
        """
        self.alpha = alpha
        self.power = power
        self.effect_size = effect_size
        self.design_type = design_type

        # 计算Z值
        self.z_alpha = stats.norm.ppf(1 - alpha / 2)
        self.z_beta = stats.norm.ppf(power)

    def calculate(
        self,
        n1_ratio: int = 1,
        n2_ratio: int = 1
    ) -> Dict:
        """
        计算卡方检验所需的样本量

        参数：
            n1_ratio: 第一组比例
            n2_ratio: 第二组比例

        返回：
            Dict: 包含样本量计算结果的字典
        """
        w = self.effect_size

        # Cohen's w 与比例差的关系：
        # w = sqrt(Σ(p0 - p1)^2 / p0)  简化为两组的率比较
        # 对于两组的率比较，可以用以下近似：
        # w ≈ |p1 - p2| / sqrt(p_mean * (1 - p_mean))

        # 使用基于正态近似的公式
        # 样本量公式（两组率比较）：
        # n = (z_α * sqrt(2*p_bar*(1-p_bar)) + z_β * sqrt(p1*(1-p1) + p2*(1-p2)))² / (p1 - p2)²

        # 但这里我们使用Cohen's w的直接计算方式
        # 对于2x2表，w = sqrt(χ²/n)，所以 χ² = n * w²
        # 非中心参数 λ = n * w²

        # 简化为两样本率比较的公式
        # 使用基于效应量的统一公式

        # 效应量w对应的率差
        # w = 0.1 对应 small (比例差约10%)
        # w = 0.3 对应 medium (比例差约30%)
        # w = 0.5 对应 large (比例差约50%)

        # 使用近似公式：n ≈ 2 * (z_α + z_β)² / w²
        z_eff = self.z_alpha + self.z_beta
        n_per_group = 2 * (z_eff ** 2) / (w ** 2)

        # 向上取整
        n_per_group = int(np.ceil(n_per_group))

        # 根据分组比例调整
        if n1_ratio != n2_ratio:
            k = n1_ratio / n2_ratio if n2_ratio > 0 else 1
            n2 = (1 + 1/k) * (z_eff ** 2) / (w ** 2)
            n1 = k * n2
            n_per_group_2 = int(np.ceil(n2))
            n_per_group = int(np.ceil(n1))
            total_n = n_per_group + n_per_group_2
        else:
            n_per_group_2 = n_per_group
            total_n = 2 * n_per_group

        # 计算实际效能
        actual_power = self._calculate_actual_power(n_per_group, n_per_group_2, w)

        # 效应量解释
        effect_interpretation = self._interpret_effect_size(w)

        return {
            'test_method': 'chi_square',
            'design_type': self.design_type,
            'alpha': self.alpha,
            'power': self.power,
            'effect_size': w,
            'effect_interpretation': effect_interpretation,
            'n_per_group': n_per_group,
            'n_per_group_2': n_per_group_2,
            'total_n': total_n,
            'n1_ratio': n1_ratio,
            'n2_ratio': n2_ratio,
            'actual_power': actual_power,
            'z_alpha': float(self.z_alpha),
            'z_beta': float(self.z_beta),
            'formula': 'n = 2 * (z_α/2 + z_β)² / w²',
            'note': 'Cohen\'s w 效应量：small=0.1, medium=0.3, large=0.5'
        }

    def _calculate_actual_power(
        self,
        n1: int,
        n2: int,
        w: float
    ) -> float:
        """
        计算给定样本量下的实际统计效能

        参数：
            n1: 第一组样本量
            n2: 第二组样本量
            w: Cohen's w 效应量

        返回：
            float: 实际统计效能
        """
        # 非中心参数
        n_total = n1 + n2
        lambda_ncp = n_total * w**2

        # 临界值（自由度为1的卡方分布）
        chi2_crit = stats.chi2.ppf(1 - self.alpha, df=1)

        # 计算功效：P(χ² > χ²_crit | λ)
        # 使用非中心卡方分布的CDF
        power = 1 - stats.ncx2.cdf(chi2_crit, df=1, nc=lambda_ncp)

        return float(min(1.0, max(0.0, power)))

    def _interpret_effect_size(self, w: float) -> str:
        """
        解释Cohen's w效应量

        参数：
            w: Cohen's w 值

        返回：
            str: 效应量解释
        """
        abs_w = abs(w)
        if abs_w < 0.1:
            magnitude = "极小"
        elif abs_w < 0.3:
            magnitude = "小"
        elif abs_w < 0.5:
            magnitude = "中等"
        else:
            magnitude = "大"

        return f"Cohen's w = {w:.2f}，效应量{magnitude}"

    def from_proportions(
        self,
        p1: float,
        p2: float,
        n1_ratio: int = 1,
        n2_ratio: int = 1
    ) -> Dict:
        """
        根据两组率计算样本量

        参数：
            p1: 第一组事件率
            p2: 第二组事件率
            n1_ratio: 第一组比例
            n2_ratio: 第二组比例

        返回：
            Dict: 包含样本量计算结果的字典
        """
        # 计算比例差
        delta = abs(p1 - p2)

        # 计算平均率
        p_bar = (p1 + p2) / 2

        # 计算Cohen's w
        if p_bar > 0 and p_bar < 1:
            w = delta / np.sqrt(p_bar * (1 - p_bar))
        else:
            w = 0

        self.effect_size = w

        result = self.calculate(n1_ratio=n1_ratio, n2_ratio=n2_ratio)

        result['p1'] = p1
        result['p2'] = p2
        result['proportion_diff'] = delta
        result['note'] = f"两组率：{p1:.1%} vs {p2:.1%}，率差{delta:.1%}"

        return result