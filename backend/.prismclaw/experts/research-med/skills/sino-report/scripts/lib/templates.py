"""
shared/templates/__init__.py - Plain Language解释模板
提供统计结果的通俗语言解释模板
"""

__version__ = "1.0.0"

from typing import Dict, Any, Optional


# t检验结果解释模板
TTEST_INTERPRETATION = """
{group1_name}的平均{var_name}（{mean1} ± {sd1}）比{group2_name}（{mean2} ± {sd2}）{direction}了约{diff}。

这个差异在统计学上是"显著的"（{p_value_formatted}），意味着这种差异{difficulty_explanation}。

通俗来说：{confidence_level}的把握认为{conclusion}，
撇开运气成分，这个{direction_text}是真实存在的。

{direction_explanation}
"""

# 卡方检验结果解释模板
CHI2_INTERPRETATION = """
{group1_name}的{event_name}发生率（{rate1}%）比{group2_name}（{rate2}%）{direction}了约{diff}个百分点。
这个差异有统计学意义（{p_value_formatted}）。

通俗来说：{confidence_statement}，
{conclusion}.

{clinical_note}
"""

# 相关分析解释模板
CORRELATION_INTERPRETATION = """
{var1}和{var2}之间存在{direction}关联（r={r_value}, p={p_value}）。

{direction_explanation}

通俗理解：{plain_explanation}

{strength_explanation}
"""


def interpret_ttest_result(
    result: Dict[str, Any],
    group1_name: str = "组1",
    group2_name: str = "组2",
    var_name: str = "指标"
) -> str:
    """
    生成t检验结果的通俗解释

    Args:
        result: t检验结果字典
        group1_name: 第一组名称
        group2_name: 第二组名称
        var_name: 变量名称

    Returns:
        通俗解释文本
    """
    mean1 = result.get("mean1", 0)
    mean2 = result.get("mean2", 0)
    sd1 = result.get("sd1", 0)
    sd2 = result.get("sd2", 0)
    diff = abs(mean1 - mean2)
    p_value = result.get("p_value", 1)
    effect_size = result.get("effect_size", 0)

    direction = "高" if mean1 > mean2 else "低"

    # p值格式化
    if p_value < 0.001:
        p_value_formatted = "p<0.001"
    else:
        p_value_formatted = f"p={p_value:.3f}"

    # 置信度
    confidence = 100 * (1 - p_value)
    confidence_level = f"{confidence:.1f}%"

    # 差异是否可能是巧合
    if p_value < 0.01:
        difficulty_explanation = "不太可能是巧合造成的"
    elif p_value < 0.05:
        difficulty_explanation = "不太可能是单纯运气造成的"
    else:
        difficulty_explanation = "可能有一定的巧合成分"

    # 结论
    if effect_size > 0.8:
        effect_text = "差异明显"
    elif effect_size > 0.5:
        effect_text = "差异较为明显"
    elif effect_size > 0.2:
        effect_text = "差异存在"
    else:
        effect_text = "差异较小"

    conclusion = f"{group1_name}确实比{group2_name}{effect_text}"

    return f"""
{group1_name}的平均{var_name}（{mean1:.1f} ± {sd1:.1f}）比{group2_name}（{mean2:.1f} ± {sd2:.1f}）{direction}了约{diff:.1f}。

这个差异在统计学上是"显著的"（{p_value_formatted}），意味着这种差异{difficulty_explanation}。

通俗来说：我们有{confidence_level}的把握认为{conclusion}，撇开运气成分，这个{direction}是真实存在的。
"""


def interpret_chi2_result(
    result: Dict[str, Any],
    group1_name: str = "组1",
    group2_name: str = "组2",
    event_name: str = "事件"
) -> str:
    """
    生成卡方检验结果的通俗解释

    Args:
        result: 卡方检验结果字典
        group1_name: 第一组名称
        group2_name: 第二组名称
        event_name: 事件名称

    Returns:
        通俗解释文本
    """
    rate1 = result.get("rate1", 0)
    rate2 = result.get("rate2", 0)
    p_value = result.get("p_value", 1)
    effect_size = result.get("effect_size", 0)

    direction = "高" if rate1 > rate2 else "低"
    diff = abs(rate1 - rate2)

    # p值格式化
    if p_value < 0.001:
        p_value_formatted = "p<0.001"
    else:
        p_value_formatted = f"p={p_value:.3f}"

    confidence_statement = f"我们有{100*(1-p_value):.1f}%的把握认为{group1_name}确实比{group2_name}更容易发生{event_name}" if p_value < 0.05 else "虽然存在差异，但统计学意义不强"

    return f"""
{group1_name}的{event_name}发生率（{rate1:.1f}%）比{group2_name}（{rate2:.1f}%）{direction}了约{diff:.1f}个百分点。
这个差异有统计学意义（{p_value_formatted}）。

通俗来说：{confidence_statement}。
"""


def interpret_correlation(
    r: float,
    p_value: float,
    var1: str = "变量1",
    var2: str = "变量2"
) -> str:
    """
    生成相关分析结果的通俗解释

    Args:
        r: 相关系数
        p_value: p值
        var1: 变量1名称
        var2: 变量2名称

    Returns:
        通俗解释文本
    """
    # 方向
    direction = "正" if r > 0 else "负"

    # 强度
    r_abs = abs(r)
    if r_abs < 0.1:
        strength = "很弱"
    elif r_abs < 0.3:
        strength = "弱"
    elif r_abs < 0.5:
        strength = "中等"
    elif r_abs < 0.7:
        strength = "较强"
    else:
        strength = "强"

    # p值
    if p_value < 0.001:
        p_text = "p<0.001"
    else:
        p_text = f"p={p_value:.3f}"

    return f"""
{var1}和{var2}之间存在{direction}向{strength}关联（r={r:.2f}, {p_text}）。

这意味着：当{var1}增加时，{var2}倾向于{('增加' if r > 0 else '减少')}。

但请注意：相关不等于因果，两者有关联可能是因为：
1. 一个变量直接影响另一个
2. 有第三个变量同时影响两者
3. 纯属巧合
"""
