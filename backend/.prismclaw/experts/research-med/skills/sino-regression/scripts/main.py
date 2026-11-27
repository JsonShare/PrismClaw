"""sino-regression 主入口


用法:
    python main.py --session_id <session_id> [--method <linear|logistic|cox>]

参数:
    --session_id: 会话ID，用于读取数据桩
    --method: 回归方法 (linear|logistic|cox)，默认自动选择
    --outcome_var: 因变量名称
    --predictor_vars: 自变量列表（逗号分隔）
    --forced_covariates: 强制纳入的协变量（逗号分隔）
    --selection_method: 变量筛选方法 (stepwise_forward|enter|backward)

输出:
    缺省写入 <cwd>/session-{session_id}/11_regression_results.json，可用 --output_path 覆盖
"""

import argparse
import json
import sys
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from regression_selector import RegressionSelector
from linear_regression import LinearRegressionAnalyzer
from logistic_regression import LogisticRegressionAnalyzer
from cox_regression import CoxRegressionAnalyzer
import pandas as pd


def parse_args():
    parser = argparse.ArgumentParser(description="regression-analysis: 回归分析")
    parser.add_argument("--session_id", type=str, required=True, help="会话ID")
    parser.add_argument("--method", type=str, default="auto", choices=["auto", "linear", "logistic", "cox"], help="回归方法")
    parser.add_argument("--outcome_var", type=str, default=None, help="因变量名称")
    parser.add_argument("--predictor_vars", type=str, default=None, help="自变量列表（逗号分隔）")
    parser.add_argument("--forced_covariates", type=str, default=None, help="强制纳入的协变量")
    parser.add_argument("--selection_method", type=str, default="stepwise_forward", choices=["stepwise_forward", "enter", "backward"], help="变量筛选方法")
    parser.add_argument("--p_enter", type=float, default=0.1, help="进入模型的p值阈值")
    parser.add_argument("--p_remove", type=float, default=0.2, help="移除模型的p值阈值")
    parser.add_argument("--check_multicollinearity", type=str, default="true", help="是否检查共线性")
    parser.add_argument("--output_path", type=str, default=None, help="自定义输出路径")
    parser.add_argument("--data_path", type=str, default=None, help="输入 CSV 路径；缺省时用 <cwd>/session-<id>/01_prepared_data.csv")
    return parser.parse_args()


def load_data(session_id: str, data_path: str | None = None) -> tuple:
    """加载清洗后的数据和数据字典。

    ``data_path`` 给出时直接读该 CSV；否则按会话数据桩
    ``<work_dir>/session-<session_id>/01_prepared_data.csv`` 查找。
    """
    if data_path:
        csv_path = Path(data_path).expanduser().resolve()
        context_path = csv_path.parent
    else:
        context_path = Path.cwd() / f"session-{session_id}"
        csv_path = context_path / "01_prepared_data.csv"

    if not csv_path.exists():
        raise FileNotFoundError(f"数据文件不存在: {csv_path}")

    df = pd.read_csv(csv_path)
    print(f"[regression-analysis] 已加载数据: {csv_path} ({len(df)}行, {len(df.columns)}列)")

    # 读取数据字典
    dict_path = context_path / "02_data_dictionary.md"
    data_dict = {}
    if dict_path.exists():
        with open(dict_path, "r", encoding="utf-8") as f:
            content = f.read()
            # 简单解析markdown格式的数据字典
            for line in content.split("\n"):
                if line.startswith("-") and ":" in line:
                    parts = line[1:].split(":", 1)
                    if len(parts) == 2:
                        var_name = parts[0].strip()
                        var_info = parts[1].strip()
                        data_dict[var_name] = var_info

    return df, data_dict


def auto_detect_regression_type(df: pd.DataFrame, outcome_var: str) -> str:
    """根据因变量类型自动判断回归方法"""
    if outcome_var not in df.columns:
        return "linear"  # 默认

    col_dtype = df[outcome_var].dtype
    unique_values = df[outcome_var].dropna().unique()

    # 检查唯一值数量
    if len(unique_values) <= 2:
        # 二分类
        return "logistic"
    elif col_dtype in ['int64', 'float64']:
        # 连续变量
        return "linear"
    else:
        return "linear"  # 默认


def _json_default(o):
    """numpy 标量/数组转原生类型，避免 json 序列化失败。"""
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def save_results(output_path: Path, results: dict):
    """保存回归分析结果"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=_json_default)
    print(f"[regression-analysis] 结果已保存至: {output_path}")


def main():
    args = parse_args()
    session_id = args.session_id

    print(f"[regression-analysis] 开始回归分析...")
    print(f"[sino-regression] session_id: {session_id}")

    # 加载数据
    try:
        df, data_dict = load_data(session_id, args.data_path)
    except FileNotFoundError as e:
        print(f"[regression-analysis] 错误: {e}")
        print("[regression-analysis] 请先执行data-preparation进行数据清洗")
        sys.exit(1)

    # 确定因变量
    outcome_var = args.outcome_var
    if outcome_var is None:
        # 尝试从数据字典或数据中推断
        print("[sino-regression] 未指定因变量，尝试自动检测...")
        # 默认使用最后一列或名为outcome/结果的列
        if "outcome" in df.columns:
            outcome_var = "outcome"
        elif "结果" in df.columns:
            outcome_var = "结果"
        else:
            outcome_var = df.columns[-1]

    print(f"[sino-regression] 因变量: {outcome_var}")

    # 确定回归方法
    method = args.method
    if method == "auto":
        method = auto_detect_regression_type(df, outcome_var)
        print(f"[sino-regression] 自动检测回归方法: {method}")

    # 解析自变量
    predictor_vars = args.predictor_vars
    if predictor_vars:
        predictor_list = [v.strip() for v in predictor_vars.split(",")]
    else:
        # 使用除因变量外的所有数值列
        predictor_list = [col for col in df.columns if col != outcome_var and df[col].dtype in ['int64', 'float64']]

    # 解析强制协变量
    forced_covariates = []
    if args.forced_covariates:
        forced_covariates = [v.strip() for v in args.forced_covariates.split(",")]

    print(f"[sino-regression] 自变量: {predictor_list}")
    print(f"[sino-regression] 强制协变量: {forced_covariates}")

    # 创建选择器
    selector = RegressionSelector()

    # 根据方法执行分析
    if method == "linear":
        analyzer = LinearRegressionAnalyzer()
        results = analyzer.analyze(
            df=df,
            outcome_var=outcome_var,
            predictor_vars=predictor_list,
            forced_covariates=forced_covariates,
            selection_method=args.selection_method,
            p_enter=args.p_enter,
            p_remove=args.p_remove,
            check_multicollinearity=args.check_multicollinearity.lower() == "true"
        )
    elif method == "logistic":
        analyzer = LogisticRegressionAnalyzer()
        results = analyzer.analyze(
            df=df,
            outcome_var=outcome_var,
            predictor_vars=predictor_list,
            forced_covariates=forced_covariates,
            selection_method=args.selection_method,
            p_enter=args.p_enter,
            p_remove=args.p_remove
        )
    elif method == "cox":
        analyzer = CoxRegressionAnalyzer()
        results = analyzer.analyze(
            df=df,
            outcome_var=outcome_var,
            predictor_vars=predictor_list,
            forced_covariates=forced_covariates,
            selection_method=args.selection_method,
            p_enter=args.p_enter,
            p_remove=args.p_remove
        )
    else:
        print(f"[sino-regression] 错误: 不支持的回归方法 {method}")
        sys.exit(1)

    # 确定输出路径
    if args.output_path:
        output_path = Path(args.output_path).expanduser().resolve()
    elif args.data_path:
        output_path = Path(args.data_path).expanduser().resolve().parent / "11_regression_results.json"
    else:
        output_path = Path.cwd() / f"session-{session_id}" / "11_regression_results.json"

    # 保存结果
    save_results(output_path, results)

    print(f"[sino-regression] 回归分析完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())