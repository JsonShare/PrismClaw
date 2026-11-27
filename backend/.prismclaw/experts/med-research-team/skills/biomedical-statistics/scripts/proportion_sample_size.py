"""Normal-approximation planning size for one population proportion, not power."""
import argparse
import json
import math
from statistics import NormalDist


def calculate(proportion, absolute_margin, alpha):
    values = (proportion, absolute_margin, alpha)
    if any(not math.isfinite(value) or not 0 < value < 1 for value in values):
        raise ValueError('p, absolute_margin and alpha must be finite numbers strictly between 0 and 1')
    probability = 1 - alpha / 2
    if probability == 1:
        raise ValueError('alpha is too small for the supported numeric precision')
    denominator = absolute_margin ** 2
    if denominator == 0:
        raise ValueError('absolute_margin is too small for the supported numeric precision')
    quantile = NormalDist().inv_cdf(probability)
    raw = quantile ** 2 * proportion * (1 - proportion) / denominator
    if not math.isfinite(raw):
        raise ValueError('sample size is outside the supported numeric range')
    required = math.ceil(raw)
    return {
        'method': 'single_proportion_normal_approximation_precision',
        'formula': 'ceil(z_(1-alpha/2)^2 * p * (1-p) / absolute_margin^2)',
        'inputs': {'p': proportion, 'absolute_margin': absolute_margin, 'alpha': alpha},
        'z': quantile,
        'unrounded_n': raw,
        'required_n': required,
        'power': 'not specified; not calculated',
        'regression_sample_size': 'not calculated',
        'attrition_adjustment': 'not specified; not applied',
        'assumptions': ['independent observations', 'large-population simple random sampling approximation',
                        'absolute rather than relative margin', 'normal approximation, not exact coverage guarantee'],
        'approximation_warning': 'expected event or non-event count below 5; review approximation' if min(
            required * proportion, required * (1 - proportion)) < 5 else None,
    }


def render_report(result):
    inputs = result['inputs']
    lines = [
        '# 单率样本量估算', '',
        f"按本次给定参数，至少需要 **{result['required_n']} 例有效观测**。", '',
        '估计目标是单个总体发生率的绝对精度，采用正态近似精度法：', '',
        '`n = ceil(z_(1-alpha/2)^2 × p × (1-p) / d^2)`', '',
        f"参数：p={inputs['p']:.6g}，绝对容许误差d={inputs['absolute_margin']:.6g}，双侧alpha={inputs['alpha']:.6g}。",
        f"本次正态分位数z={result['z']:.12g}；未取整n={result['unrounded_n']:.12g}，向上取整为{result['required_n']}。", '',
        '这回答“发生率估计达到给定绝对精度需要多少有效观测”，不是两组差异的检验效能规划。', '',
        '适用前提是独立观测、大总体及简单随机抽样近似；该式为正态近似，不保证精确区间覆盖率。实际抽样方式须与研究方案核对。', '',
        '该数仅用于发生率精度，不能直接当作整个相关因素或多变量建模研究的最终样本量。未给定的失访、资料缺失、设计效应和建模参数未纳入；本次不补报这些部分的公式、例数或可容纳变量数。',
    ]
    if result['approximation_warning']:
        lines += ['', '近似警示：预期事件数或非事件数低于5，需另行评估该近似是否适用。']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p', type=float, required=True)
    parser.add_argument('--absolute-margin', type=float, required=True)
    parser.add_argument('--alpha', type=float, required=True)
    parser.add_argument('--format', choices=('json', 'markdown'), default='json')
    args = parser.parse_args()
    try:
        result = calculate(args.p, args.absolute_margin, args.alpha)
    except ValueError as error:
        parser.exit(2, f'{error}\n')
    if args.format == 'markdown':
        print(render_report(result), end='')
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
