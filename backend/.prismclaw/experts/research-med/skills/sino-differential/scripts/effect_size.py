#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
效应量计算模块

提供Cohen's d、Hedges' g、η²、Cramer's V、Odds Ratio、等级二列相关系数等
"""
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
from scipy import stats


class EffectSizeCalculator:
    """
    效应量计算器

    提供各种效应量指标的计算和解释
    """

    # Cohen's d 解释标准
    COHEN_D_THRESHOLDS = {'small': 0.2, 'medium': 0.5, 'large': 0.8}

    # η² 解释标准
    ETA_SQUARED_THRESHOLDS = {'small': 0.01, 'medium': 0.06, 'large': 0.14}

    # Cramér's V 解释标准（2x2表）
    CRAMERS_V_THRESHOLDS = {'small': 0.1, 'medium': 0.3, 'large': 0.5}

    # r (相关系数) 解释标准
    R_THRESHOLDS = {'small': 0.1, 'medium': 0.3, 'large': 0.5}

    def __init__(self, ci_level: float = 0.95):
        """
        初始化效应量计算器

        参数：
            ci_level: 置信水平
        """
        self.ci_level = ci_level

    def cohens_d(
        self,
        group1: Union[List, np.ndarray],
        group2: Union[List, np.ndarray],
        pooled: bool = True
    ) -> Dict:
        """
        计算Cohen's d（两均值差异的效应量）

        参数：
            group1: 第一组数据
            group2: 第二组数据
            pooled: 是否使用 pooled variance（True用混合标准差，False用单独标准差）

        返回：
            Dict: 包含效应量和解释的字典
        """
        group1 = np.array(group1)
        group2 = np.array(group2)

        # 去除缺失值
        group1 = group1[~np.isnan(group1)]
        group2 = group2[~np.isnan(group2)]

        n1, n2 = len(group1), len(group2)
        mean1, mean2 = np.mean(group1), np.mean(group2)
        var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)

        if pooled:
            # Pooled standard deviation
            df = n1 + n2 - 2
            pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / df
            sd = np.sqrt(pooled_var)
        else:
            # 使用较大组的方差
            sd = np.sqrt(max(var1, var2))

        if sd == 0:
            return {
                'effect_size': None,
                'sd': 0,
                'interpretation': '标准差为0，无法计算效应量',
                'ci': None
            }

        d = (mean1 - mean2) / sd

        # 解释
        interpretation = self._interpret_cohens_d(d)

        # 置信区间（近似）
        se_d = np.sqrt((n1 + n2) / (n1 * n2) + d**2 / (2 * (n1 + n2)))
        z_crit = stats.norm.ppf((1 + self.ci_level) / 2)
        ci_lower = d - z_crit * se_d
        ci_upper = d + z_crit * se_d

        # 转换为其他表示
        # overlap = 2 * norm.cdf(-abs(d)/2)
        # U1 = norm.cdf(d)

        return {
            'effect_size': float(d),
            'cohens_d': float(d),
            'mean1': float(mean1),
            'mean2': float(mean2),
            'sd_pooled': float(np.sqrt(pooled_var)) if pooled else None,
            'n1': n1,
            'n2': n2,
            'interpretation': interpretation,
            'ci': (float(ci_lower), float(ci_upper)),
            'magnitude': self._get_magnitude(d, self.COHEN_D_THRESHOLDS)
        }

    def hedges_g(
        self,
        group1: Union[List, np.ndarray],
        group2: Union[List, np.ndarray]
    ) -> Dict:
        """
        计算Hedges' g（偏差校正的Cohen's d，更适合小样本）

        参数：
            group1: 第一组数据
            group2: 第二组数据

        返回：
            Dict: 包含效应量和解释的字典
        """
        group1 = np.array(group1)
        group2 = np.array(group2)

        # 去除缺失值
        group1 = group1[~np.isnan(group1)]
        group2 = group2[~np.isnan(group2)]

        n1, n2 = len(group1), len(group2)
        mean1, mean2 = np.mean(group1), np.mean(group2)
        var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)

        # Pooled variance
        df = n1 + n2 - 2
        pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / df
        sd = np.sqrt(pooled_var)

        # Cohen's d
        d = (mean1 - mean2) / sd

        # 偏差校正因子
        correction = 1 - (3 / (4 * df - 1))
        g = d * correction

        interpretation = self._interpret_cohens_d(g)  # 使用Cohen's d的解释标准

        return {
            'effect_size': float(g),
            'hedges_g': float(g),
            'cohens_d': float(d),
            'correction_factor': float(correction),
            'df': df,
            'interpretation': interpretation,
            'magnitude': self._get_magnitude(g, self.COHEN_D_THRESHOLDS)
        }

    def eta_squared(
        self,
        f_stat: float,
        df_between: int,
        df_within: int
    ) -> Dict:
        """
        计算η²（方差解释比例，用于ANOVA）

        参数：
            f_stat: F统计量
            df_between: 组间自由度
            df_within: 组内自由度

        返回：
            Dict: 包含效应量和解释的字典
        """
        # η² = df_between * F / (df_between * F + df_within)
        if df_within <= 0:
            return {
                'effect_size': None,
                'interpretation': '组内自由度不足'
            }

        eta_sq = (df_between * f_stat) / (df_between * f_stat + df_within)

        # 确保在[0,1]范围内
        eta_sq = max(0, min(1, eta_sq))

        # 解释
        interpretation = self._interpret_eta_squared(eta_sq)

        # 计算部分η²
        partial_eta_sq = eta_sq

        return {
            'effect_size': float(eta_sq),
            'eta_squared': float(eta_sq),
            'partial_eta_squared': float(partial_eta_sq),
            'f_stat': float(f_stat),
            'df': (df_between, df_within),
            'interpretation': interpretation,
            'magnitude': self._get_magnitude(eta_sq, self.ETA_SQUARED_THRESHOLDS)
        }

    def omega_squared(
        self,
        f_stat: float,
        df_between: int,
        df_within: int
    ) -> Dict:
        """
        计算ω²（更保守的效应量估计）

        参数：
            f_stat: F统计量
            df_between: 组间自由度
            df_within: 组内自由度

        返回：
            Dict: 包含效应量和解释的字典
        """
        if df_within <= 0:
            return {
                'effect_size': None,
                'interpretation': '组内自由度不足'
            }

        # ω² = (df_between * (F - 1)) / (df_between * (F - 1) + df_within + 1)
        numerator = df_between * (f_stat - 1)
        denominator = df_between * (f_stat - 1) + df_within + 1

        if denominator <= 0:
            return {
                'effect_size': 0,
                'omega_squared': 0,
                'interpretation': '效应量为0或负值'
            }

        omega_sq = numerator / denominator

        # 确保在合理范围内
        omega_sq = max(0, min(1, omega_sq))

        return {
            'effect_size': float(omega_sq),
            'omega_squared': float(omega_sq),
            'interpretation': self._interpret_eta_squared(omega_sq),
            'magnitude': self._get_magnitude(omega_sq, self.ETA_SQUARED_THRESHOLDS)
        }

    def cramers_v(
        self,
        chi2: float,
        n: int,
        min_dim: int
    ) -> Dict:
        """
        计算Cramér's V（列联表的关联强度）

        参数：
            chi2: 卡方统计量
            n: 总样本量
            min_dim: 行和列中较小值减1（对于2x2表，min_dim=1）

        返回：
            Dict: 包含效应量和解释的字典
        """
        if n <= 0 or min_dim <= 0:
            return {
                'effect_size': None,
                'interpretation': '样本量不足'
            }

        # Cramér's V = sqrt(chi2 / (n * min_dim))
        v = np.sqrt(chi2 / (n * min_dim))

        # 确保在[0,1]范围内
        v = max(0, min(1, v))

        interpretation = self._interpret_cramers_v(v)

        return {
            'effect_size': float(v),
            'cramers_v': float(v),
            'chi2': float(chi2),
            'n': n,
            'min_dim': min_dim,
            'interpretation': interpretation,
            'magnitude': self._get_magnitude(v, self.CRAMERS_V_THRESHOLDS)
        }

    def odds_ratio(
        self,
        a: int, b: int, c: int, d: int
    ) -> Dict:
        """
        计算比值比（Odds Ratio）

        参数：
            a: 组1事件数
            b: 组1未事件数
            c: 组2事件数
            d: 组2未事件数

        返回：
            Dict: 包含OR值和解释的字典
        """
        # OR = (a*b) / (c*d)
        if c * b == 0:
            if a * d > 0:
                or_value = float('inf')
            else:
                or_value = 1.0
        else:
            or_value = (a * d) / (c * b)

        # 对数转换
        if or_value > 0 and or_value != float('inf'):
            log_or = np.log(or_value)
            # se_log_or = sqrt(1/a + 1/b + 1/c + 1/d)
            se_log_or = np.sqrt(1/a + 1/b + 1/c + 1/d) if all(x > 0 for x in [a, b, c, d]) else None

            if se_log_or:
                z_crit = stats.norm.ppf((1 + self.ci_level) / 2)
                ci_lower = np.exp(log_or - z_crit * se_log_or)
                ci_upper = np.exp(log_or + z_crit * se_log_or)
            else:
                ci_lower = None
                ci_upper = None
        else:
            log_or = None
            ci_lower = None
            ci_upper = None

        return {
            'effect_size': float(or_value) if or_value != float('inf') else 'inf',
            'odds_ratio': float(or_value) if or_value != float('inf') else 'inf',
            'log_odds_ratio': float(log_or) if log_or else None,
            'ci': (float(ci_lower) if ci_lower else None, float(ci_upper) if ci_upper else None),
            'a': a, 'b': b, 'c': c, 'd': d,
            'interpretation': self._interpret_odds_ratio(or_value)
        }

    def rank_biserial(
        self,
        group1: Union[List, np.ndarray],
        group2: Union[List, np.ndarray]
    ) -> Dict:
        """
        计算等级二列相关系数r（Mann-Whitney U的效应量）

        参数：
            group1: 第一组数据
            group2: 第二组数据

        返回：
            Dict: 包含效应量和解释的字典
        """
        group1 = np.array(group1)
        group2 = np.array(group2)

        # 去除缺失值
        group1 = group1[~np.isnan(group1)]
        group2 = group2[~np.isnan(group2)]

        n1, n2 = len(group1), len(group2)
        N = n1 + n2

        # Mann-Whitney U
        combined = np.concatenate([group1, group2])
        ranks = stats.rankdata(combined)
        rank1 = np.sum(ranks[:n1])

        # 等级二列相关系数
        # r = (2*U) / (n1*n2) - 1
        # 或等价于 r = (2*mean_rank1) / N - 1 - (n2+1)/N
        # 简化公式：r = 1 - (2*U) / (n1*n2)
        U1 = rank1 - n1 * (n1 + 1) / 2
        r = (2 * U1) / (n1 * n2) - 1

        # 确保在[-1, 1]范围内
        r = max(-1, min(1, r))

        return {
            'effect_size': float(r),
            'rank_biserial_r': float(r),
            'U': float(U1),
            'n1': n1,
            'n2': n2,
            'interpretation': self._interpret_r(r),
            'magnitude': self._get_magnitude(abs(r), self.R_THRESHOLDS)
        }

    def _interpret_cohens_d(self, d: float) -> str:
        """解释Cohen's d"""
        abs_d = abs(d)
        if abs_d < 0.2:
            return f"Cohen's d = {d:.2f}，效应量极小（可忽略）"
        elif abs_d < 0.5:
            return f"Cohen's d = {d:.2f}，效应量小"
        elif abs_d < 0.8:
            return f"Cohen's d = {d:.2f}，效应量中等"
        else:
            return f"Cohen's d = {d:.2f}，效应量大"

    def _interpret_eta_squared(self, eta_sq: float) -> str:
        """解释η²"""
        if eta_sq < 0.01:
            return f"η² = {eta_sq:.4f}，效应量极小"
        elif eta_sq < 0.06:
            return f"η² = {eta_sq:.4f}，效应量小"
        elif eta_sq < 0.14:
            return f"η² = {eta_sq:.4f}，效应量中等"
        else:
            return f"η² = {eta_sq:.4f}，效应量大"

    def _interpret_cramers_v(self, v: float) -> str:
        """解释Cramér's V"""
        if v < 0.1:
            return f"Cramér's V = {v:.4f}，关联极弱"
        elif v < 0.3:
            return f"Cramér's V = {v:.4f}，关联弱"
        elif v < 0.5:
            return f"Cramér's V = {v:.4f}，关联中等"
        else:
            return f"Cramér's V = {v:.4f}，关联强"

    def _interpret_odds_ratio(self, or_value: float) -> str:
        """解释比值比"""
        if or_value == float('inf'):
            return "比值比为无穷大，一组事件率100%，另一组0%"
        elif or_value > 1:
            return f"OR = {or_value:.2f}，组1事件风险是组2的{or_value:.2f}倍"
        elif or_value < 1:
            return f"OR = {or_value:.2f}，组1事件风险是组2的{1/or_value:.2f}分之一"
        else:
            return f"OR = {or_value:.2f}，两组事件风险相同"

    def _interpret_r(self, r: float) -> str:
        """解释相关系数r"""
        abs_r = abs(r)
        if abs_r < 0.1:
            return f"r = {r:.4f}，相关极弱"
        elif abs_r < 0.3:
            return f"r = {r:.4f}，相关弱"
        elif abs_r < 0.5:
            return f"r = {r:.4f}，相关中等"
        else:
            return f"r = {r:.4f}，相关强"

    def _get_magnitude(self, value: float, thresholds: Dict) -> str:
        """根据阈值判断效应量大小"""
        abs_val = abs(value)
        if abs_val < thresholds['small']:
            return 'negligible'
        elif abs_val < thresholds['medium']:
            return 'small'
        elif abs_val < thresholds['large']:
            return 'medium'
        else:
            return 'large'


# 类型提示
from typing import List