import itertools
import math
import statistics

import scipy
from scipy import stats


def denominator_df(values, equal_var):
    if equal_var:
        return sum(map(len, values)) - len(values)
    variances = [statistics.variance(group) for group in values]
    if any(not math.isfinite(variance) or variance <= 0 for variance in variances):
        raise ValueError('Welch ANOVA requires finite positive variance in every group')
    weights = [len(group) / variance for group, variance in zip(values, variances)]
    total = sum(weights)
    if not math.isfinite(total):
        raise ValueError('Welch ANOVA returned nonfinite variance weights')
    correction = sum((1 - weight / total) ** 2 / (len(group) - 1)
                     for group, weight in zip(values, weights))
    return (len(values) ** 2 - 1) / (3 * correction)


def one_way_anova(groups, plan):
    if scipy.__version__ != '1.17.1':
        raise ValueError('One-way ANOVA requires scipy==1.17.1')
    if plan['design'] != 'independent' or any(key[2] is not None for key in groups):
        raise ValueError('One-way ANOVA requires independent groups without repeated measurements')
    panels = list(dict.fromkeys(key[0] for key in groups))
    variance_model = plan.get('variance_model', 'unequal')
    if variance_model not in ('equal', 'unequal'):
        raise ValueError('variance_model must be equal or unequal')
    alpha = float(plan.get('alpha', .05))
    if not 0 < alpha < 1:
        raise ValueError('Require 0<alpha<1')
    panel_models = dict.fromkeys(panels, variance_model)
    if 'variance_model_by_panel' in plan:
        panel_models = plan['variance_model_by_panel']
        if 'variance_model' in plan:
            raise ValueError('Choose either variance_model or variance_model_by_panel')
        if (not isinstance(panel_models, dict) or set(panel_models) != set(panels)
                or any(model not in ('equal', 'unequal') for model in panel_models.values())):
            raise ValueError('variance_model_by_panel must assign equal or unequal to every data panel')
    omnibus = []
    comparisons = []
    for panel in panels:
        model = panel_models[panel]
        equal_var = model == 'equal'
        method = 'One-way ANOVA' if equal_var else 'Welch one-way ANOVA'
        posthoc = 'Tukey' if equal_var else 'Games-Howell'
        names = [key[1] for key in groups if key[0] == panel]
        if not 2 <= len(names) <= 20:
            raise ValueError('One-way ANOVA supports 2 through 20 groups per panel')
        values = [[row['value'] for row in groups[(panel, name, None)]] for name in names]
        df_denominator = denominator_df(values, equal_var)
        overall = stats.f_oneway(*values, equal_var=equal_var, nan_policy='raise')
        if not all(math.isfinite(value) for value in (overall.statistic, overall.pvalue)):
            raise ValueError('ANOVA returned a nonfinite statistic or P value')
        omnibus.append({'panel': panel, 'method': method, 'variance_model': model,
                        'group_count': len(names), 'n': sum(map(len, values)),
                        'df_numerator': len(names) - 1, 'df_denominator': df_denominator,
                        'statistic': float(overall.statistic), 'p_value': float(overall.pvalue)})
        pairwise = stats.tukey_hsd(*values, equal_var=equal_var)
        interval = pairwise.confidence_interval(1 - alpha)
        for i, j in itertools.combinations(range(len(names)), 2):
            difference = float(pairwise.statistic[i, j])
            adjusted = float(pairwise.pvalue[i, j])
            low = float(interval.low[i, j])
            high = float(interval.high[i, j])
            if not all(math.isfinite(value) for value in (difference, adjusted, low, high)):
                raise ValueError('Multiple comparisons returned a nonfinite effect, P value or interval')
            comparisons.append({
                'panel': panel, 'a': names[i], 'b': names[j],
                'n_a': len(values[i]), 'n_b': len(values[j]),
                'mean_difference_a_minus_b': difference, 'statistic': difference, 'df': None,
                'p_value': None, 'p_adjusted': adjusted, 'ci_low': low, 'ci_high': high,
                'ci_scope': 'simultaneous ' + posthoc,
                'correction': posthoc + ' within panel',
            })
    models = set(panel_models.values())
    return {'variance_model': next(iter(models)) if len(models) == 1 else 'mixed',
            'omnibus': omnibus, 'comparisons': comparisons}
