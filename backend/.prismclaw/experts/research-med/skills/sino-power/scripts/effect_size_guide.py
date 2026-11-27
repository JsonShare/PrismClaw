#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
效应量参考值指南

提供各检验方法的效应量参考值和选择建议
"""
from typing import Dict, List


class EffectSizeGuide:
    """
    效应量参考指南

    提供Cohen's d、w、HR等效应量的参考值和临床解释
    """

    # 两样本t检验 Cohen's d 参考值
    TTEST_COHENS_D = {
        'small': {
            'value': 0.2,
            'description': '均值差异0.25个标准差',
            'clinical': '微小的、可能不重要的差异',
            'example': '血压降低2-3mmHg'
        },
        'medium': {
            'value': 0.5,
            'description': '均值差异0.5个标准差',
            'clinical': '中等差异，临床上可感知的变化',
            'example': '疼痛评分降低约1分（0-10量表）'
        },
        'large': {
            'value': 0.8,
            'description': '均值差异0.8个标准差',
            'clinical': '显著的、临床上有意义的差异',
            'example': '功能评分改善超过最小临床重要差异(MCID)'
        }
    }

    # 卡方检验 Cohen's w 参考值
    CHI2_COHENS_W = {
        'small': {
            'value': 0.1,
            'description': '比例差约10%',
            'clinical': '微小但可能累积重要的差异',
            'example': '有效率从60%到70%'
        },
        'medium': {
            'value': 0.3,
            'description': '比例差约30%',
            'clinical': '中等差异，临床上相关',
            'example': '有效率从50%到65%'
        },
        'large': {
            'value': 0.5,
            'description': '比例差约50%',
            'clinical': '显著差异，临床决策相关',
            'example': '有效率从40%到70%'
        }
    }

    # Log-rank检验 HR参考值
    LOG_RANK_HR = {
        'small': {
            'value': 0.75,
            'description': '风险降低25%（或增加33%）',
            'clinical': '可能的临床获益，需更多证据',
            'example': 'HR=0.75，1年生存率从60%改善到70%'
        },
        'medium': {
            'value': 0.5,
            'description': '风险降低50%',
            'clinical': '明确的临床获益',
            'example': 'HR=0.5，中位生存期从10个月延长到20个月'
        },
        'large': {
            'value': 0.33,
            'description': '风险降低67%',
            'clinical': '显著临床获益，可能改变临床实践',
            'example': 'HR=0.33，2年生存率从25%提高到50%'
        }
    }

    def __init__(self):
        """初始化效应量指南"""
        pass

    def get_reference_table(self, test_method: str) -> str:
        """
        获取指定检验方法的效应量参考表（Markdown格式）

        参数：
            test_method: 检验方法

        返回：
            str: Markdown格式的参考表
        """
        if test_method == 'two_sample_ttest':
            data = self.TTEST_COHENS_D
            title = "Cohen's d（两样本t检验）"
        elif test_method == 'chi_square':
            data = self.CHI2_COHENS_W
            title = "Cohen's w（卡方检验）"
        elif test_method == 'logrank':
            data = self.LOG_RANK_HR
            title = "风险比 HR（Log-rank检验）"
        else:
            return "| 效应量 | 值 | 说明 |\n|-------|-----|------|"

        lines = [
            f"## {title}",
            "",
            "| 效应量级别 | 值 | 描述 | 临床意义 | 示例 |",
            "|-----------|-----|------|---------|------|"
        ]

        for level, info in data.items():
            lines.append(
                f"| {level.capitalize()} | {info['value']} | {info['description']} | "
                f"{info['clinical']} | {info['example']} |"
            )

        return '\n'.join(lines)

    def recommend_effect_size(
        self,
        test_method: str,
        based_on: str = 'literature'
    ) -> Dict:
        """
        推荐效应量选择

        参数：
            test_method: 检验方法
            based_on: 依据（literature/meta_analysis/clinical）

        返回：
            Dict: 推荐值和建议
        """
        recommendations = {
            'two_sample_ttest': {
                'recommended': 0.5,
                'alternatives': [0.2, 0.8],
                'basis': '中等效应量(0.5)是临床研究的常用默认值',
                'note': '如果没有文献依据，建议从0.5开始计算'
            },
            'chi_square': {
                'recommended': 0.3,
                'alternatives': [0.1, 0.5],
                'basis': '中等效应量(0.3)对应约15-20%的率差',
                'note': '临床有意义的最小率差通常在10%以上'
            },
            'logrank': {
                'recommended': 0.5,
                'alternatives': [0.75, 0.33],
                'basis': 'HR=0.5表示50%风险降低，是临床上常要求的效应量',
                'note': '肿瘤和心血管研究中，HR<0.7通常被认为有临床意义'
            }
        }

        return recommendations.get(test_method, {
            'recommended': 0.5,
            'alternatives': [0.2, 0.8],
            'basis': '使用中等效应量作为默认',
            'note': '建议根据文献或预研究调整'
        })

    def interpret_effect_size(
        self,
        test_method: str,
        value: float
    ) -> Dict:
        """
        解释给定效应量的临床意义

        参数：
            test_method: 检验方法
            value: 效应量值

        返回：
            Dict: 解释信息
        """
        if test_method == 'two_sample_ttest':
            data = self.TTEST_COHENS_D
            value_key = 'value'
        elif test_method == 'chi_square':
            data = self.CHI2_COHENS_W
            value_key = 'value'
        elif test_method == 'logrank':
            data = self.LOG_RANK_HR
            value_key = 'value'
        else:
            return {
                'magnitude': 'unknown',
                'interpretation': f'无法解释{test_method}的效应量{value}'
            }

        # 找到最接近的级别
        closest_level = None
        min_diff = float('inf')

        for level, info in data.items():
            diff = abs(info[value_key] - value)
            if diff < min_diff:
                min_diff = diff
                closest_level = level

        if closest_level:
            info = data[closest_level]
            return {
                'magnitude': closest_level,
                'value': info['value'],
                'interpretation': info['description'],
                'clinical_significance': info['clinical'],
                'example': info['example']
            }

        return {
            'magnitude': 'custom',
            'value': value,
            'interpretation': f'自定义效应量{value}'
        }

    def sample_size_comparison(self, test_method: str) -> str:
        """
        生成不同效应量下样本量的对比表

        参数：
            test_method: 检验方法

        返回：
            str: Markdown格式表格
        """
        lines = [
            f"## 不同效应量下的样本量对比（{test_method}）",
            "",
            "| 效应量级别 | 效应量值 | 每组样本量(估算) | 总样本量(估算) |",
            "|-----------|---------|----------------|----------------|"
        ]

        if test_method == 'two_sample_ttest':
            data = self.TTEST_COHENS_D
        elif test_method == 'chi_square':
            data = self.CHI2_COHENS_W
        elif test_method == 'logrank':
            data = self.LOG_RANK_HR
        else:
            return "| 效应量 | 每组样本 | 总样本 |\n|-------|---------|--------|"

        # 简化计算
        # n ≈ 2 * (1.96 + 0.84)² / d² ≈ 16 / d²
        for level, info in data.items():
            d = info['value']
            n_per_group = int(np.ceil(16 / (d ** 2)))
            total_n = n_per_group * 2

            lines.append(
                f"| {level.capitalize()} | {d} | {n_per_group} | {total_n} |"
            )

        return '\n'.join(lines)


# 需要导入numpy
import numpy as np