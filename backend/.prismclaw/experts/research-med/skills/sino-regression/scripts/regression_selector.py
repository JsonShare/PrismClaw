"""regression_selector.py - 回归方法选择器

根据因变量类型和特征自动选择合适的回归方法。
- 因变量连续 → 线性回归
- 因变量二分类 → 逻辑回归
- 因变量生存数据 → Cox回归
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple, Optional


class RegressionSelector:
    """回归方法选择器"""

    # 回归方法常量
    LINEAR = "linear"
    LOGISTIC = "logistic"
    COX = "cox"
    POISSON = "poisson"

    def __init__(self):
        pass

    def select_regression_method(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        time_var: Optional[str] = None
    ) -> str:
        """根据因变量类型选择回归方法

        参数：
            df: 数据框
            outcome_var: 因变量名称
            time_var: 时间变量名称（用于Cox回归）

        返回：
            回归方法名称
        """
        if outcome_var not in df.columns:
            raise ValueError(f"因变量 {outcome_var} 不在数据中")

        col = df[outcome_var]
        dtype = col.dtype
        unique_values = col.dropna().unique()
        n_unique = len(unique_values)

        # 检查是否是生存数据
        if time_var and time_var in df.columns:
            return self.COX

        # 根据唯一值数量和类型判断
        if n_unique == 2:
            # 二分类变量
            # 检查是否是0/1编码
            unique_sorted = sorted(unique_values)
            if set(unique_sorted).issubset({0, 1}):
                return self.LOGISTIC
            elif set(unique_sorted).issubset({0, 1, 2}):  # 多分类但只有3个值
                return self.LOGISTIC
            else:
                # 可能是二分类但用其他编码
                return self.LOGISTIC
        elif n_unique > 2 and n_unique <= 10:
            # 有序多分类 → 可以用有序logistic（暂不支持，用普通logistic）
            return self.LOGISTIC
        else:
            # 连续变量
            if dtype in ['int64', 'float64']:
                return self.LINEAR
            else:
                return self.LINEAR

    def check_multicollinearity(
        self,
        df: pd.DataFrame,
        predictor_vars: List[str]
    ) -> Dict[str, Any]:
        """检查多重共线性

        使用方差膨胀因子（VIF）进行诊断。
        - VIF > 5: 存在共线性问题
        - VIF > 10: 存在严重共线性问题

        参数：
            df: 数据框
            predictor_vars: 预测变量列表

        返回：
            共线性诊断结果字典
        """
        from sklearn.preprocessing import StandardScaler
        import statsmodels.api as sm

        # 准备数据
        X = df[predictor_vars].copy()

        # 处理缺失值
        X = X.dropna()

        # 添加常数项
        X_const = sm.add_constant(X)

        # 计算VIF
        vif_data = {}
        try:
            from statsmodels.stats.outliers_influence import variance_inflation_factor

            for i, var in enumerate(X.columns):
                vif_i = variance_inflation_factor(X.values, i)
                vif_data[var] = vif_i
        except Exception as e:
            # 如果VIF计算失败，返回警告
            return {
                "success": False,
                "error": str(e),
                "vif_values": {},
                "has_issues": False
            }

        # 判断是否有共线性问题
        has_issues = any(v > 5 for v in vif_data.values())
        has_severe_issues = any(v > 10 for v in vif_data.values())

        return {
            "success": True,
            "vif_values": vif_data,
            "has_issues": has_issues,
            "has_severe_issues": has_severe_issues,
            "warnings": self._generate_vif_warnings(vif_data)
        }

    def _generate_vif_warnings(self, vif_values: Dict[str, float]) -> List[str]:
        """生成VIF警告信息"""
        warnings = []

        for var, vif in vif_values.items():
            if vif > 10:
                warnings.append(f"严重警告：{var}的VIF={vif:.1f}，存在严重共线性，建议剔除或合并")
            elif vif > 5:
                warnings.append(f"警告：{var}的VIF={vif:.1f}，存在中等共线性问题")

        return warnings

    def recommend_preprocessing(
        self,
        df: pd.DataFrame,
        predictor_vars: List[str]
    ) -> Dict[str, Any]:
        """推荐变量预处理方法

        参数：
            df: 数据框
            predictor_vars: 预测变量列表

        返回：
            预处理建议字典
        """
        recommendations = {
            "standardization": [],  # 需要标准化的变量
            "dummy_encoding": [],   # 需要哑变量编码的变量
            "transformations": {},  # 需要变换的变量
        }

        for var in predictor_vars:
            if var not in df.columns:
                continue

            col = df[var]
            dtype = col.dtype

            # 检测数据类型
            if dtype == 'object' or dtype.name == 'category':
                # 分类变量需要哑变量编码
                n_categories = col.nunique()
                if n_categories > 2:
                    recommendations["dummy_encoding"].append({
                        "variable": var,
                        "n_categories": n_categories,
                        "recommendation": f"需要{n_categories}个哑变量"
                    })
                else:
                    recommendations["dummy_encoding"].append({
                        "variable": var,
                        "n_categories": n_categories,
                        "recommendation": "直接编码为0/1"
                    })
            elif dtype in ['int64', 'float64']:
                # 数值变量
                # 检查是否需要标准化（量纲差异大时）
                if col.std() > 10 * col.abs().mean():
                    recommendations["standardization"].append(var)

                # 检查偏度
                skewness = col.skew()
                if abs(skewness) > 2:
                    if skewness > 0:
                        recommendations["transformations"][var] = "对数变换或Box-Cox"
                    else:
                        recommendations["transformations"][var] = "可能需要逆向变换"

        return recommendations

    def create_model_spec(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str],
        method: str = LINEAR
    ) -> Dict[str, Any]:
        """创建模型规格说明

        参数：
            df: 数据框
            outcome_var: 因变量名称
            predictor_vars: 预测变量列表
            method: 回归方法

        返回：
            模型规格字典
        """
        spec = {
            "method": method,
            "outcome_var": outcome_var,
            "predictor_vars": predictor_vars,
            "n_total": len(df),
            "n_predictors": len(predictor_vars),
            "preprocessing": self.recommend_preprocessing(df, predictor_vars)
        }

        # 添加方法特定的规格
        if method == self.LINEAR:
            spec["family"] = "Gaussian"
            spec["link"] = "identity"
        elif method == self.LOGISTIC:
            spec["family"] = "Binomial"
            spec["link"] = "logit"
            # 计算事件率
            if outcome_var in df.columns:
                event_rate = df[outcome_var].mean()
                spec["event_rate"] = event_rate
                spec["n_events"] = int(event_rate * len(df))
        elif method == self.COX:
            spec["family"] = "Cox"
            # 需要time_var和event_var

        return spec
