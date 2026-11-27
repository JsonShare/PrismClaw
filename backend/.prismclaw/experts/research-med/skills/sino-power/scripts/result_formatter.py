#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
样本量计算结果格式化模块
"""
from typing import Dict, Any
import numpy as np


class PowerResultFormatter:
    """
    样本量计算结果格式化器

    生成标准化的Markdown格式报告
    """

    def __init__(self):
        """初始化格式化器"""
        pass

    def format_result(
        self,
        result: Dict,
        effect_size_reference: str,
        test_method: str
    ) -> str:
        """
        格式化样本量计算结果

        参数：
            result: 样本量计算结果
            effect_size_reference: 效应量参考表
            test_method: 检验方法

        返回：
            str: Markdown格式报告
        """
        lines = [
            "# 样本量计算报告",
            "",
            f"**检验方法**: {self._get_method_cn(test_method)}",
            f"**设计类型**: {self._get_design_type_cn(result.get('design_type', 'superiority'))}",
            f"**生成时间**: {self._get_timestamp()}",
            "",
            "---",
            "",
            "## 参数设置",
            "",
            f"| 参数 | 值 |",
            f"|------|-----|",
            f"| 显著性水平(α) | {result.get('alpha', 0.05)} |",
            f"| 统计效能(1-β) | {result.get('power', 0.8)} |",
            f"| 效应量 | {result.get('effect_size', 'N/A')} |",
            f"| 效应量类型 | {result.get('effect_size_type', 'custom')} |",
            f"| 效应量依据 | {result.get('effect_size_basis', '用户指定')} |",
            f"| 分组比例 | {result.get('n1_ratio', 1)}:{result.get('n2_ratio', 1)} |",
            f"| 失访率 | {result.get('loss_rate', 0)*100:.0f}% |",
            ""
        ]

        # 效应量解释
        if 'effect_interpretation' in result:
            lines.extend([
                "## 效应量解释",
                "",
                f"{result['effect_interpretation']}",
                ""
            ])

        # 主要结果
        lines.extend([
            "---",
            "",
            "## 样本量计算结果",
            "",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 每组最小样本量 | {result.get('n_per_group', 'N/A')} : {result.get('n_per_group_2', 'N/A')} |",
            f"| 总样本量(不含失访) | {result.get('total_n', 'N/A')} |",
        ])

        # 失访补偿后的结果
        if 'n_per_group_adjusted' in result:
            lines.extend([
                f"| 每组样本量(含{result.get('loss_rate', 0)*100:.0f}%失访) | "
                f"{result['n_per_group_adjusted']} : {result.get('n_per_group_2_adjusted', 'N/A')} |",
                f"| 总样本量(含失访) | {result.get('total_n_adjusted', 'N/A')} |",
            ])

        # Log-rank特殊参数
        if test_method == 'logrank':
            lines.extend([
                "",
                f"| 预期事件数 | {result.get('required_events', 'N/A')} |",
                f"| 预期事件率 | {result.get('expected_event_rate', 'N/A')} |",
                f"| {result.get('time_note', '')} |",
            ])

        lines.extend(["", "---", ""])

        # 敏感性分析
        if 'sensitivity_analysis' in result:
            lines.extend([
                "## 敏感性分析",
                "",
                "不同效应量假设下的样本量需求：",
                "",
                "| 效应量 | 每组样本 | 总样本(10%失访) | 总样本(15%失访) | 总样本(20%失访) |",
                "|-------|---------|----------------|----------------|----------------|"
            ])

            sensitivity = result['sensitivity_analysis']
            for magnitude in ['small', 'medium', 'large']:
                if magnitude not in sensitivity:
                    continue
                s = sensitivity[magnitude]
                n_10 = s.get('by_loss_rate', {}).get('loss_10pct', {}).get('n_total', '-')
                n_15 = s.get('by_loss_rate', {}).get('loss_15pct', {}).get('n_total', '-')
                n_20 = s.get('by_loss_rate', {}).get('loss_20pct', {}).get('n_total', '-')

                es_label = f"{magnitude} ({s.get('effect_size', '')})"

                lines.append(
                    f"| {es_label} | {s.get('n_per_group', '-')} | {n_10} | {n_15} | {n_20} |"
                )

            lines.extend(["", "---", ""])

        # 效应量参考表
        if effect_size_reference:
            lines.extend([
                "## 效应量参考值",
                "",
                effect_size_reference,
                ""
            ])

        # 计算公式
        lines.extend([
            "---",
            "",
            "## 计算公式",
            "",
            f"`{result.get('formula', 'N/A')}`",
            ""
        ])

        if test_method == 'two_sample_ttest':
            lines.extend([
                "其中：",
                "- z_α/2: 标准正态分布在α/2处的值",
                "- z_β: 标准正态分布在β处的值",
                "- d: Cohen's d 效应量",
                "",
                "**简化公式**: n ≈ 16 / d² （当α=0.05, power=0.8时）"
            ])
        elif test_method == 'chi_square':
            lines.extend([
                "其中：",
                "- w: Cohen's w 效应量",
                "",
                "**简化公式**: n ≈ 16 / w² （当α=0.05, power=0.8时）"
            ])
        elif test_method == 'logrank':
            lines.extend([
                "其中：",
                "- D: 所需事件数",
                "- HR: 风险比（Hazard Ratio）",
                "",
                "**注意**: Log-rank检验直接估计所需事件数，样本量 = 事件数 / 预期事件率"
            ])

        # 假设和限制
        lines.extend([
            "",
            "---",
            "",
            "## 假设和限制",
            "",
            "1. **正态性假设**: t检验假设数据近似正态分布",
            "2. **独立性假设**: 样本之间相互独立",
            "3. **方差齐性**: 假设两组方差相等（如果不等，将使用Welch校正）",
            "4. **失访率假设**: 假设预期失访率准确",
            "5. **效应量假设**: 效应量估计的准确性直接影响样本量计算的可靠性",
            "",
            "## 建议",
            "",
            "- 在开始研究前，建议进行预研究以更准确地估计效应量",
            "- 如果不确定性较大，使用保守的样本量估计",
            "- 考虑研究的实际可行性，适当增加样本量以应对失访"
        ])

        return '\n'.join(lines)

    def _get_method_cn(self, method: str) -> str:
        """获取检验方法的中文名称"""
        method_cn = {
            'two_sample_ttest': '两样本t检验',
            'chi_square': '卡方检验',
            'logrank': 'Log-rank检验',
            'anova': '方差分析',
            'correlation': '相关性检验'
        }
        return method_cn.get(method, method)

    def _get_design_type_cn(self, design_type: str) -> str:
        """获取设计类型的中文名称"""
        design_cn = {
            'superiority': '优效性检验',
            'equivalence': '等效性检验',
            'non_inferiority': '非劣效性检验'
        }
        return design_cn.get(design_type, design_type)

    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _format_number(self, value: Any) -> Any:
        """格式化数字"""
        if value is None:
            return 'N/A'
        if isinstance(value, (int, np.integer)):
            return int(value)
        if isinstance(value, (float, np.floating)):
            if np.isnan(value):
                return 'N/A'
            if np.isinf(value):
                return '∞'
            if abs(value) < 0.001 or abs(value) > 10000:
                return f"{value:.2e}"
            else:
                return round(float(value), 2)
        return value