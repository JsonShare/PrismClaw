"""interpreter_factory.py - 解释器工厂

根据结果类型创建对应的解释器。
"""

from typing import Dict, Any, Optional

from interpreters.ttest_interpreter import TTestInterpreter
from interpreters.chi2_interpreter import Chi2Interpreter
from interpreters.anova_interpreter import AnovaInterpreter
from interpreters.linear_interpreter import LinearRegressionInterpreter
from interpreters.logistic_interpreter import LogisticRegressionInterpreter


class InterpreterFactory:
    """解释器工厂类"""

    def __init__(self):
        self._interpreters = {
            "ttest": TTestInterpreter,
            "t_test": TTestInterpreter,
            "t检验": TTestInterpreter,
            "paired_ttest": TTestInterpreter,
            "配对t检验": TTestInterpreter,
            "chi2": Chi2Interpreter,
            "chi_square": Chi2Interpreter,
            "卡方检验": Chi2Interpreter,
            "fisher": Chi2Interpreter,
            "mcnemar": Chi2Interpreter,
            "anova": AnovaInterpreter,
            "单因素anova": AnovaInterpreter,
            "repeated_measures_anova": AnovaInterpreter,
            "two_way_anova": AnovaInterpreter,
            "linear": LinearRegressionInterpreter,
            "linear_regression": LinearRegressionInterpreter,
            "线性回归": LinearRegressionInterpreter,
            "logistic": LogisticRegressionInterpreter,
            "logistic_regression": LogisticRegressionInterpreter,
            "逻辑回归": LogisticRegressionInterpreter,
        }

        # 默认解释器
        self._default_ttest = TTestInterpreter()
        self._default_chi2 = Chi2Interpreter()
        self._default_anova = AnovaInterpreter()
        self._default_linear = LinearRegressionInterpreter()
        self._default_logistic = LogisticRegressionInterpreter()

    def create_interpreter(self, result_type: str):
        """根据结果类型创建解释器

        参数：
            result_type: 结果类型（differential/regression/survival）

        返回：
            对应的解释器实例
        """
        if result_type == "differential":
            return self._default_ttest  # 简化版，实际应该根据method字段选择
        elif result_type == "regression":
            return self._default_linear  # 简化版，实际应该根据method字段选择
        elif result_type == "survival":
            return self._default_linear  # 简化版，Cox回归用类似线性回归的解释
        else:
            return self._default_ttest

    def get_interpreter_for_method(self, method: str):
        """根据具体方法名获取解释器

        参数：
            method: 统计方法名称

        返回：
            对应的解释器实例
        """
        method_lower = method.lower()

        for key, interpreter_class in self._interpreters.items():
            if key.lower() in method_lower:
                return interpreter_class()

        # 默认返回t检验解释器
        return self._default_ttest

    def interpret(self, results: Dict[str, Any], result_type: Optional[str] = None) -> str:
        """解释结果的快捷方法

        参数：
            results: 分析结果字典
            result_type: 结果类型（可选，会从results中推断）

        返回：
            Plain Language解释字符串
        """
        # 推断结果类型
        if result_type is None:
            result_type = self._infer_result_type(results)

        # 获取方法名
        method = results.get("method", "").lower()

        # 根据方法选择解释器
        interpreter = self.get_interpreter_for_method(method)

        return interpreter.interpret(results)

    def _infer_result_type(self, results: Dict[str, Any]) -> str:
        """从结果字典推断结果类型"""
        # 检查结果中的关键字段
        if "predictors" in results or "model_fit" in results or "outcome_var" in results:
            if "or_value" in str(results.get("predictors", [{}])[0]) if results.get("predictors") else "":
                return "regression"
            return "regression"
        elif "means" in results or "rate_difference" in results or "statistics" in results:
            return "differential"
        elif "hr" in results or "survival" in str(results.get("method", "")).lower():
            return "survival"
        else:
            return "differential"  # 默认
