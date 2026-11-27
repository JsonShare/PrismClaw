"""model_diagnostics.py - 模型诊断

对回归模型进行诊断，包括：
- 残差正态性检验
- 异方差检验
- 异常点识别（学生化残差）
- 强影响点识别（DFFITS、Cook距离）
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import (
    OLSInfluence,
    summary_table
)


class ModelDiagnostics:
    """模型诊断器"""

    def __init__(self):
        pass

    def diagnose(
        self,
        y,
        X: pd.DataFrame,
        model=None
    ) -> Dict[str, Any]:
        """执行完整的模型诊断

        参数：
            y: 因变量
            X: 自变量
            model: 已拟合的模型（可选）

        返回：
            诊断结果字典
        """
        diagnostics = {
            "residual_normality": None,
            "heteroscedasticity": None,
            "outliers": [],
            "influential_points": [],
            "summary": {}
        }

        if model is None:
            diagnostics["summary"] = {
                "status": "model_not_provided",
                "message": "请提供已拟合的模型进行诊断"
            }
            return diagnostics

        try:
            # 残差正态性检验
            diagnostics["residual_normality"] = self._check_residual_normality(model)

            # 异方差检验
            diagnostics["heteroscedasticity"] = self._check_heteroscedasticity(model)

            # 异常点识别
            diagnostics["outliers"] = self._identify_outliers(model)

            # 强影响点识别
            diagnostics["influential_points"] = self._identify_influential_points(model)

            # 总结
            diagnostics["summary"] = self._generate_diagnostic_summary(diagnostics)

        except Exception as e:
            diagnostics["summary"] = {
                "status": "error",
                "message": f"诊断过程出错: {str(e)}"
            }

        return diagnostics

    def _check_residual_normality(self, model) -> Dict[str, Any]:
        """残差正态性检验"""
        from scipy import stats

        residuals = model.resid

        # Shapiro-Wilk检验（样本量<5000时推荐）
        if len(residuals) < 5000:
            stat, p_value = stats.shapiro(residuals)
            test_name = "Shapiro-Wilk"
        else:
            # Kolmogorov-Smirnov检验
            stat, p_value = stats.kstest(residuals, 'norm')
            test_name = "Kolmogorov-Smirnov"

        # 偏度和峰度
        skewness = stats.skew(residuals)
        kurtosis = stats.kurtosis(residuals)

        return {
            "test": test_name,
            "statistic": float(stat),
            "p_value": float(p_value),
            "skewness": float(skewness),
            "kurtosis": float(kurtosis),
            "is_normal": p_value > 0.05,
            "interpretation": "残差服从正态分布" if p_value > 0.05 else "残差不服从正态分布，模型估计可能偏倚"
        }

    def _check_heteroscedasticity(self, model) -> Dict[str, Any]:
        """异方差检验"""
        from scipy import stats

        residuals = model.resid
        fitted = model.fittedvalues

        # Breusch-Pagan检验
        try:
            from statsmodels.stats.diagnostic import het_breuschpagan
            bp_stat, bp_pvalue, _, _ = het_breuschpagan(residuals, model.model.exog)
            bp_test = True
        except:
            bp_stat, bp_pvalue = None, None
            bp_test = False

        # White检验
        try:
            from statsmodels.stats.diagnostic import het_white
            white_stat, white_pvalue, _, _ = het_white(residuals, model.model.exog)
            white_test = True
        except:
            white_stat, white_pvalue = None, None
            white_test = False

        # 相关性检验（残差与拟合值）
        correlation = np.corrcoef(fitted, residuals)[0, 1]

        return {
            "breusch_pagan": {
                "statistic": float(bp_stat) if bp_stat else None,
                "p_value": float(bp_pvalue) if bp_pvalue else None,
                "is_homoscedastic": bp_pvalue > 0.05 if bp_pvalue else None
            } if bp_test else {},
            "white": {
                "statistic": float(white_stat) if white_stat else None,
                "p_value": float(white_pvalue) if white_pvalue else None,
                "is_homoscedastic": white_pvalue > 0.05 if white_pvalue else None
            } if white_test else {},
            "residual_fitted_correlation": float(correlation),
            "interpretation": "方差齐性" if (not bp_test or bp_pvalue > 0.05) else "存在异方差，标准误估计可能不准确"
        }

    def _identify_outliers(self, model, threshold: float = 3) -> list:
        """识别异常点（学生化残差 > 3）"""
        try:
            influence = OLSInfluence(model)
            studentized_residuals = influence.resid_studentized_external

            outliers = []
            for i, resid in enumerate(studentized_residuals):
                if abs(resid) > threshold:
                    outliers.append({
                        "index": int(i),
                        "studentized_residual": float(resid),
                        "fitted_value": float(model.fittedvalues[i]),
                        "actual_value": float(model.model.endog[i]) if i < len(model.model.endog) else None
                    })

            return outliers
        except:
            return []

    def _identify_influential_points(self, model, threshold: float = 0.5) -> list:
        """识别强影响点（Cook's D > 4/n）"""
        try:
            influence = OLSInfluence(model)
            cooks_d = influence.cooks_distance[0]

            # Cook's D阈值
            n = len(model.resid)
            d_threshold = 4 / n

            influential = []
            for i, d in enumerate(cooks_d):
                if abs(d) > d_threshold:
                    influential.append({
                        "index": int(i),
                        "cooks_distance": float(d),
                        "threshold": float(d_threshold),
                        "fitted_value": float(model.fittedvalues[i])
                    })

            return influential
        except:
            return []

    def _generate_diagnostic_summary(self, diagnostics: Dict) -> Dict[str, Any]:
        """生成诊断总结"""
        issues = []
        warnings = []

        # 检查正态性
        norm_result = diagnostics.get("residual_normality")
        if norm_result and not norm_result.get("is_normal", True):
            issues.append("残差不服从正态分布")

        # 检查异方差
        hetero_result = diagnostics.get("heteroscedasticity")
        if hetero_result:
            bp = hetero_result.get("breusch_pagan", {})
            if bp.get("is_homoscedastic") == False:
                issues.append("存在异方差问题")

        # 检查异常点
        outliers = diagnostics.get("outliers", [])
        if len(outliers) > 0:
            warnings.append(f"发现{len(outliers)}个异常点")

        # 检查强影响点
        influential = diagnostics.get("influential_points", [])
        if len(influential) > 0:
            warnings.append(f"发现{len(influential)}个强影响点")

        # 总体评价
        if len(issues) == 0 and len(warnings) == 0:
            status = "good"
            message = "模型诊断通过，无明显问题"
        elif len(issues) > 0:
            status = "problematic"
            message = f"模型存在{len(issues)}个问题，需要关注"
        else:
            status = "acceptable"
            message = f"模型存在{len(warnings)}个警告，建议检查"

        return {
            "status": status,
            "message": message,
            "issues": issues,
            "warnings": warnings,
            "n_outliers": len(outliers),
            "n_influential": len(influential)
        }
