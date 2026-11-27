"""linear_regression.py - 线性回归实现

支持：
- 单变量/多变量线性回归
- 变量筛选（逐步法/AIC/BIC）
- 残差分析
- 多重共线性诊断（VIF）
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple, Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from univariate_screen import UnivariateScreen
from multivariate_model import MultivariateModelBuilder
from multicollinearity import MulticollinearityChecker
from model_diagnostics import ModelDiagnostics
from variable_processor import VariableProcessor


class LinearRegressionAnalyzer:
    """线性回归分析器"""

    def __init__(self):
        self.univariate_screen = UnivariateScreen(regression_type="linear")
        self.multivariate_builder = MultivariateModelBuilder(regression_type="linear")
        self.multicollinearity_checker = MulticollinearityChecker()
        self.model_diagnostics = ModelDiagnostics()
        self.variable_processor = VariableProcessor()

    def analyze(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str],
        forced_covariates: List[str] = None,
        selection_method: str = "stepwise_forward",
        p_enter: float = 0.1,
        p_remove: float = 0.2,
        check_multicollinearity: bool = True
    ) -> Dict[str, Any]:
        """执行完整的线性回归分析

        参数：
            df: 数据框
            outcome_var: 因变量名称
            predictor_vars: 预测变量列表
            forced_covariates: 强制纳入的协变量（不受筛选影响）
            selection_method: 变量筛选方法
            p_enter: 进入模型的p值阈值
            p_remove: 移除模型的p值阈值
            check_multicollinearity: 是否检查共线性

        返回：
            完整的回归分析结果字典
        """
        if forced_covariates is None:
            forced_covariates = []

        print(f"[linear_regression] 开始线性回归分析...")
        print(f"[linear_regression] 因变量: {outcome_var}")
        print(f"[linear_regression] 候选预测变量: {predictor_vars}")
        print(f"[linear_regression] 强制协变量: {forced_covariates}")

        # 步骤1：变量预处理
        df_processed, var_info = self.variable_processor.process_variables(
            df=df,
            outcome_var=outcome_var,
            predictor_vars=predictor_vars + forced_covariates
        )

        # 移除缺失值
        all_vars = [outcome_var] + predictor_vars + forced_covariates
        df_clean = df_processed[all_vars].dropna()
        print(f"[linear_regression] 完整数据: {len(df_clean)}/{len(df)} 行")

        if len(df_clean) < len(all_vars) * 5:
            print(f"[linear_regression] 警告: 样本量较小，可能影响结果可靠性")

        # 步骤2：单因素筛选
        print(f"[linear_regression] 执行单因素筛选（p<{p_enter}进入）...")
        univariate_results = self.univariate_screen.screen(
            df=df_clean,
            outcome_var=outcome_var,
            predictor_vars=predictor_vars,
            p_threshold=p_enter
        )

        # 分离显著和不显著的变量
        significant_vars = [v for v, r in univariate_results.items() if r["p_value"] < p_enter]
        not_significant_vars = [v for v, r in univariate_results.items() if r["p_value"] >= p_enter]

        print(f"[linear_regression] 单因素筛选结果:")
        print(f"[linear_regression]   显著变量({len(significant_vars)}): {significant_vars}")
        print(f"[linear_regression]   非显著变量({len(not_significant_vars)}): {not_significant_vars}")

        # 强制纳入协变量（去除重复）
        final_vars = list(set(significant_vars + forced_covariates))
        selection_steps = []

        # 记录筛选步骤
        for var, result in univariate_results.items():
            selection_steps.append({
                "step": len(selection_steps) + 1,
                "variable": var,
                "screened_out": result["p_value"] >= p_enter,
                "reason": "p>=阈值" if result["p_value"] >= p_enter else "进入多因素",
                "beta": result["beta"],
                "p_value": result["p_value"],
                "r_squared": result.get("r_squared", 0)
            })

        # 步骤3：共线性检查
        multicollinearity_result = {"vif_values": {}, "has_issues": False}
        if check_multicollinearity and len(final_vars) > 0:
            print(f"[linear_regression] 检查共线性...")
            multicollinearity_result = self.multicollinearity_checker.check(
                df=df_clean,
                predictor_vars=final_vars,
                outcome_var=outcome_var
            )

            if multicollinearity_result["has_issues"]:
                print(f"[linear_regression] 警告: 存在共线性问题")
                for warning in multicollinearity_result.get("warnings", []):
                    print(f"[linear_regression]   - {warning}")

        # 步骤4：多因素建模
        print(f"[linear_regression] 执行多因素建模...")
        multivariate_result = self.multivariate_builder.build(
            df=df_clean,
            outcome_var=outcome_var,
            predictor_vars=final_vars,
            selection_method=selection_method,
            p_enter=p_enter,
            p_remove=p_remove,
            forced_covariates=forced_covariates
        )

        # 步骤5：模型诊断
        print(f"[linear_regression] 执行模型诊断...")
        X_final = df_clean[final_vars] if final_vars else pd.DataFrame()
        y_final = df_clean[outcome_var]
        diagnostics = self.model_diagnostics.diagnose(
            y=y_final,
            X=X_final,
            model=multivariate_result.get("model")
        )

        # 步骤6：整理最终结果
        final_predictors = multivariate_result.get("predictors", [])

        # 构建结果字典
        results = {
            "method": "多因素线性回归",
            "outcome_var": outcome_var,
            "n": len(df_clean),
            "predictors": [],
            "model_fit": {
                "r_squared": multivariate_result.get("r_squared", 0),
                "adjusted_r_squared": multivariate_result.get("adjusted_r_squared", 0),
                "f_stat": multivariate_result.get("f_stat", 0),
                "f_df": multivariate_result.get("f_df", 0),
                "f_pvalue": multivariate_result.get("f_pvalue", 1)
            },
            "univariate_results": univariate_results,
            "selection_steps": selection_steps,
            "multicollinearity": multicollinearity_result,
            "model_diagnostics": diagnostics,
            "variable_processing": var_info
        }

        # 添加预测变量结果
        for pred in final_predictors:
            var_name = pred.get("variable", "")
            beta = pred.get("beta", 0)
            se = pred.get("se", 0)
            t_stat = pred.get("t", 0)
            p_value = pred.get("p_value", 1)
            ci = pred.get("ci", [0, 0])
            std_beta = pred.get("std_beta", 0)
            vif = multicollinearity_result.get("vif_values", {}).get(var_name, 1)

            results["predictors"].append({
                "variable": var_name,
                "beta": beta,
                "se": se,
                "t": t_stat,
                "p_value": p_value,
                "ci": ci,
                "std_beta": std_beta,
                "vif": vif
            })

        # 按p值排序
        results["predictors"] = sorted(
            results["predictors"],
            key=lambda x: x["p_value"]
        )

        # 添加显著影响因素
        results["significant_predictors"] = [
            p for p in results["predictors"] if p["p_value"] < 0.05
        ]

        # 添加AIC/BIC
        results["model_fit"]["aic"] = multivariate_result.get("aic", 0)
        results["model_fit"]["bic"] = multivariate_result.get("bic", 0)

        print(f"[linear_regression] 分析完成!")
        print(f"[linear_regression] 最终模型包含 {len(results['predictors'])} 个预测变量")
        print(f"[linear_regression] R² = {results['model_fit']['r_squared']:.3f}")
        print(f"[linear_regression] 调整R² = {results['model_fit']['adjusted_r_squared']:.3f}")

        return results
