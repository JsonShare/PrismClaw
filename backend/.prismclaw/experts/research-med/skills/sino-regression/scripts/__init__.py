"""sino-regression: 回归分析Skill

支持多种回归分析：
- 线性回归（连续变量结局）
- 逻辑回归（二分类结局）
- Cox回归（生存结局，Phase 2）

标准流行病学两步流程：
1. 单因素筛选：每个自变量分别与因变量做简单回归，p<0.1的进入多因素模型
2. 多因素建模：将筛选出的变量纳入多元线性回归
"""

__version__ = "1.0.0"
__author__ = "medical-skills-build team"

from .regression_selector import RegressionSelector
from .linear_regression import LinearRegressionAnalyzer
from .logistic_regression import LogisticRegressionAnalyzer
from .cox_regression import CoxRegressionAnalyzer
from .multivariate_model import MultivariateModelBuilder

__all__ = [
    "RegressionSelector",
    "LinearRegressionAnalyzer",
    "LogisticRegressionAnalyzer",
    "CoxRegressionAnalyzer",
    "MultivariateModelBuilder",
]