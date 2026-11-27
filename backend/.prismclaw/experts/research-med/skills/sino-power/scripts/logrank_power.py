#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Log-rank检验样本量计算模块

基于HR、accrual time、follow-up time计算所需样本量
"""
from typing import Dict, Optional
import numpy as np
from scipy import stats


class LogRankPowerCalculator:
    """
    Log-rank检验样本量计算器

    基于指数分布假设和Cox比例风险模型
    """

    def __init__(
        self,
        alpha: float = 0.05,
        power: float = 0.8,
        effect_size: float = 0.7,
        design_type: str = 'superiority',
        expected_event_rate: Optional[float] = None,
        accrual_time: Optional[float] = None,
        follow_up_time: Optional[float] = None
    ):
        """
        初始化Log-rank检验样本量计算器

        参数：
            alpha: 显著性水平（双侧）
            power: 统计效能（1-β）
            effect_size: 风险比HR（新疗法/对照疗法）
            design_type: 设计类型
            expected_event_rate: 预期事件率（总研究期间的事件发生比例）
            accrual_time: 入组时间（年）
            follow_up_time: 随访时间（年）
        """
        self.alpha = alpha
        self.power = power
        self.effect_size = effect_size  # HR = h1/h2, 通常HR < 1 表示新疗法更好
        self.design_type = design_type

        # 研究时间参数
        self.expected_event_rate = expected_event_rate
        self.accrual_time = accrual_time
        self.follow_up_time = follow_up_time

        # 计算Z值
        self.z_alpha = stats.norm.ppf(1 - alpha / 2)
        self.z_beta = stats.norm.ppf(power)

    def calculate(self) -> Dict:
        """
        计算Log-rank检验所需的样本量

        返回：
            Dict: 包含样本量计算结果的字典
        """
        hr = self.effect_size

        # Log-rank检验样本量公式（基于指数分布）
        # n = 4 * (z_α + z_β)² / (log(HR))² * (r+1)/r
        # 其中 r = n1/n2 为分组比例

        # 简化公式（假设1:1分组）
        # 需要的事件数 D = 4 * (z_α + z_β)² / (log(HR))²

        log_hr = np.log(hr) if hr > 0 else 0

        # 避免log(0)或log(负数)
        if abs(log_hr) < 0.001:
            return {
                'test_method': 'logrank',
                'error': 'HR=1表示两组无差异，无法计算样本量',
                'effect_size': hr,
                'effect_interpretation': 'HR = 1，两组无差异'
            }

        # 所需事件数
        d = 4 * (self.z_alpha + self.z_beta) ** 2 / (log_hr ** 2)

        # 事件率
        if self.expected_event_rate:
            event_rate = self.expected_event_rate
        else:
            # 假设合理的默认事件率
            event_rate = 0.5  # 50%事件率作为默认值

        # 总样本量 = 事件数 / 事件率
        total_n = int(np.ceil(d / event_rate))

        # 效应量解释
        effect_interpretation = self._interpret_hr(hr)

        # 研究时间说明
        time_note = ""
        if self.accrual_time and self.follow_up_time:
            total_study_time = self.accrual_time + self.follow_up_time
            time_note = f"入组{self.accrual_time}年 + 随访{self.follow_up_time}年 = 总研究时间{total_study_time}年"
        elif self.follow_up_time:
            time_note = f"随访时间{self.follow_up_time}年"
        else:
            time_note = "研究时间未指定"

        return {
            'test_method': 'logrank',
            'design_type': self.design_type,
            'alpha': self.alpha,
            'power': self.power,
            'effect_size': hr,
            'effect_interpretation': effect_interpretation,
            'log_hr': float(log_hr),
            'required_events': int(np.ceil(d)),
            'expected_event_rate': event_rate,
            'total_n': total_n,
            'n_per_group': int(np.ceil(total_n / 2)),
            'accrual_time': self.accrual_time,
            'follow_up_time': self.follow_up_time,
            'time_note': time_note,
            'z_alpha': float(self.z_alpha),
            'z_beta': float(self.z_beta),
            'formula': 'D = 4 * (z_α/2 + z_β)² / (log(HR))²',
            'note': '假设指数分布和1:1分组'
        }

    def _interpret_hr(self, hr: float) -> str:
        """
        解释风险比HR

        参数：
            hr: 风险比

        返回：
            str: HR解释
        """
        if hr >= 0.9:
            magnitude = "极小改善"
        elif hr >= 0.75:
            magnitude = "小改善"
        elif hr >= 0.5:
            magnitude = "中等改善"
        elif hr >= 0.33:
            magnitude = "大改善"
        else:
            magnitude = "极大改善"

        risk_reduction = (1 - hr) * 100

        if hr < 1:
            return f"HR = {hr:.2f}，新疗法风险降低{risk_reduction:.0f}%，效应{magnitude}"
        elif hr > 1:
            risk_increase = (hr - 1) * 100
            return f"HR = {hr:.2f}，新疗法风险增加{risk_increase:.0f}%"
        else:
            return f"HR = {hr:.2f}，两组风险相同"

    def calculate_with_time(
        self,
        accrual_time: float,
        follow_up_time: float,
        hazard_rate1: float,
        hazard_rate2: float,
        dropout_rate: float = 0
    ) -> Dict:
        """
        使用具体时间参数计算样本量

        参数：
            accrual_time: 入组时间（年）
            follow_up_time: 随访时间（年）
            hazard_rate1: 第一组风险率（每年事件率）
            hazard_rate2: 第二组风险率
            dropout_rate: 年失访率

        返回：
            Dict: 包含样本量计算结果的字典
        """
        self.accrual_time = accrual_time
        self.follow_up_time = follow_up_time

        # HR
        hr = hazard_rate1 / hazard_rate2 if hazard_rate2 > 0 else 1
        self.effect_size = hr

        # 总研究时间
        total_time = accrual_time + follow_up_time

        # 平均随访时间
        # 对于均匀入组，平均随访时间 = follow_up_time + accrual_time/2
        mean_follow_up = follow_up_time + accrual_time / 2

        # 每组预期事件数
        # D1 = n1 * hazard_rate1 * mean_follow_up
        # D2 = n2 * hazard_rate2 * mean_follow_up

        # 总事件数 D = D1 + D2 = (n1 + n2) * (h1 + h2)/2 * mean_follow_up

        # 样本量公式
        log_hr = np.log(hr) if hr > 0 else 0

        if abs(log_hr) < 0.001:
            return {
                'error': '两组风险相同，无法计算样本量'
            }

        # 所需总事件数
        d = 4 * (self.z_alpha + self.z_beta) ** 2 / (log_hr ** 2)

        # 平均风险率
        mean_hazard = (hazard_rate1 + hazard_rate2) / 2

        # 总样本量
        total_n = d / (mean_hazard * mean_follow_up * (1 - dropout_rate))

        total_n = int(np.ceil(total_n))

        return {
            'test_method': 'logrank',
            'hr': hr,
            'required_events': int(np.ceil(d)),
            'total_n': total_n,
            'n_per_group': int(np.ceil(total_n / 2)),
            'accrual_time': accrual_time,
            'follow_up_time': follow_up_time,
            'mean_follow_up': mean_follow_up,
            'mean_hazard': mean_hazard,
            'dropout_rate': dropout_rate
        }