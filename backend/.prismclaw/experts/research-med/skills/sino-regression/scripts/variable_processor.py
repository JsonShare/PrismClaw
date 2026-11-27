"""variable_processor.py - 变量处理

负责变量的标准化、哑变量编码等预处理工作。
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple
from sklearn.preprocessing import StandardScaler


class VariableProcessor:
    """变量处理器"""

    def __init__(self):
        self.scaler = StandardScaler()
        self.encoding_info = {}

    def process_variables(
        self,
        df: pd.DataFrame,
        outcome_var: str,
        predictor_vars: List[str]
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """处理所有变量

        参数：
            df: 数据框
            outcome_var: 因变量名称
            predictor_vars: 预测变量列表

        返回：
            (处理后的数据框, 变量信息字典)
        """
        df_processed = df.copy()
        var_info = {
            "original_vars": predictor_vars.copy(),
            "standardized_vars": [],
            "dummy_vars": {},
            "categorical_vars": [],
            "continuous_vars": []
        }

        for var in predictor_vars:
            if var not in df.columns:
                continue

            col = df[var]
            dtype = col.dtype

            # 检测变量类型
            if dtype == 'object' or dtype.name == 'category':
                # 分类变量 - 哑变量编码
                var_info["categorical_vars"].append(var)
                df_processed, dummy_names = self._create_dummy_variables(
                    df_processed, var
                )
                var_info["dummy_vars"][var] = dummy_names

            elif dtype in ['int64', 'float64']:
                # 连续变量
                var_info["continuous_vars"].append(var)

        return df_processed, var_info

    def _create_dummy_variables(
        self,
        df: pd.DataFrame,
        var: str,
        drop_first: bool = True
    ) -> Tuple[pd.DataFrame, List[str]]:
        """创建哑变量

        参数：
            df: 数据框
            var: 变量名
            drop_first: 是否删除第一个类别（避免多重共线性）

        返回：
            (处理后的数据框, 哑变量名称列表)
        """
        dummies = pd.get_dummies(df[var], prefix=var, drop_first=drop_first)
        dummy_names = dummies.columns.tolist()

        # 删除原始变量，添加哑变量
        df_processed = df.drop(columns=[var])
        df_processed = pd.concat([df_processed, dummies], axis=1)

        self.encoding_info[var] = {
            "type": "dummy",
            "dummy_names": dummy_names,
            "original_categories": df[var].unique().tolist()
        }

        return df_processed, dummy_names

    def standardize(
        self,
        df: pd.DataFrame,
        vars_to_standardize: List[str]
    ) -> pd.DataFrame:
        """标准化变量

        参数：
            df: 数据框
            vars_to_standardize: 需要标准化的变量列表

        返回：
            标准化后的数据框
        """
        df_std = df.copy()

        if len(vars_to_standardize) == 0:
            return df_std

        # 只选择数值列
        vars_exist = [v for v in vars_to_standardize if v in df.columns]
        if len(vars_exist) == 0:
            return df_std

        # 标准化
        scaler = StandardScaler()
        df_std[vars_exist] = scaler.fit_transform(df[vars_exist])

        return df_std

    def calculate_std_beta(
        self,
        df: pd.DataFrame,
        y: pd.Series,
        predictor_vars: List[str],
        model
    ) -> Dict[str, float]:
        """计算标准化回归系数

        参数：
            df: 数据框
            y: 因变量
            predictor_vars: 预测变量列表
            model: 已拟合的模型

        返回：
            {变量名: 标准化系数}
        """
        # 标准化自变量
        X_std = df[predictor_vars].copy()
        scaler = StandardScaler()
        X_std_values = scaler.fit_transform(X_std)

        # 标准化因变量
        y_mean = y.mean()
        y_std = y.std()
        y_std_values = (y - y_mean) / y_std

        # 用标准化数据重新拟合
        import statsmodels.api as sm
        X_std_const = sm.add_constant(X_std_values)
        model_std = sm.OLS(y_std_values, X_std_const).fit()

        std_betas = {}
        for i, var in enumerate(predictor_vars):
            # 注意：索引+1是因为有常数项
            if i + 1 < len(model_std.params):
                std_betas[var] = float(model_std.params.iloc[i + 1])

        return std_betas

    def get_variable_summary(
        self,
        df: pd.DataFrame,
        var: str
    ) -> Dict[str, Any]:
        """获取变量摘要信息

        参数：
            df: 数据框
            var: 变量名

        返回：
            变量摘要字典
        """
        if var not in df.columns:
            return {}

        col = df[var]
        dtype = col.dtype

        summary = {
            "name": var,
            "type": str(dtype),
            "n_total": len(col),
            "n_missing": int(col.isna().sum()),
            "missing_rate": float(col.isna().sum() / len(col))
        }

        if dtype in ['int64', 'float64']:
            summary.update({
                "mean": float(col.mean()),
                "std": float(col.std()),
                "min": float(col.min()),
                "max": float(col.max()),
                "median": float(col.median()),
                "q25": float(col.quantile(0.25)),
                "q75": float(col.quantile(0.75)),
                "skewness": float(col.skew())
            })
        else:
            summary.update({
                "n_unique": int(col.nunique()),
                "categories": col.unique().tolist()[:10],  # 最多10个
                "mode": col.mode().iloc[0] if len(col.mode()) > 0 else None
            })

        return summary
