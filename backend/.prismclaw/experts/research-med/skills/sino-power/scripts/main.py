#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
样本量计算主入口

功能：计算不同统计检验所需的样本量
用法：
    python scripts/main.py --session_id xxx --test_method two_sample_ttest --effect_size 0.5
"""
import argparse
import json
import sys
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from ttest_power import TTestPowerCalculator
from chi2_power import Chi2PowerCalculator
from logrank_power import LogRankPowerCalculator
from sensitivity_analysis import SensitivityAnalyzer
from effect_size_guide import EffectSizeGuide
from result_formatter import PowerResultFormatter


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
        output_file = Path.cwd() / f"session-{session_id}" / "power_analysis_result.md"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(results)

    print(f"结果已保存到: {output_file}")


def main():
    """主入口函数"""
    parser = argparse.ArgumentParser(description='样本量计算 - 统计效能分析')
    parser.add_argument('--session_id', type=str, required=True, help='会话ID')
    parser.add_argument('--test_method', type=str, required=True,
                        choices=['two_sample_ttest', 'chi_square', 'logrank'],
                        help='检验方法')
    parser.add_argument('--design_type', type=str, default='superiority',
                        choices=['superiority', 'equivalence', 'non_inferiority'],
                        help='研究设计类型')
    parser.add_argument('--effect_size', type=float, required=True,
                        help='效应量（Cohen\'s d for ttest, Cohen\'s w for chi-square, HR for logrank）')
    parser.add_argument('--alpha', type=float, default=0.05, help='显著性水平（双侧）')
    parser.add_argument('--power', type=float, default=0.8, help='统计效能（1-β）')
    parser.add_argument('--group_ratio', type=str, default='1:1',
                        help='分组比例，如"1:1"、"2:1"')
    parser.add_argument('--loss_to_followup', type=float, default=0.15,
                        help='预期失访率（0-1之间）')
    parser.add_argument('--expected_event_rate', type=float, default=None,
                        help='预期事件率（用于Log-rank检验）')
    parser.add_argument('--accrual_time', type=float, default=None,
                        help='入组时间（年，用于Log-rank检验）')
    parser.add_argument('--follow_up_time', type=float, default=None,
                        help='随访时间（年，用于Log-rank检验）')
    parser.add_argument('--effect_size_type', type=str, default='medium',
                        choices=['small', 'medium', 'large', 'custom'],
                        help='效应量预设类型（仅用于显示）')
    parser.add_argument('--effect_size_basis', type=str, default=None,
                        help='效应量依据说明')
    parser.add_argument('--output_path', type=str, default=None,
                        help='输出 Markdown 路径；缺省写入 <cwd>/session-<id>/power_analysis_result.md')

    args = parser.parse_args()

    # 解析分组比例
    ratio_parts = args.group_ratio.split(':')
    n1_ratio = int(ratio_parts[0])
    n2_ratio = int(ratio_parts[1]) if len(ratio_parts) > 1 else int(ratio_parts[0])
    total_ratio = n1_ratio + n2_ratio

    print("=" * 60)
    print("样本量计算开始")
    print("=" * 60)
    print(f"会话ID: {args.session_id}")
    print(f"检验方法: {args.test_method}")
    print(f"设计类型: {args.design_type}")
    print(f"效应量: {args.effect_size}")
    print(f"显著性水平(α): {args.alpha}")
    print(f"统计效能(1-β): {args.power}")
    print(f"分组比例: {args.group_ratio}")
    print(f"失访率: {args.loss_to_followup*100}%")

    # 根据检验方法选择计算器
    if args.test_method == 'two_sample_ttest':
        calculator = TTestPowerCalculator(
            alpha=args.alpha,
            power=args.power,
            effect_size=args.effect_size,
            design_type=args.design_type
        )
        result = calculator.calculate(n1_ratio=n1_ratio, n2_ratio=n2_ratio)

    elif args.test_method == 'chi_square':
        calculator = Chi2PowerCalculator(
            alpha=args.alpha,
            power=args.power,
            effect_size=args.effect_size,
            design_type=args.design_type
        )
        result = calculator.calculate(n1_ratio=n1_ratio, n2_ratio=n2_ratio)

    elif args.test_method == 'logrank':
        calculator = LogRankPowerCalculator(
            alpha=args.alpha,
            power=args.power,
            effect_size=args.effect_size,
            design_type=args.design_type,
            expected_event_rate=args.expected_event_rate,
            accrual_time=args.accrual_time,
            follow_up_time=args.follow_up_time
        )
        result = calculator.calculate()

    else:
        print(f"错误: 未知检验方法 '{args.test_method}'")
        return None

    # 计算失访补偿后的样本量
    loss_rate = args.loss_to_followup
    n1_per_group = result['n_per_group']
    n2_per_group = result['n_per_group_2']

    n1_adjusted = int(n1_per_group / (1 - loss_rate)) if (1 - loss_rate) > 0 else n1_per_group
    n2_adjusted = int(n2_per_group / (1 - loss_rate)) if (1 - loss_rate) > 0 else n2_per_group

    result['n_per_group_adjusted'] = n1_adjusted
    result['n_per_group_2_adjusted'] = n2_adjusted
    result['total_n_adjusted'] = n1_adjusted + n2_adjusted
    result['loss_rate'] = loss_rate

    # 添加效应量信息
    result['effect_size_type'] = args.effect_size_type
    result['effect_size_basis'] = args.effect_size_basis or "用户指定"

    # 敏感性分析
    analyzer = SensitivityAnalyzer(
        test_method=args.test_method,
        alpha=args.alpha,
        power=args.power,
        design_type=args.design_type,
        loss_rate=loss_rate
    )

    sensitivity = analyzer.run_sensitivity_analysis()
    result['sensitivity_analysis'] = sensitivity

    # 生成效应量参考表
    es_guide = EffectSizeGuide()
    effect_size_reference = es_guide.get_reference_table(args.test_method)

    # 格式化结果
    formatter = PowerResultFormatter()
    output = formatter.format_result(
        result=result,
        effect_size_reference=effect_size_reference,
        test_method=args.test_method
    )

    # 保存结果
    save_results(output, args.session_id, args.output_path)

    # 打印汇总
    print("\n" + "=" * 60)
    print("样本量计算完成!")
    print("=" * 60)
    print(f"\n【结果汇总】")
    print(f"每组最小样本量: {n1_per_group} : {n2_per_group}")
    print(f"总样本量(不含失访): {result['total_n']}")
    print(f"\n考虑{loss_rate*100}%失访率后:")
    print(f"每组需要: {n1_adjusted} : {n2_adjusted}")
    print(f"总样本量: {result['total_n_adjusted']}")

    print(f"\n【敏感性分析摘要】")
    print(f"效应量小(d=0.2): 每组{sensitivity['small']['n_per_group']}例, 总{sensitivity['small']['total_n_adjusted']}例")
    print(f"效应量中(d=0.5): 每组{sensitivity['medium']['n_per_group']}例, 总{sensitivity['medium']['total_n_adjusted']}例")
    print(f"效应量大(d=0.8): 每组{sensitivity['large']['n_per_group']}例, 总{sensitivity['large']['total_n_adjusted']}例")

    return result


if __name__ == '__main__':
    main()