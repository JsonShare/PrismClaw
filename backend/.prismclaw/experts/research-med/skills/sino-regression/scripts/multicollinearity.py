"""multicollinearity.py - 多重共线性诊断

使用方差膨胀因子（VIF）进行共线性诊断：
- VIF > 5: 存在共线性问题
- VIF > 10: 存在严重共线性问题
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor


class MulticollinearityChecker:
    """多重共线性诊断器"""

    def __init__(self):
        pass

    def check(
        self,
        df: pd.DataFrame,
        predictor_vars: List[str],
        outcome_var: str = None
    ) -> Dict[str, Any]:
        """检查多重共线性

        参数：
            df: 数据框
            predictor_vars: 预测变量列表
            outcome_var: 因变量（可选）

        返回：
            共线性诊断结果
        """
        if len(predictor_vars) == 0:
            return {
                "vif_values": {},
                "has_issues": False,
                "warnings": []
            }

        # 准备数据
        X = df[predictor_vars].copy()

        # 移除缺失值
        X = X.dropna()

        if len(X) < 10:
            return {
                "vif_values": {},
                "has_issues": False,
                "error": f"样本量不足: {len(X)}"
            }

        # 添加常数项（用于截距）
        try:
            X_const = sm.add_constant(X)
        except:
            X_const = X

        # 计算VIF
        vif_values = {}
        warnings = []

        try:
            # 对每个变量计算VIF
            for i, var in enumerate(X.columns):
                vif_i = variance_inflation_factor(X.values, i)
                vif_values[var] = float(vif_i)

                # 生成警告
                if vif_i > 10:
                    warnings.append(f"严重警告: {var}的VIF={vif_i:.1f}，存在严重共线性，建议剔除或合并")
                elif vif_i > 5:
                    warnings.append(f"警告: {var}的VIF={vif_i:.1f}，存在中等共线性问题")

        except Exception as e:
            return {
                "vif_values": {},
                "has_issues": False,
                "error": f"VIF计算失败: {str(e)}"
            }

        # 判断整体情况
        has_issues = any(v > 5 for v in vif_values.values())
        has_severe_issues = any(v > 10 for v in vif_values.values())

        return {
            "vif_values": vif_values,
            "has_issues": has_issues,
            "has_severe_issues": has_severe_issues,
            "warnings": warnings,
            "max_vif": max(vif_values.values()) if vif_values else 0,
            "mean_vif": np.mean(list(vif_values.values())) if vif_values else 0
        }

    def get_condition_index(self, df: pd.DataFrame, predictor_vars: List[str]) -> Dict[str, Any]:
        """计算条件指数（Condition Index）作为辅助诊断

        参数索引>30表示存在共线性问题。
        """
        X = df[predictor_vars].copy()
        X = X.dropna()

        # 标准化
        X_std = (X - X.mean()) / X.std()

        # SVD分解
        try:
            U, s, V = np.linalg.svd(X_std)
            condition_indices = s.max() / s
            max_ci = condition_indices.max()

            return {
                "condition_indices": condition_indices.tolist(),
                "max_condition_index": float(max_ci),
                "has_issues": max_ci > 30
            }
        except:
            return {
                "error": "条件指数计算失败"
            }

    def recommend_remedies(self, vif_values: Dict[str, float]) -> List[str]:
        """推荐共线性处理方法"""
        recommendations = []

        high_vif_vars = {v: vif for v, vif in vif_values.items() if vif > 5}

        if not high_vif_vars:
            recommendations.append("所有变量VIF均正常，无需处理")
            return recommendations

        # 找出VIF最高的变量
        sorted_vars = sorted(high_vif_vars.items(), key=lambda x: x[1], reverse=True)

        for var, vif in sorted_vars:
            if vif > 10:
                recommendations.append(f"{var}(VIF={vif:.1f}): 建议剔除该变量，或使用主成分回归")
            elif vif > 5:
                recommendations.append(f"{var}(VIF={vif:.1f}): 建议合并相关变量，或使用岭回归")

        recommendations.append("替代方案: 使用正则化方法（岭回归、Lasso回归）")

        return recommendations
