"""univariate_screen.py - 单因素筛选

对每个自变量进行单因素回归分析，筛选出p<阈值的变量进入多因素模型。
标准流行病学流程：p<0.1进入多因素模型。
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any
import statsmodels.api as sm


class UnivariateScreen:
    """单因素筛选器"""

    def __init__(self, regression_type: str = "linear"):
        self.regression_type = regression_type

    def screen(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str],
        p_threshold: float = 0.1
    ) -> Dict[str, Dict[str, Any]]:
        """对每个预测变量进行单因素筛选

        参数：
            df: 数据框
            outcome_var: 因变量名称
            predictor_vars: 预测变量列表
            p_threshold: 进入阈值

        返回：
            字典：{变量名: {beta, se, p_value, ci, r_squared}}
        """
        results = {}
        y = df[outcome_var]

        for var in predictor_vars:
            try:
                X = df[[var]].copy()

                # 处理缺失值
                mask = ~(X[var].isna() | y.isna())
                X_clean = X[mask]
                y_clean = y[mask]

                if len(X_clean) < 10:
                    results[var] = self._create_empty_result(f"样本量不足: {len(X_clean)}")
                    continue

                # 添加常数项
                X_const = sm.add_constant(X_clean)

                # 拟合模型
                if self.regression_type == "linear":
                    model = sm.OLS(y_clean, X_const).fit()
                elif self.regression_type == "logistic":
                    model = sm.Logit(y_clean, X_const).fit(disp=0)
                else:
                    model = sm.OLS(y_clean, X_const).fit()

                # 提取结果
                result = self._extract_results(model, var, X_const)
                results[var] = result

            except Exception as e:
                results[var] = self._create_empty_result(f"模型拟合失败: {str(e)}")

        return results

    def _extract_results(self, model, var: str, X) -> Dict[str, Any]:
        """从模型中提取结果"""
        if self.regression_type == "linear":
            # OLS回归
            params = model.params
            conf_int = model.conf_int()

            beta = params.get(var, 0)
            se = model.bse.get(var, 0)
            t_stat = model.tvalues.get(var, 0)
            p_value = model.pvalues.get(var, 1)
            ci_low = conf_int.loc[var, 0] if var in conf_int.index else 0
            ci_high = conf_int.loc[var, 1] if var in conf_int.index else 0
            r_squared = model.rsquared

            return {
                "beta": float(beta),
                "se": float(se),
                "t": float(t_stat),
                "p_value": float(p_value),
                "ci": [float(ci_low), float(ci_high)],
                "r_squared": float(r_squared),
                "n": int(model.nobs)
            }
        else:
            # Logistic回归
            params = model.params
            conf_int = model.conf_int()

            beta = params.get(var, 0)
            or_value = np.exp(beta)
            se = model.bse.get(var, 0)
            z_stat = model.tvalues.get(var, 0)  # logistic用z
            p_value = model.pvalues.get(var, 1)
            ci_low = np.exp(conf_int.loc[var, 0]) if var in conf_int.index else 0
            ci_high = np.exp(conf_int.loc[var, 1]) if var in conf_int.index else 0
            pseudo_r2 = model.prsquared

            return {
                "beta": float(beta),
                "or_value": float(or_value),
                "se": float(se),
                "z": float(z_stat),
                "p_value": float(p_value),
                "ci": [float(ci_low), float(ci_high)],
                "pseudo_r2": float(pseudo_r2) if hasattr(model, 'prsquared') else 0,
                "n": int(model.nobs)
            }

    def _create_empty_result(self, error: str) -> Dict[str, Any]:
        """创建空结果（用于错误情况）"""
        return {
            "beta": 0,
            "se": 0,
            "p_value": 1,
            "ci": [0, 0],
            "r_squared": 0,
            "error": error
        }
