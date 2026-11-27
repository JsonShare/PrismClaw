"""multivariate_model.py - 多因素建模

将筛选出的变量纳入多元回归模型，支持：
- 逐步前向筛选
- 全变量纳入
- 强制纳入协变量
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import statsmodels.api as sm


class MultivariateModelBuilder:
    """多因素模型构建器"""

    def __init__(self, regression_type: str = "linear"):
        self.regression_type = regression_type

    def build(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str],
        selection_method: str = "stepwise_forward",
        p_enter: float = 0.1,
        p_remove: float = 0.2,
        forced_covariates: List[str] = None
    ) -> Dict[str, Any]:
        """构建多因素回归模型

        参数：
            df: 数据框
            outcome_var: 因变量名称
            predictor_vars: 预测变量列表
            selection_method: 筛选方法
            p_enter: 进入阈值
            p_remove: 移除阈值
            forced_covariates: 强制纳入的协变量

        返回：
            模型结果字典
        """
        if forced_covariates is None:
            forced_covariates = []

        y = df[outcome_var]

        if len(predictor_vars) == 0:
            return {
                "model": None,
                "predictors": [],
                "r_squared": 0,
                "adjusted_r_squared": 0,
                "aic": 0,
                "bic": 0,
                "f_stat": 0,
                "f_df": 0,
                "f_pvalue": 1
            }

        X = df[predictor_vars].copy()

        # 处理缺失值
        mask = ~(X.isna().any(axis=1) | y.isna())
        X_clean = X[mask]
        y_clean = y[mask]

        # 添加常数项
        X_const = sm.add_constant(X_clean)

        # 根据筛选方法构建模型
        if selection_method == "stepwise_forward":
            return self._stepwise_forward(
                X=X_const,
                y=y_clean,
                predictor_vars=predictor_vars,
                forced_covariates=forced_covariates,
                p_enter=p_enter,
                p_remove=p_remove
            )
        elif selection_method == "enter":
            return self._enter_all(
                X=X_const,
                y=y_clean,
                predictor_vars=predictor_vars
            )
        elif selection_method == "backward":
            return self._backward_elimination(
                X=X_const,
                y=y_clean,
                predictor_vars=predictor_vars,
                p_remove=p_remove
            )
        else:
            # 默认使用enter_all
            return self._enter_all(
                X=X_const,
                y=y_clean,
                predictor_vars=predictor_vars
            )

    def _stepwise_forward(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        predictor_vars: List[str],
        forced_covariates: List[str],
        p_enter: float,
        p_remove: float
    ) -> Dict[str, Any]:
        """逐步前向筛选"""
        # 分离协变量和候选变量
        candidate_vars = [v for v in predictor_vars if v not in forced_covariates]
        all_vars = forced_covariates + candidate_vars

        # 记录筛选步骤
        steps = []

        # 第一步：将协变量强制纳入
        current_vars = forced_covariates.copy()

        if len(current_vars) > 0:
            X_current = X[forced_covariates + ["const"]]
            if self.regression_type == "linear":
                model = sm.OLS(y, X_current).fit()
            else:
                model = sm.Logit(y, X_current).fit(disp=0)

            for var in forced_covariates:
                steps.append({
                    "step": len(steps) + 1,
                    "action": "forced_enter",
                    "variable": var,
                    "p_value": model.pvalues.get(var, 0),
                    "aic": model.aic
                })

        # 前向筛选
        remaining_vars = [v for v in candidate_vars if v not in current_vars]

        while len(remaining_vars) > 0:
            best_var = None
            best_p = 1.0

            # 尝试加入每个剩余变量
            for var in remaining_vars:
                test_vars = current_vars + [var]
                X_test = X[test_vars + ["const"]]

                if self.regression_type == "linear":
                    model = sm.OLS(y, X_test).fit()
                else:
                    model = sm.Logit(y, X_test).fit(disp=0)

                p_value = model.pvalues.get(var, 1)

                if p_value < best_p:
                    best_p = p_value
                    best_var = var
                    best_model = model

            # 如果最佳变量的p值小于进入阈值，加入模型
            if best_var and best_p < p_enter:
                current_vars.append(best_var)
                remaining_vars.remove(best_var)

                steps.append({
                    "step": len(steps) + 1,
                    "action": "forward_enter",
                    "variable": best_var,
                    "p_value": best_p,
                    "aic": best_model.aic,
                    "in_model": True
                })
            else:
                # 没有变量满足进入条件，停止筛选
                break

        # 用最终变量集拟合模型
        X_final = X[current_vars + ["const"]]
        if self.regression_type == "linear":
            final_model = sm.OLS(y, X_final).fit()
        else:
            final_model = sm.Logit(y, X_final).fit(disp=0)

        # 构建结果
        predictors = []
        for var in current_vars:
            if var == "const":
                continue

            if self.regression_type == "linear":
                predictors.append({
                    "variable": var,
                    "beta": float(final_model.params.get(var, 0)),
                    "se": float(final_model.bse.get(var, 0)),
                    "t": float(final_model.tvalues.get(var, 0)),
                    "p_value": float(final_model.pvalues.get(var, 1)),
                    "ci": [
                        float(final_model.conf_int().loc[var, 0]) if var in final_model.conf_int().index else 0,
                        float(final_model.conf_int().loc[var, 1]) if var in final_model.conf_int().index else 0
                    ]
                })
            else:
                beta = final_model.params.get(var, 0)
                predictors.append({
                    "variable": var,
                    "beta": float(beta),
                    "or_value": float(np.exp(beta)),
                    "se": float(final_model.bse.get(var, 0)),
                    "z": float(final_model.tvalues.get(var, 0)),
                    "p_value": float(final_model.pvalues.get(var, 1)),
                    "ci": [
                        float(np.exp(final_model.conf_int().loc[var, 0])) if var in final_model.conf_int().index else 0,
                        float(np.exp(final_model.conf_int().loc[var, 1])) if var in final_model.conf_int().index else 0
                    ]
                })

        return {
            "model": final_model,
            "predictors": predictors,
            "r_squared": float(final_model.rsquared) if self.regression_type == "linear" else 0,
            "adjusted_r_squared": float(final_model.rsquared_adj) if self.regression_type == "linear" else 0,
            "aic": float(final_model.aic),
            "bic": float(final_model.bic),
            "f_stat": float(final_model.fvalue) if self.regression_type == "linear" else 0,
            "f_df": int(final_model.df_model),  # 近似
            "f_pvalue": float(final_model.f_pvalue) if self.regression_type == "linear" else 0,
            "selection_steps": steps,
            "n_in_model": len(current_vars)
        }

    def _enter_all(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        predictor_vars: List[str]
    ) -> Dict[str, Any]:
        """全部进入模型"""
        # 移除const（会重复添加）
        vars_to_use = [v for v in predictor_vars if v != "const"]
        X_use = X[vars_to_use + ["const"]]

        if self.regression_type == "linear":
            model = sm.OLS(y, X_use).fit()
        else:
            model = sm.Logit(y, X_use).fit(disp=0)

        # 构建结果
        predictors = []
        for var in vars_to_use:
            if self.regression_type == "linear":
                predictors.append({
                    "variable": var,
                    "beta": float(model.params.get(var, 0)),
                    "se": float(model.bse.get(var, 0)),
                    "t": float(model.tvalues.get(var, 0)),
                    "p_value": float(model.pvalues.get(var, 1)),
                    "ci": [
                        float(model.conf_int().loc[var, 0]) if var in model.conf_int().index else 0,
                        float(model.conf_int().loc[var, 1]) if var in model.conf_int().index else 0
                    ]
                })
            else:
                beta = model.params.get(var, 0)
                predictors.append({
                    "variable": var,
                    "beta": float(beta),
                    "or_value": float(np.exp(beta)),
                    "se": float(model.bse.get(var, 0)),
                    "z": float(model.tvalues.get(var, 0)),
                    "p_value": float(model.pvalues.get(var, 1)),
                    "ci": [
                        float(np.exp(model.conf_int().loc[var, 0])) if var in model.conf_int().index else 0,
                        float(np.exp(model.conf_int().loc[var, 1])) if var in model.conf_int().index else 0
                    ]
                })

        return {
            "model": model,
            "predictors": predictors,
            "r_squared": float(model.rsquared) if self.regression_type == "linear" else 0,
            "adjusted_r_squared": float(model.rsquared_adj) if self.regression_type == "linear" else 0,
            "aic": float(model.aic),
            "bic": float(model.bic),
            "f_stat": float(model.fvalue) if self.regression_type == "linear" else 0,
            "f_df": int(model.df_model),
            "f_pvalue": float(model.f_pvalue) if self.regression_type == "linear" else 0,
            "selection_steps": [],
            "n_in_model": len(vars_to_use)
        }

    def _backward_elimination(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        predictor_vars: List[str],
        p_remove: float
    ) -> Dict[str, Any]:
        """后向消除法"""
        # 先用所有变量拟合
        vars_to_use = [v for v in predictor_vars if v != "const"]
        X_use = X[vars_to_use + ["const"]]

        if self.regression_type == "linear":
            model = sm.OLS(y, X_use).fit()
        else:
            model = sm.Logit(y, X_use).fit(disp=0)

        # 逐步移除p值最大的变量
        steps = []
        current_vars = vars_to_use.copy()

        while len(current_vars) > 0:
            # 找出p值最大的变量
            p_values = {var: model.pvalues.get(var, 1) for var in current_vars}
            max_var = max(p_values, key=p_values.get)
            max_p = p_values[max_var]

            if max_p > p_remove:
                # 移除该变量
                current_vars.remove(max_var)

                if len(current_vars) == 0:
                    break

                X_current = X[current_vars + ["const"]]
                if self.regression_type == "linear":
                    model = sm.OLS(y, X_current).fit()
                else:
                    model = sm.Logit(y, X_current).fit(disp=0)

                steps.append({
                    "step": len(steps) + 1,
                    "action": "backward_remove",
                    "variable": max_var,
                    "p_value": max_p,
                    "aic": model.aic
                })
            else:
                break

        # 构建最终结果
        predictors = []
        for var in current_vars:
            if self.regression_type == "linear":
                predictors.append({
                    "variable": var,
                    "beta": float(model.params.get(var, 0)),
                    "se": float(model.bse.get(var, 0)),
                    "t": float(model.tvalues.get(var, 0)),
                    "p_value": float(model.pvalues.get(var, 1)),
                    "ci": [
                        float(model.conf_int().loc[var, 0]) if var in model.conf_int().index else 0,
                        float(model.conf_int().loc[var, 1]) if var in model.conf_int().index else 0
                    ]
                })
            else:
                beta = model.params.get(var, 0)
                predictors.append({
                    "variable": var,
                    "beta": float(beta),
                    "or_value": float(np.exp(beta)),
                    "se": float(model.bse.get(var, 0)),
                    "z": float(model.tvalues.get(var, 0)),
                    "p_value": float(model.pvalues.get(var, 1)),
                    "ci": [
                        float(np.exp(model.conf_int().loc[var, 0])) if var in model.conf_int().index else 0,
                        float(np.exp(model.conf_int().loc[var, 1])) if var in model.conf_int().index else 0
                    ]
                })

        return {
            "model": model,
            "predictors": predictors,
            "r_squared": float(model.rsquared) if self.regression_type == "linear" else 0,
            "adjusted_r_squared": float(model.rsquared_adj) if self.regression_type == "linear" else 0,
            "aic": float(model.aic),
            "bic": float(model.bic),
            "f_stat": float(model.fvalue) if self.regression_type == "linear" else 0,
            "f_df": int(model.df_model),
            "f_pvalue": float(model.f_pvalue) if self.regression_type == "linear" else 0,
            "selection_steps": steps,
            "n_in_model": len(current_vars)
        }
