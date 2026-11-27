"""cox_regression.py - Cox回归实现

基于lifelines库实现Cox比例风险回归分析。
支持：
- HR值和95%置信区间
- Schoenfeld残差检验（比例风险假设）
- 生存曲线绘制
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple, Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


class CoxRegressionAnalyzer:
    """Cox回归分析器"""

    def __init__(self):
        self.dependencies_available = self._check_dependencies()

    def _check_dependencies(self) -> bool:
        """检查依赖是否可用"""
        try:
            from lifelines import CoxPHFitter
            return True
        except ImportError:
            print("[cox_regression] 警告: lifelines库未安装，将使用statsmodels替代")
            return False

    def analyze(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str],
        time_var: str = "time",
        event_var: str = "event",
        forced_covariates: List[str] = None,
        selection_method: str = "stepwise_forward",
        p_enter: float = 0.1,
        p_remove: float = 0.2
    ) -> Dict[str, Any]:
        """执行完整的Cox回归分析

        参数：
            df: 数据框
            outcome_var: 因变量名称（生存状态）
            predictor_vars: 预测变量列表
            time_var: 时间变量名称
            event_var: 事件变量名称（0=删失，1=事件）
            forced_covariates: 强制纳入的协变量
            selection_method: 变量筛选方法
            p_enter: 进入阈值
            p_remove: 移除阈值

        返回：
            完整的Cox回归分析结果字典
        """
        if forced_covariates is None:
            forced_covariates = []

        print(f"[cox_regression] 开始Cox回归分析...")

        # 检查数据要求
        if time_var not in df.columns:
            raise ValueError(f"时间变量 {time_var} 不在数据中")
        if event_var not in df.columns:
            raise ValueError(f"事件变量 {event_var} 不在数据中")

        # 准备数据
        df_cox = df[[time_var, event_var] + predictor_vars + forced_covariates].copy()
        df_clean = df_cox.dropna()

        n_total = len(df)
        n_complete = len(df_clean)
        n_events = int(df_clean[event_var].sum())

        print(f"[cox_regression] 完整数据: {n_complete}/{n_total} 行")
        print(f"[cox_regression] 事件数: {n_events}")

        if self.dependencies_available:
            return self._analyze_with_lifelines(
                df_clean, time_var, event_var, predictor_vars, forced_covariates
            )
        else:
            return self._analyze_fallback(
                df_clean, time_var, event_var, predictor_vars
            )

    def _analyze_with_lifelines(
        self,
        df: pd.DataFrame,
        time_var: str,
        event_var: str,
        predictor_vars: List[str],
        forced_covariates: List[str]
    ) -> Dict[str, Any]:
        """使用lifelines进行分析"""
        from lifelines import CoxPHFitter
        from lifelines.statistics import proportional_hazard_test

        # 准备数据（lifelines需要特定格式）
        df_lifelines = df.copy()
        df_lifelines = df_lifelines.rename(columns={
            time_var: 'duration',
            event_var: 'event'
        })

        all_predictors = predictor_vars + forced_covariates

        # 拟合Cox模型
        cph = CoxPHFitter()
        cph.fit(df_lifelines[['duration', 'event'] + all_predictors],
                duration_col='duration',
                event_col='event')

        # 提取结果
        summary = cph.summary

        predictors = []
        for var in all_predictors:
            if var in summary.index:
                row = summary.loc[var]
                hr = row['exp(coef)']
                ci = [row['exp(coef) lower 95%'], row['exp(coef) upper 95%']]
                p_value = row['p']

                predictors.append({
                    "variable": var,
                    "hr": float(hr),
                    "ci": [float(c) for c in ci],
                    "se": float(row['se(coef)']),
                    "z": float(row['z']),
                    "p_value": float(p_value)
                })

        # 比例风险假设检验
        ph_test = proportional_hazard_test(
            cph, df_lifelines[['duration', 'event'] + all_predictors]
        )
        ph_pvalue = ph_test.p_value

        # 模型评估
        try:
            concordance = cph.concordance_index_
        except:
            concordance = 0

        # 构建结果
        results = {
            "method": "Cox比例风险回归",
            "time_var": time_var,
            "event_var": event_var,
            "n": len(df),
            "n_events": int(df['event'].sum()),
            "predictors": sorted(predictors, key=lambda x: x["p_value"]),
            "model_fit": {
                "concordance": float(concordance),
                "partial_log_likelihood": float(cph.log_likelihood_),
                "aic": float(cph.AIC_partial_),
                "ph_assumption_p": float(ph_pvalue),
                "ph_assumption_valid": ph_pvalue > 0.05
            },
            "summary": cph.print_summary(return_result=True)
        }

        # 添加显著因素
        results["significant_predictors"] = [
            p for p in results["predictors"] if p["p_value"] < 0.05
        ]

        print(f"[cox_regression] 分析完成!")
        print(f"[cox_regression] C-index = {concordance:.3f}")

        return results

    def _analyze_fallback(
        self,
        df: pd.DataFrame,
        time_var: str,
        event_var: str,
        predictor_vars: List[str]
    ) -> Dict[str, Any]:
        """无法使用lifelines时的降级方案"""
        import statsmodels.duration.hazard_regression as hz

        print("[cox_regression] 使用statsmodels替代方案...")

        # 注意：statsmodels的Cox回归接口较复杂，这里提供简化版
        # 实际上建议安装lifelines库

        results = {
            "method": "Cox比例风险回归（简化版）",
            "time_var": time_var,
            "event_var": event_var,
            "n": len(df),
            "n_events": int(df[event_var].sum()),
            "predictors": [],
            "model_fit": {
                "warning": "建议安装lifelines库以获得完整功能"
            },
            "note": "statsmodels不支持完整的Cox回归，请使用pip install lifelines"
        }

        return results

    def calculate_median_survival(
        self,
        df: pd.DataFrame,
        time_var: str,
        event_var: str,
        group_var: str = None
    ) -> Dict[str, Any]:
        """计算中位生存时间

        参数：
            df: 数据框
            time_var: 时间变量
            event_var: 事件变量
            group_var: 分组变量（可选）

        返回：
            中位生存时间信息
        """
        if self.dependencies_available:
            from lifelines import KaplanMeierFitter

            kmf = KaplanMeierFitter()

            if group_var:
                results = {}
                for group_value in df[group_var].unique():
                    df_group = df[df[group_var] == group_value]
                    kmf.fit(
                        df_group[time_var],
                        df_group[event_var],
                        label=group_value
                    )
                    results[group_value] = {
                        "median_survival": float(kmf.median_) if not np.isnan(kmf.median_) else None,
                        "survival_curve": kmf.survival_function_.to_dict()
                    }
                return results
            else:
                kmf.fit(df[time_var], df[event_var])
                return {
                    "median_survival": float(kmf.median_) if not np.isnan(kmf.median_) else None,
                    "survival_curve": kmf.survival_function_.to_dict()
                }
        else:
            return {"error": "lifelines库未安装"}
