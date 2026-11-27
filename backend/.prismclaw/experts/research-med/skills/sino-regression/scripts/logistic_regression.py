"""logistic_regression.py - 逻辑回归实现

支持：
- 单变量/多变量逻辑回归
- OR值和95%置信区间
- Hosmer-Lemeshow拟合度检验
- ROC曲线和AUC
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from univariate_screen import UnivariateScreen
from multivariate_model import MultivariateModelBuilder
from variable_processor import VariableProcessor


class LogisticRegressionAnalyzer:
    """逻辑回归分析器"""

    def __init__(self):
        self.univariate_screen = UnivariateScreen(regression_type="logistic")
        self.multivariate_builder = MultivariateModelBuilder(regression_type="logistic")
        self.variable_processor = VariableProcessor()

    def analyze(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str],
        forced_covariates: List[str] = None,
        selection_method: str = "stepwise_forward",
        p_enter: float = 0.1,
        p_remove: float = 0.2
    ) -> Dict[str, Any]:
        """执行完整的逻辑回归分析

        参数：
            df: 数据框
            outcome_var: 因变量名称
            predictor_vars: 预测变量列表
            forced_covariates: 强制纳入的协变量
            selection_method: 变量筛选方法
            p_enter: 进入阈值
            p_remove: 移除阈值

        返回：
            完整的逻辑回归分析结果字典
        """
        if forced_covariates is None:
            forced_covariates = []

        print(f"[logistic_regression] 开始逻辑回归分析...")
        print(f"[logistic_regression] 因变量: {outcome_var}")
        print(f"[logistic_regression] 候选预测变量: {predictor_vars}")

        # 变量预处理
        df_processed, var_info = self.variable_processor.process_variables(
            df=df,
            outcome_var=outcome_var,
            predictor_vars=predictor_vars + forced_covariates
        )

        # 确保因变量是0/1编码
        unique_outcomes = df_processed[outcome_var].dropna().unique()
        if len(unique_outcomes) == 2:
            # 二分类，进行编码标准化
            df_processed[outcome_var] = df_processed[outcome_var].map(
                {unique_outcomes[0]: 0, unique_outcomes[1]: 1}
            )
        elif set(unique_outcomes) == {0, 1}:
            pass  # 已经是0/1编码
        else:
            print(f"[logistic_regression] 警告: 因变量不是标准的0/1编码")

        # 移除缺失值
        all_vars = [outcome_var] + predictor_vars + forced_covariates
        df_clean = df_processed[all_vars].dropna()

        n_total = len(df)
        n_complete = len(df_clean)
        n_events = int(df_clean[outcome_var].sum())
        event_rate = n_events / n_complete if n_complete > 0 else 0

        print(f"[logistic_regression] 完整数据: {n_complete}/{n_total} 行")
        print(f"[logistic_regression] 事件数: {n_events} ({event_rate*100:.1f}%)")

        if n_complete < 50:
            print(f"[logistic_regression] 警告: 样本量较小，可能影响结果可靠性")

        # 单因素筛选
        print(f"[logistic_regression] 执行单因素筛选...")
        univariate_results = self.univariate_screen.screen(
            df=df_clean,
            outcome_var=outcome_var,
            predictor_vars=predictor_vars,
            p_threshold=p_enter
        )

        significant_vars = [v for v, r in univariate_results.items() if r["p_value"] < p_enter]

        print(f"[logistic_regression] 单因素显著变量: {significant_vars}")

        # 多因素建模
        final_vars = list(set(significant_vars + forced_covariates))

        print(f"[logistic_regression] 执行多因素建模...")
        multivariate_result = self.multivariate_builder.build(
            df=df_clean,
            outcome_var=outcome_var,
            predictor_vars=final_vars,
            selection_method=selection_method,
            p_enter=p_enter,
            p_remove=p_remove,
            forced_covariates=forced_covariates
        )

        # 计算Hosmer-Lemeshow检验
        hl_result = self._hosmer_lemeshow_test(
            df_clean, outcome_var, multivariate_result
        )

        # 计算AUC
        auc_result = self._calculate_auc(
            df_clean, outcome_var, multivariate_result
        )

        # 整理结果
        results = {
            "method": "多因素逻辑回归",
            "outcome_var": outcome_var,
            "n": n_complete,
            "n_events": n_events,
            "event_rate": event_rate,
            "predictors": [],
            "model_fit": {
                "hl_p": hl_result.get("p_value", 1),
                "hl_stat": hl_result.get("statistic", 0),
                "auc": auc_result.get("auc", 0),
                "aic": multivariate_result.get("aic", 0),
                "bic": multivariate_result.get("bic", 0),
                "pseudo_r2": multivariate_result.get("r_squared", 0)
            },
            "univariate_results": univariate_results,
            "selection_steps": multivariate_result.get("selection_steps", []),
            "variable_processing": var_info
        }

        # 添加预测变量结果
        for pred in multivariate_result.get("predictors", []):
            var_name = pred.get("variable", "")
            beta = pred.get("beta", 0)
            or_value = pred.get("or_value", np.exp(beta))
            ci = pred.get("ci", [0, 0])

            results["predictors"].append({
                "variable": var_name,
                "beta": float(beta),
                "or_value": float(or_value),
                "se": float(pred.get("se", 0)),
                "z": float(pred.get("z", 0)),
                "p_value": float(pred.get("p_value", 1)),
                "ci": [float(c) for c in ci]
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

        print(f"[logistic_regression] 分析完成!")
        print(f"[logistic_regression] 最终模型包含 {len(results['predictors'])} 个预测变量")
        print(f"[logistic_regression] AUC = {results['model_fit']['auc']:.3f}")

        return results

    def _hosmer_lemeshow_test(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        model_result: Dict
    ) -> Dict[str, float]:
        """Hosmer-Lemeshow拟合度检验"""
        try:
            from statsmodels.stats.diagnostic import het_breuschpagan

            model = model_result.get("model")
            if model is None:
                return {"statistic": 0, "p_value": 1}

            # 获取预测概率
            y_pred = model.predict()

            # 分组（默认10组）
            n_groups = 10
            df_temp = pd.DataFrame({
                'observed': df[outcome_var].values,
                'predicted': y_pred
            })
            df_temp['group'] = pd.qcut(df_temp['predicted'], n_groups, labels=False, duplicates='drop')

            # 计算每组的观察值和期望值
            observed = df_temp.groupby('group')['observed'].sum()
            expected = df_temp.groupby('group')['predicted'].sum()
            n = df_temp.groupby('group').size()

            # H-L统计量
            chi2 = np.sum((observed - expected) ** 2 / (expected * (1 - expected / n)))

            # 自由度 = 组数 - 2
            df_hl = len(observed) - 2

            from scipy import stats
            p_value = 1 - stats.chi2.cdf(chi2, df_hl)

            return {
                "statistic": float(chi2),
                "df": int(df_hl),
                "p_value": float(p_value)
            }
        except Exception as e:
            return {"statistic": 0, "p_value": 1, "error": str(e)}

    def _calculate_auc(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        model_result: Dict
    ) -> Dict[str, float]:
        """计算AUC"""
        try:
            from sklearn.metrics import roc_auc_score, roc_curve

            model = model_result.get("model")
            if model is None:
                return {"auc": 0}

            # 获取预测概率
            y_pred_prob = model.predict()

            # 计算AUC
            auc = roc_auc_score(df[outcome_var], y_pred_prob)

            return {"auc": float(auc)}
        except Exception as e:
            return {"auc": 0, "error": str(e)}
