#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结果格式化模块

将分析结果格式化为标准JSON输出格式
"""
from typing import Dict, List, Any
import numpy as np


class ResultFormatter:
    """
    结果格式化器

    将各种检验结果格式化为统一的JSON输出格式
    """

    def __init__(self, alpha: float = 0.05, confidence_level: float = 0.95):
        """
        初始化结果格式化器

        参数：
            alpha: 显著性水平
            confidence_level: 置信水平
        """
        self.alpha = alpha
        self.confidence_level = confidence_level

    def format_results(
        self,
        results: List[Dict],
        correction_method: str = 'None',
        alpha: float = 0.05
    ) -> Dict:
        """
        格式化所有分析结果

        参数：
            results: 所有分析结果的列表
            correction_method: 多重比较校正方法
            alpha: 显著性水平

        返回：
            Dict: 格式化后的完整结果
        """
        formatted_results = []

        for result in results:
            formatted = self._format_single_result(result, alpha)
            formatted_results.append(formatted)

        return {
            'results': formatted_results,
            'metadata': {
                'correction_method': correction_method,
                'alpha': alpha,
                'confidence_level': self.confidence_level
            }
        }

    def _format_single_result(self, result: Dict, alpha: float) -> Dict:
        """
        格式化单个分析结果

        参数：
            result: 原始结果字典
            alpha: 显著性水平

        返回：
            Dict: 格式化后的结果
        """
        formatted = {
            'variable': result.get('variable'),
            'groupby': result.get('groupby'),
            'groups': result.get('groups'),
            'method': result.get('method'),
            'method_cn': result.get('method_cn'),
            'n': result.get('n', {})
        }

        # 格式化统计量
        stats = result.get('statistics', {})

        # 计算效应量解释
        effect_size = stats.get('effect_size')
        effect_interpretation = stats.get('effect_interpretation', '')

        # 格式化主统计量
        formatted['statistics'] = {
            'statistic': self._format_number(stats.get('statistic')),
            'df': self._format_number(stats.get('df')),
            'p_raw': self._format_pvalue(stats.get('p_raw')),
            'p_adjusted': self._format_pvalue(stats.get('p_adjusted')),
            'effect_size': self._format_number(effect_size),
            'effect_interpretation': effect_interpretation,
            'correction_method': stats.get('correction_method')
        }

        # 添加方法特定的信息
        method = result.get('method', '')

        if method in ['independent_ttest', 'welch_ttest', 'paired_ttest', 'mann_whitney_u']:
            # 两组比较的结果
            formatted['means'] = {}
            for group in result.get('groups', []):
                if 'means' in result and group in result['means']:
                    formatted['means'][group] = result['means'][group]
                else:
                    formatted['means'][group] = result.get('n', {}).get(group, {})

            # 添加均值差异信息
            if 'mean_diff' in stats:
                formatted['mean_difference'] = {
                    'value': self._format_number(stats.get('mean_diff')),
                    'ci': self._format_ci(stats.get('ci')),
                    'interpretation': f"组间差异为{self._format_number(stats.get('mean_diff'))}（95%CI: {self._format_number(stats.get('ci', [None, None])[0])}-{self._format_number(stats.get('ci', [None, None])[1])})"
                }

        elif method in ['oneway_anova', 'kruskal_wallis']:
            # 多组比较的结果
            formatted['group_means'] = result.get('group_means', [])
            formatted['group_ns'] = result.get('group_ns', [])

        elif method in ['chi_square', 'fisher_exact']:
            # 率比较的结果
            if 'contingency_table' in result:
                formatted['contingency_table'] = result['contingency_table']
            if 'prop1' in stats and 'prop2' in stats:
                formatted['proportions'] = {
                    result['groups'][0]: self._format_number(stats.get('prop1')),
                    result['groups'][1]: self._format_number(stats.get('prop2'))
                }
                formatted['proportion_difference'] = {
                    'value': self._format_number(stats.get('prop_diff')),
                    'ci': self._format_ci(stats.get('prop_ci'))
                }

        # 添加检验前提条件信息
        if 'normality' in result:
            formatted['assumptions'] = {
                'normality': result['normality'],
                'variance_homogeneity': result.get('variance_homogeneity')
            }

        # 添加显著性判断
        p_value = stats.get('p_adjusted', stats.get('p_raw', 1))
        is_significant = p_value is not None and p_value < alpha

        formatted['conclusion'] = {
            'significant': is_significant,
            'p_value': self._format_pvalue(p_value),
            'alpha': alpha,
            'interpretation': self._interpret_conclusion(is_significant, effect_interpretation, method)
        }

        return formatted

    def _format_number(self, value: Any) -> Any:
        """格式化数字"""
        if value is None:
            return None
        if isinstance(value, (int, np.integer)):
            return int(value)
        if isinstance(value, (float, np.floating)):
            if np.isnan(value):
                return None
            if np.isinf(value):
                return 'inf' if value > 0 else '-inf'
            # 保留4位有效数字
            if abs(value) < 0.0001 or abs(value) > 10000:
                return float(f"{value:.4e}")
            else:
                return round(float(value), 4)
        return value

    def _format_pvalue(self, p_value: Any) -> Any:
        """格式化p值"""
        if p_value is None:
            return None
        if isinstance(p_value, (float, np.floating)):
            if np.isnan(p_value):
                return None
            if p_value < 0.001:
                return '<0.001'
            elif p_value > 0.999:
                return '>0.999'
            else:
                return round(float(p_value), 4)
        return p_value

    def _format_ci(self, ci: Any) -> Any:
        """格式化置信区间"""
        if ci is None:
            return None
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            return [self._format_number(ci[0]), self._format_number(ci[1])]
        return ci

    def _interpret_conclusion(
        self,
        is_significant: bool,
        effect_interpretation: str,
        method: str
    ) -> str:
        """
        生成结论性解释

        参数：
            is_significant: 是否显著
            effect_interpretation: 效应量解释
            method: 检验方法

        返回：
            str: 结论解释
        """
        significance = "差异有统计学意义" if is_significant else "差异无统计学意义"

        if effect_interpretation:
            return f"{significance}，{effect_interpretation}"
        else:
            return significance

    def generate_summary_table(
        self,
        results: List[Dict]
    ) -> str:
        """
        生成汇总表格（Markdown格式）

        参数：
            results: 分析结果列表

        返回：
            str: Markdown格式的表格
        """
        lines = [
            "| 变量 | 分组 | 方法 | 统计量 | p值 | 效应量 | 结论 |",
            "|-----|------|------|--------|-----|--------|------|"
        ]

        for result in results:
            variable = result.get('variable', '')
            groupby = result.get('groupby', '')
            method_cn = result.get('method_cn', '')
            groups = ', '.join(str(g) for g in result.get('groups', []))

            stats = result.get('statistics', {})
            statistic = stats.get('statistic')
            p_value = stats.get('p_adjusted', stats.get('p_raw'))
            effect_size = stats.get('effect_size')
            effect_magnitude = stats.get('effect_interpretation', '')

            conclusion = result.get('conclusion', {})
            sig_text = "显著" if conclusion.get('significant') else "不显著"

            lines.append(
                f"| {variable} | {groups} | {method_cn} | "
                f"{self._format_number(statistic)} | {self._format_pvalue(p_value)} | "
                f"{self._format_number(effect_size)} | {sig_text} |"
            )

        return '\n'.join(lines)