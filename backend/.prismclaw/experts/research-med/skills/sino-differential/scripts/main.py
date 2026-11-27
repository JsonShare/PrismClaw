#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
差异分析主入口

功能：执行分组数据的差异分析，支持自动方法选择
用法：
    python scripts/main.py --session_id xxx --groupby_vars '["treatment"]' --compare_vars '["efficacy_score"]'
"""
import argparse
import json
import sys
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from method_selector import MethodSelector
from normality_tester import NormalityTester
from variance_tester import VarianceTester
from param_tests import ParametricTests
from nonparam_tests import NonparametricTests
from proportions_test import ProportionsTests
from effect_size import EffectSizeCalculator
from correction import MultipleComparisonCorrector
from result_formatter import ResultFormatter


def load_data(session_id: str, data_path: str = None):
    """
    从共享数据桩加载数据

    参数：
        session_id: 会话ID
        data_path: 可选的数据路径

    返回：
        DataFrame: 加载的数据
        dict: 数据字典
    """
    import pandas as pd

    if data_path:
        data_file = Path(data_path).expanduser().resolve()
    else:
        # 缺省按会话数据桩查找：<cwd>/session-<id>/01_prepared_data.csv
        data_file = Path.cwd() / f"session-{session_id}" / "01_prepared_data.csv"

    if not data_file.exists():
        raise FileNotFoundError(f"数据文件不存在: {data_file}")

    # 读取数据
    df = pd.read_csv(data_file)

    # 尝试读取数据字典
    dict_file = data_file.parent / "02_data_dictionary.md"
    data_dict = {}
    if dict_file.exists():
        # 简单解析数据字典
        with open(dict_file, 'r', encoding='utf-8') as f:
            content = f.read()
            # 提取变量信息
            for line in content.split('\n'):
                if line.startswith('|') and not line.startswith('|--'):
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if len(parts) >= 2:
                        var_name = parts[0]
                        var_label = parts[1] if len(parts) > 1 else var_name
                        data_dict[var_name] = {'label': var_label}

    return df, data_dict


def save_results(results: dict, session_id: str, output_path: str = None):
    """
    保存分析结果到共享数据桩

    参数：
        results: 分析结果字典
        session_id: 会话ID
        output_path: 可选的输出路径
    """
    if output_path:
        output_file = Path(output_path).expanduser().resolve()
    else:
        output_file = Path.cwd() / f"session-{session_id}" / "10_differential_results.json"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    def _default(o):
        """numpy 标量/数组转原生类型，避免 json 序列化失败。"""
        if hasattr(o, 'item'):
            return o.item()
        if hasattr(o, 'tolist'):
            return o.tolist()
        raise TypeError(f'Object of type {type(o).__name__} is not JSON serializable')

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=_default)

    print(f"结果已保存到: {output_file}")


def main():
    """主入口函数"""
    parser = argparse.ArgumentParser(description='差异分析 - 自动方法选择')
    parser.add_argument('--session_id', type=str, required=True, help='会话ID')
    parser.add_argument('--groupby_vars', type=str, required=True,
                        help='分组变量，JSON数组格式，如 \'["treatment_group"]\'')
    parser.add_argument('--compare_vars', type=str, required=True,
                        help='比较变量，JSON数组格式，如 \'["efficacy_score", "response_rate"]\'')
    parser.add_argument('--test_type', type=str, default='auto',
                        choices=['auto', 'parametric', 'nonparametric', 'proportions'],
                        help='检验类型：auto自动选择，或指定parametric/nonparametric/proportions')
    parser.add_argument('--user_specified_method', type=str, default=None,
                        help='用户指定的检验方法')
    parser.add_argument('--correction_method', type=str, default='BH',
                        choices=['Bonferroni', 'BH', 'None'],
                        help='多重比较校正方法')
    parser.add_argument('--alpha', type=float, default=0.05, help='显著性水平')
    parser.add_argument('--confidence_level', type=float, default=0.95, help='置信水平')
    parser.add_argument('--data_path', type=str, default=None, help='数据文件路径')
    parser.add_argument('--design', type=str, default='independent',
                        choices=['independent', 'paired', 'repeated'],
                        help='设计类型')

    args = parser.parse_args()

    # 解析JSON参数
    import json
    groupby_vars = json.loads(args.groupby_vars)
    compare_vars = json.loads(args.compare_vars)

    print("=" * 60)
    print("差异分析开始")
    print("=" * 60)
    print(f"会话ID: {args.session_id}")
    print(f"分组变量: {groupby_vars}")
    print(f"比较变量: {compare_vars}")
    print(f"检验类型: {args.test_type}")
    print(f"设计类型: {args.design}")

    # 加载数据
    df, data_dict = load_data(args.session_id, args.data_path)
    print(f"\n数据加载完成: {len(df)} 条记录, {len(df.columns)} 个变量")

    # 初始化各模块
    method_selector = MethodSelector()
    normality_tester = NormalityTester()
    variance_tester = VarianceTester()
    param_tests = ParametricTests()
    nonparam_tests = NonparametricTests()
    proportions_tests = ProportionsTests()
    effect_size_calc = EffectSizeCalculator()
    corrector = MultipleComparisonCorrector()
    formatter = ResultFormatter()

    # 存储所有结果
    all_results = []
    all_p_values = []

    # 对每个分组变量和比较变量组合进行分析
    for groupby_var in groupby_vars:
        if groupby_var not in df.columns:
            print(f"警告: 分组变量 '{groupby_var}' 不在数据中，跳过")
            continue

        for compare_var in compare_vars:
            if compare_var not in df.columns:
                print(f"警告: 比较变量 '{compare_var}' 不在数据中，跳过")
                continue

            print(f"\n--- 分析: {compare_var} 按 {groupby_var} 分组 ---")

            # 获取分组数据
            groups_data = df.groupby(groupby_var)[compare_var].apply(list).to_dict()
            groups = list(groups_data.keys())
            n_groups = len(groups)

            if n_groups < 2:
                print(f"警告: 分组 '{groupby_var}' 只有 {n_groups} 组，跳过")
                continue

            # 判断变量类型
            is_categorical = df[compare_var].dtype == 'object' or df[compare_var].nunique() < 5

            if is_categorical:
                # 分类变量 - 率比较
                test_type = 'proportions'
            else:
                # 连续变量 - 均值比较
                test_type = args.test_type

            # 检验正态性和方差齐性
            normality_results = {}
            for group in groups:
                group_data = groups_data[group]
                n = len(group_data)
                normality_results[group] = normality_tester.test_normality(group_data, n)

            all_normal = all(r['is_normal'] for r in normality_results.values())

            # 方差齐性检验（仅对两组的连续变量）
            variance_result = None
            if n_groups == 2 and not is_categorical:
                group_values = [groups_data[g] for g in groups]
                variance_result = variance_tester.test_variance_homogeneity(*group_values)
                variance_homogeneous = variance_result['is_homogeneous']
            else:
                variance_homogeneous = True

            # 计算总样本量
            total_n = sum(len(groups_data[g]) for g in groups)
            min_group_n = min(len(groups_data[g]) for g in groups)

            # 方法选择
            if args.user_specified_method:
                selected_method = args.user_specified_method
                method_selection_reason = f"用户指定: {selected_method}"
            else:
                selected_method, method_selection_reason = method_selector.select_method(
                    var_type='continuous' if not is_categorical else 'categorical',
                    n_groups=n_groups,
                    normality=all_normal,
                    variance_homogeneous=variance_homogeneous,
                    n=min_group_n,
                    design=args.design
                )

            print(f"选择方法: {selected_method}")
            print(f"选择原因: {method_selection_reason}")

            # 执行检验
            if selected_method in ['independent_ttest', 'welch_ttest', 'paired_ttest']:
                # t检验
                group1 = groups_data[groups[0]]
                group2 = groups_data[groups[1]] if len(groups) > 1 else None

                if selected_method == 'paired_ttest' or args.design == 'paired':
                    test_result = param_tests.paired_ttest(group1, group2)
                elif selected_method == 'welch_ttest' or not variance_homogeneous:
                    test_result = param_tests.welch_ttest(group1, group2)
                else:
                    test_result = param_tests.independent_ttest(group1, group2)

                # 计算效应量
                if group2:
                    es_result = effect_size_calc.cohens_d(group1, group2)
                else:
                    es_result = {'effect_size': None, 'interpretation': None}

                result = {
                    'variable': compare_var,
                    'groupby': groupby_var,
                    'groups': groups,
                    'method': selected_method,
                    'method_cn': _get_method_cn(selected_method),
                    'statistics': {
                        'statistic': test_result.get('statistic'),
                        'df': test_result.get('df'),
                        'p_raw': test_result.get('p_value'),
                        'effect_size': es_result.get('effect_size'),
                        'effect_interpretation': es_result.get('interpretation')
                    },
                    'normality': normality_results,
                    'variance_homogeneity': variance_result,
                    'n': {g: len(groups_data[g]) for g in groups}
                }

            elif selected_method in ['mann_whitney_u', 'wilcoxon', 'kruskal_wallis', 'friedman']:
                # 非参数检验
                group_values = [groups_data[g] for g in groups]

                if selected_method == 'mann_whitney_u':
                    test_result = nonparam_tests.mann_whitney_u(group_values[0], group_values[1])
                    es_result = effect_size_calc.rank_biserial(group_values[0], group_values[1])
                elif selected_method == 'wilcoxon':
                    test_result = nonparam_tests.wilcoxon_signed_rank(group_values[0], group_values[1])
                    es_result = {'effect_size': None, 'interpretation': None}
                elif selected_method == 'kruskal_wallis':
                    test_result = nonparam_tests.kruskal_wallis(*group_values)
                    es_result = {'effect_size': None, 'interpretation': None}
                else:  # friedman
                    test_result = nonparam_tests.friedman_test(*group_values)
                    es_result = {'effect_size': None, 'interpretation': None}

                result = {
                    'variable': compare_var,
                    'groupby': groupby_var,
                    'groups': groups,
                    'method': selected_method,
                    'method_cn': _get_method_cn(selected_method),
                    'statistics': {
                        'statistic': test_result.get('statistic'),
                        'df': test_result.get('df'),
                        'p_raw': test_result.get('p_value'),
                        'effect_size': es_result.get('effect_size'),
                        'effect_interpretation': es_result.get('interpretation')
                    },
                    'normality': normality_results,
                    'variance_homogeneity': variance_result,
                    'n': {g: len(groups_data[g]) for g in groups}
                }

            elif selected_method in ['chi_square', 'fisher_exact', 'mcnemar']:
                # 率比较
                # 构建列联表
                contingency_data = {}
                for i, group in enumerate(groups):
                    group_data = groups_data[group]
                    positive = sum(1 for x in group_data if x in [1, '1', 'yes', 'Yes', True, 'true'])
                    negative = len(group_data) - positive
                    contingency_data[group] = [negative, positive]

                import pandas as pd
                contingency_table = pd.DataFrame(contingency_data,
                                               index=['negative', 'positive']).T

                if selected_method == 'chi_square':
                    test_result = proportions_tests.chi_square_test(contingency_table)
                elif selected_method == 'fisher_exact':
                    test_result = proportions_tests.fisher_exact_test(contingency_table)
                else:  # mcnemar
                    test_result = proportions_tests.mcnemar_test(contingency_table)

                # 计算Cramer's V（统计量为 None 时跳过效应量，避免崩溃）
                chi2_stat = test_result.get('statistic')
                n_total = int(contingency_table.values.sum())
                min_dim = min(contingency_table.shape) - 1
                if chi2_stat is None:
                    es_result = {'effect_size': None, 'interpretation': '统计量缺失，未计算效应量'}
                else:
                    es_result = effect_size_calc.cramers_v(chi2_stat, n_total, min_dim)

                result = {
                    'variable': compare_var,
                    'groupby': groupby_var,
                    'groups': groups,
                    'method': selected_method,
                    'method_cn': _get_method_cn(selected_method),
                    'statistics': {
                        'statistic': test_result.get('statistic'),
                        'df': test_result.get('df'),
                        'p_raw': test_result.get('p_value'),
                        'effect_size': es_result.get('effect_size'),
                        'effect_interpretation': es_result.get('interpretation')
                    },
                    'contingency_table': contingency_table.to_dict(),
                    'n': {g: len(groups_data[g]) for g in groups}
                }

            elif selected_method == 'oneway_anova':
                # ANOVA
                group_values = [groups_data[g] for g in groups]
                test_result = param_tests.oneway_anova(*group_values)

                # 计算η²
                f_stat = test_result.get('statistic', 0)
                df_between = len(groups) - 1
                df_within = total_n - len(groups)
                es_result = effect_size_calc.eta_squared(f_stat, df_between, df_within)

                result = {
                    'variable': compare_var,
                    'groupby': groupby_var,
                    'groups': groups,
                    'method': selected_method,
                    'method_cn': _get_method_cn(selected_method),
                    'statistics': {
                        'statistic': test_result.get('statistic'),
                        'df': (df_between, df_within),
                        'p_raw': test_result.get('p_value'),
                        'effect_size': es_result.get('effect_size'),
                        'effect_interpretation': es_result.get('interpretation')
                    },
                    'normality': normality_results,
                    'variance_homogeneity': variance_result,
                    'n': {g: len(groups_data[g]) for g in groups}
                }

            else:
                print(f"警告: 未知方法 '{selected_method}'，跳过")
                continue

            # 收集p值用于多重比较校正
            all_p_values.append({
                'variable': compare_var,
                'groupby': groupby_var,
                'p_raw': result['statistics']['p_raw']
            })

            all_results.append(result)

    # 多重比较校正
    if len(all_p_values) > 1 and args.correction_method != 'None':
        p_raw_list = [p['p_raw'] for p in all_p_values]
        if args.correction_method == 'Bonferroni':
            p_adjusted_list = corrector.bonferroni_correction(p_raw_list)
        else:  # BH
            p_adjusted_list = corrector.benjamini_hochberg(p_raw_list, fdr=0.05)

        # 更新结果
        for i, result in enumerate(all_results):
            result['statistics']['p_adjusted'] = p_adjusted_list[i]
            result['statistics']['correction_method'] = args.correction_method

    # 格式化最终结果
    final_results = formatter.format_results(
        results=all_results,
        correction_method=args.correction_method if len(all_p_values) > 1 else 'None',
        alpha=args.alpha
    )

    def _pval_of(r):
        p = r['statistics'].get('p_adjusted')
        if p is None:
            p = r['statistics'].get('p_raw')
        return p if p is not None else 1.0

    # 添加汇总信息
    final_results['analysis_summary'] = {
        'total_tests': len(all_results),
        'significant_tests': sum(1 for r in all_results if _pval_of(r) < args.alpha),
        'correction_method': args.correction_method if len(all_p_values) > 1 else 'None'
    }

    # 保存结果
    save_results(final_results, args.session_id)

    # 打印汇总
    print("\n" + "=" * 60)
    print("分析完成!")
    print("=" * 60)
    print(f"总检验数: {final_results['analysis_summary']['total_tests']}")
    print(f"显著检验数: {final_results['analysis_summary']['significant_tests']}")
    if len(all_p_values) > 1:
        print(f"多重比较校正方法: {args.correction_method}")

    return final_results


def _get_method_cn(method: str) -> str:
    """获取方法的中文名称"""
    method_cn_map = {
        'independent_ttest': '独立样本t检验',
        'welch_ttest': 'Welch校正t检验',
        'paired_ttest': '配对t检验',
        'mann_whitney_u': 'Mann-Whitney U检验',
        'wilcoxon': 'Wilcoxon符号秩检验',
        'oneway_anova': '单因素ANOVA',
        'kruskal_wallis': 'Kruskal-Wallis检验',
        'friedman': 'Friedman检验',
        'chi_square': '卡方检验',
        'fisher_exact': 'Fisher精确检验',
        'mcnemar': 'McNemar检验'
    }
    return method_cn_map.get(method, method)


if __name__ == '__main__':
    main()