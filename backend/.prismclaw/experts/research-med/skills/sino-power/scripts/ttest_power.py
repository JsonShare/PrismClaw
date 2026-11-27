#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两样本t检验样本量计算模块

基于效应量d、α=0.05、power=0.8计算所需样本量
"""
from typing import Dict, Tuple
import numpy as np
from scipy import stats


class TTestPowerCalculator:
    """
    两样本t检验样本量计算器

    核心公式：n ≈ 2*(Z_α/2 + Z_β)²*σ²/δ²
    其中 δ 为效应量（Cohen's d * σ）
    """

    def __init__(
        self,
        alpha: float = 0.05,
        power: float = 0.8,
        effect_size: float = 0.5,
        design_type: str = 'superiority'
    ):
        """
        初始化t检验样本量计算器

        参数：
            alpha: 显著性水平（双侧）
            power: 统计效能（1-β）
            effect_size: Cohen's d 效应量
            design_type: 设计类型（superiority/equivalence/non_inferiority）
        """
        self.alpha = alpha
        self.power = power
        self.effect_size = effect_size
        self.design_type = design_type

        # 计算Z值
        self.z_alpha = stats.norm.ppf(1 - alpha / 2)  # 双侧
        self.z_beta = stats.norm.ppf(power)

    def calculate(
        self,
        n1_ratio: int = 1,
        n2_ratio: int = 1
    ) -> Dict:
        """
        计算两样本t检验所需的样本量

        参数：
            n1_ratio: 第一组比例
            n2_ratio: 第二组比例

        返回：
            Dict: 包含样本量计算结果的字典
        """
        # 效应量d对应的标准化差异
        d = self.effect_size

        # 根据设计类型调整
        if self.design_type == 'superiority':
            # 优效性检验
            z_eff = self.z_alpha + self.z_beta
        elif self.design_type == 'equivalence':
            # 等效性检验（使用双单侧检验TOST）
            z_eff = stats.norm.ppf(1 - alpha / 2) + stats.norm.ppf(power)
        elif self.design_type == 'non_inferiority':
            # 非劣效性检验
            z_eff = self.z_alpha + self.z_beta
        else:
            z_eff = self.z_alpha + self.z_beta

        # 样本量公式（两组相等时）
        # n = 2 * (z_alpha + z_beta)^2 / d^2
        # 这来自公式：d = 2*(z_alpha + z_beta) / sqrt(n)
        n_per_group = 2 * (z_eff ** 2) / (d ** 2)

        # 向上取整
        n_per_group = int(np.ceil(n_per_group))

        # 根据分组比例调整
        if n1_ratio != n2_ratio:
            # 非1:1比例时，使用有效样本大小概念
            # n1 = N * n1_ratio / total_ratio
            # n2 = N * n2_ratio / total_ratio
            # 其中 N = n1 + n2
            # 对于非均衡设计，n1 = k * n2
            k = n1_ratio / n2_ratio if n2_ratio > 0 else 1

            # 修正公式：n2 = (1 + 1/k) * (z_alpha + z_beta)^2 / d^2
            n2 = (1 + 1/k) * (z_eff ** 2) / (d ** 2)
            n1 = k * n2

            n_per_group_2 = int(np.ceil(n2))  # 较小组的样本量
            n_per_group = int(np.ceil(n1))  # 较大组的样本量
            total_n = n_per_group + n_per_group_2
        else:
            n_per_group_2 = n_per_group
            total_n = 2 * n_per_group

        # 计算实际效能
        actual_power = self._calculate_actual_power(n_per_group, n_per_group_2, d)

        # 效应量解释
        effect_interpretation = self._interpret_effect_size(d)

        return {
            'test_method': 'two_sample_ttest',
            'design_type': self.design_type,
            'alpha': self.alpha,
            'power': self.power,
            'effect_size': d,
            'effect_interpretation': effect_interpretation,
            'n_per_group': n_per_group,  # 第一组（或较大组）
            'n_per_group_2': n_per_group_2,  # 第二组（或较小组）
            'total_n': total_n,
            'n1_ratio': n1_ratio,
            'n2_ratio': n2_ratio,
            'actual_power': actual_power,
            'z_alpha': float(self.z_alpha),
            'z_beta': float(self.z_beta),
            'formula': 'n = 2 * (z_α/2 + z_β)² / d²'
        }

    def _calculate_actual_power(
        self,
        n1: int,
        n2: int,
        d: float
    ) -> float:
        """
        计算给定样本量下的实际统计效能

        参数：
            n1: 第一组样本量
            n2: 第二组样本量
            d: 效应量

        返回：
            float: 实际统计效能
        """
        # 计算标准误
        se = np.sqrt(1/n1 + 1/n2)

        # 计算在真实效应量d下，拒绝原假设的概率
        # 非中心参数 λ = d / se
        # power = P(Z > z_alpha - λ) + P(Z < -z_alpha - λ) 近似为单侧

        # 使用简化的双样本公式
        # Z_beta = d * sqrt(n/2) - z_alpha
        # 其中 n 为每组样本量（假设相等）
        if n1 == n2:
            n = n1
            z_beta_approx = d * np.sqrt(n/2) - self.z_alpha
        else:
            # 非均衡设计
            z_beta_approx = d / se - self.z_alpha

        actual_power = stats.norm.cdf(z_beta_approx)

        return float(min(1.0, max(0.0, actual_power)))

    def _interpret_effect_size(self, d: float) -> str:
        """
        解释Cohen's d效应量

        参数：
            d: Cohen's d 值

        返回：
            str: 效应量解释
        """
        abs_d = abs(d)
        if abs_d < 0.2:
            magnitude = "极小"
        elif abs_d < 0.5:
            magnitude = "小"
        elif abs_d < 0.8:
            magnitude = "中等"
        else:
            magnitude = "大"

        return f"Cohen's d = {d:.2f}，效应量{magnitude}"

    def power_curve(
        self,
        n_range: range,
        n1_ratio: int = 1,
        n2_ratio: int = 1
    ) -> Dict:
        """
        生成样本量-效能曲线数据

        参数：
            n_range: 样本量范围
            n1_ratio: 第一组比例
            n2_ratio: 第二组比例

        返回：
            Dict: 包含曲线数据的字典
        """
        curve_data = []

        for n in n_range:
            if n1_ratio == n2_ratio:
                actual_power = self._calculate_actual_power(n, n, self.effect_size)
            else:
                k = n1_ratio / n2_ratio
                n2 = n / (1 + k)
                n1 = n - n2
                actual_power = self._calculate_actual_power(int(n1), int(n2), self.effect_size)

            curve_data.append({
                'n_per_group': n,
                'total_n': 2 * n if n1_ratio == n2_ratio else int(n),
                'power': actual_power
            })

        return {
            'effect_size': self.effect_size,
            'curve_data': curve_data
        }