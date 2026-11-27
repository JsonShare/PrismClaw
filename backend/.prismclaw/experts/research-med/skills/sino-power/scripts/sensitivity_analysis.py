#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
敏感性分析模块

对不同效应量假设下的样本量进行敏感性分析
"""
from typing import Dict
import numpy as np


class SensitivityAnalyzer:
    """
    敏感性分析器

    分析在不同效应量假设下的样本量变化
    """

    # 效应量标准参考值
    EFFECT_SIZE_REFERENCE = {
        'two_sample_ttest': {
            'small': 0.2,
            'medium': 0.5,
            'large': 0.8
        },
        'chi_square': {
            'small': 0.1,
            'medium': 0.3,
            'large': 0.5
        },
        'logrank': {
            'small': 0.75,   # 对应HR
            'medium': 0.5,   # HR = 0.5 表示50%风险降低
            'large': 0.33    # HR = 0.33 表示67%风险降低
        }
    }

    def __init__(
        self,
        test_method: str,
        alpha: float = 0.05,
        power: float = 0.8,
        design_type: str = 'superiority',
        loss_rate: float = 0.15
    ):
        """
        初始化敏感性分析器

        参数：
            test_method: 检验方法
            alpha: 显著性水平
            power: 统计效能
            design_type: 设计类型
            loss_rate: 失访率
        """
        self.test_method = test_method
        self.alpha = alpha
        self.power = power
        self.design_type = design_type
        self.loss_rate = loss_rate

        # 导入计算器
        if test_method == 'two_sample_ttest':
            from ttest_power import TTestPowerCalculator
            self.calculator_class = TTestPowerCalculator
        elif test_method == 'chi_square':
            from chi2_power import Chi2PowerCalculator
            self.calculator_class = Chi2PowerCalculator
        elif test_method == 'logrank':
            from logrank_power import LogRankPowerCalculator
            self.calculator_class = LogRankPowerCalculator
        else:
            self.calculator_class = None

    def run_sensitivity_analysis(
        self,
        loss_rates: list = None
    ) -> Dict:
        """
        运行敏感性分析

        参数：
            loss_rates: 要分析的失访率列表

        返回：
            Dict: 敏感性分析结果
        """
        if self.calculator_class is None:
            return {'error': f'未知检验方法: {self.test_method}'}

        effect_sizes = self.EFFECT_SIZE_REFERENCE.get(
            self.test_method,
            {'small': 0.2, 'medium': 0.5, 'large': 0.8}
        )

        if loss_rates is None:
            loss_rates = [0.10, 0.15, 0.20]  # 10%, 15%, 20%

        results = {}

        for magnitude, effect_size in effect_sizes.items():
            # 创建计算器
            calculator = self.calculator_class(
                alpha=self.alpha,
                power=self.power,
                effect_size=effect_size,
                design_type=self.design_type
            )

            # 计算样本量
            result = calculator.calculate()

            # 应用不同失访率
            n_base = result['total_n']
            sensitivity_for_es = {}

            for lr in loss_rates:
                n_adjusted = int(np.ceil(n_base / (1 - lr)))
                sensitivity_for_es[f'loss_{int(lr*100)}pct'] = {
                    'n_total': n_adjusted,
                    'n_per_group': int(np.ceil(n_adjusted / 2))
                }

            results[magnitude] = {
                'effect_size': effect_size,
                'n_per_group': result.get('n_per_group'),
                'n_per_group_2': result.get('n_per_group_2'),
                'total_n': n_base,
                'total_n_adjusted': sensitivity_for_es[f'loss_{int(self.loss_rate*100)}pct']['n_total'],
                'by_loss_rate': sensitivity_for_es
            }

        return results

    def generate_sensitivity_table(self, results: Dict) -> str:
        """
        生成敏感性分析表格（Markdown格式）

        参数：
            results: run_sensitivity_analysis的返回结果

        返回：
            str: Markdown表格
        """
        lines = [
            '| 效应量 | 每组样本 | 总样本(不含失访) | 总样本(10%失访) | 总样本(15%失访) | 总样本(20%失访) |',
            '|-------|---------|----------------|----------------|----------------|----------------|'
        ]

        for magnitude in ['small', 'medium', 'large']:
            if magnitude not in results:
                continue

            r = results[magnitude]
            es_label = f"{magnitude} (d={r['effect_size']})" if isinstance(r['effect_size'], (int, float)) else magnitude

            n_10 = r['by_loss_rate'].get('loss_10pct', {}).get('n_total', '-')
            n_15 = r['by_loss_rate'].get('loss_15pct', {}).get('n_total', '-')
            n_20 = r['by_loss_rate'].get('loss_20pct', {}).get('n_total', '-')

            lines.append(
                f"| {es_label} | {r.get('n_per_group', '-')} | {r['total_n']} | "
                f"{n_10} | {n_15} | {n_20} |"
            )

        return '\n'.join(lines)

    def power_curve_data(
        self,
        effect_size: float,
        n_range: range
    ) -> list:
        """
        生成功效曲线数据

        参数：
            effect_size: 效应量
            n_range: 样本量范围

        返回：
            list: 功效曲线数据
        """
        if self.calculator_class is None:
            return []

        calculator = self.calculator_class(
            alpha=self.alpha,
            power=self.power,
            effect_size=effect_size,
            design_type=self.design_type
        )

        curve_data = []

        for n in n_range:
            # 简化的功效计算
            if self.test_method == 'two_sample_ttest':
                # n = 2 * (z_α + z_β)² / d²
                # 所以 z_β = d * sqrt(n/2) - z_α
                from scipy import stats
                z_alpha = stats.norm.ppf(1 - self.alpha / 2)
                z_beta = effect_size * np.sqrt(n / 2) - z_alpha
                power = stats.norm.cdf(z_beta)
            else:
                power = 0.5  # 默认

            curve_data.append({
                'n': n,
                'total_n': 2 * n,
                'power': float(min(1.0, max(0.0, power)))
            })

        return curve_data