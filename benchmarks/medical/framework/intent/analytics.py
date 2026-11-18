"""Executable personal-record tasks inspired by public benchmark taxonomies."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TASKS = ("conditional_mean", "lagged_comparison", "paired_coverage", "correlation",
         "trimmed_comparison", "longest_streak", "observed_weighted_mean", "personal_baseline")
REFERENCES = {
    "PHIA": "https://arxiv.org/abs/2406.06464",
    "LifeAgentBench": "https://arxiv.org/abs/2601.13880",
    "WearableQA": "https://arxiv.org/abs/2609.05405",
    "HealthBench": "https://openai.com/index/healthbench/",
}


def daily(case: dict) -> dict[str, dict[str, float]]:
    values = defaultdict(lambda: defaultdict(list))
    timezone = ZoneInfo(case["window"].get("timezone", "UTC"))
    for e in case["evidence"]:
        if e["visible"] and e["kind"] == "device_reading":
            row = e["data"]
            day = datetime.fromisoformat(row["time"]).astimezone(timezone).date().isoformat()
            if case["window"]["start"] <= day <= case["window"]["end"]:
                values[row["metric"]][day].append(float(row["value"]))
    return {metric: {day: statistics.mean(v) for day, v in days.items()} for metric, days in values.items()}


def compute(case: dict) -> dict[str, tuple[float | str, str, str]]:
    data, spec = daily(case), case["analysis"]
    kind = spec["kind"]
    start = date.fromisoformat(case["window"]["start"])
    split = (start + timedelta(days=14)).isoformat()
    output = {}

    def number(name, value, unit):
        output[name] = (float(value), unit, "number")

    def text(name, value, result_kind="text"):
        output[name] = (value, "", result_kind)

    if kind == "conditional_mean":
        days = sorted(set(data["steps"]) & set(data["rhr"]))
        selected = [d for d in days if data["steps"][d] >= spec["step_threshold"]]
        if not selected:
            raise ValueError("Conditional group is empty")
        number("qualifying_days", len(selected), "")
        number("conditional_rhr_mean", statistics.mean(data["rhr"][d] for d in selected), "bpm")
    elif kind == "lagged_comparison":
        groups = {True: [], False: []}
        for day, steps in data["steps"].items():
            following = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            if following in data["rhr"]:
                groups[steps >= spec["step_threshold"]].append(data["rhr"][following])
        if not all(groups.values()):
            raise ValueError("Both lagged groups need paired observations")
        high, low = statistics.mean(groups[True]), statistics.mean(groups[False])
        number("next_day_high_activity_mean", high, "bpm")
        number("next_day_low_activity_mean", low, "bpm")
        number("high_activity_pairs", len(groups[True]), "")
        number("low_activity_pairs", len(groups[False]), "")
        text("higher_rhr_group", "high_activity" if high > low else "low_activity" if low > high else "equal")
    elif kind == "paired_coverage":
        paired = set(data["steps"]) & set(data["weight"])
        total = (date.fromisoformat(case["window"]["end"]) - start).days + 1
        number("complete_pair_days", len(paired), "")
        number("calendar_days_without_a_pair", total - len(paired), "")
        if not paired:
            raise ValueError("This task requires at least one complete pair")
        number("paired_day_mean_steps", statistics.mean(data["steps"][d] for d in paired), "count")
    elif kind == "correlation":
        paired = sorted(set(data["steps"]) & set(data["rhr"]))
        if len(paired) < 3:
            raise ValueError("A correlation requires at least three complete pairs")
        r = statistics.correlation([data["steps"][d] for d in paired], [data["rhr"][d] for d in paired])
        number("pearson_r", r, "")
        number("complete_pairs", len(paired), "")
    elif kind == "trimmed_comparison":
        excluded = sorted(data["steps"], key=lambda d: (-data["steps"][d], d))[:2]
        first = [v for d, v in data["steps"].items() if d < split and d not in excluded]
        second = [v for d, v in data["steps"].items() if d >= split and d not in excluded]
        if not first or not second:
            raise ValueError("Both trimmed windows need observations")
        number("trimmed_first_mean", statistics.mean(first), "count")
        number("trimmed_second_mean", statistics.mean(second), "count")
        for index, day in enumerate(sorted(excluded), 1):
            text(f"excluded_date_{index}", day, "date")
    elif kind == "longest_streak":
        best, current = [], []
        for day in sorted(data["steps"]):
            if data["steps"][day] < spec["step_threshold"]:
                current = []
                continue
            if current and (date.fromisoformat(day) - date.fromisoformat(current[-1])).days != 1:
                current = []
            current.append(day)
            if len(current) > len(best):
                best = current[:]
        if not best:
            raise ValueError("No observed threshold streak")
        number("longest_streak_days", len(best), "")
        text("streak_start", best[0], "date")
        text("streak_end", best[-1], "date")
    elif kind == "observed_weighted_mean":
        values = data["weight"]
        number("observed_day_weight_mean", statistics.mean(values.values()), "kg")
        number("first_half_observed_days", sum(d < split for d in values), "")
        number("second_half_observed_days", sum(d >= split for d in values), "")
    elif kind == "personal_baseline":
        boundary = (date.fromisoformat(case["window"]["end"]) - timedelta(days=6)).isoformat()
        earlier = [v for d, v in data["rhr"].items() if d < boundary]
        recent = [v for d, v in data["rhr"].items() if d >= boundary]
        baseline, current = statistics.median(earlier), statistics.mean(recent)
        number("earlier_median_rhr", baseline, "bpm")
        number("recent_week_mean_rhr", current, "bpm")
        number("absolute_change", abs(current - baseline), "bpm")
        text("direction", "higher" if current > baseline else "lower" if current < baseline else "unchanged")
    else:
        raise ValueError("Unknown personal-record analysis")
    return output


PROMPTS = {
    "conditional_mean": ("过去四周，步数至少8000的那些天，静息心率平均多少？先按同一天配对，告诉我实际用了几天，不要拿全月心率平均代替。",
                         "For the last four weeks, what was my mean resting HR only on days with at least 8,000 steps? Join by day and report the number of qualifying complete days."),
    "lagged_comparison": ("我想给医生看：高步数日（至少8000步）之后一天的静息心率，和低步数日之后相比如何？分别给均值和有效配对天数，别把当天和次日搞混，也别推断因果。",
                          "Compare next-day resting HR after days with at least 8,000 steps versus lower-step days. Give both means and pair counts, identify the higher group, and do not claim causation."),
    "paired_coverage": ("先别解读我的步数和体重关系。这四周有多少天两项都记录了，多少个日历日不能配对？只用完整配对日，步数均值是多少？",
                        "Before interpreting steps versus weight, count complete paired days and calendar days without a pair. What is the mean step count on complete paired days only?"),
    "correlation": ("这四周静息心率和步数在同一天的皮尔逊相关系数是多少？报出有效配对数，缺一项的日子排除。相关性能证明多走路导致心率变化吗？",
                    "Compute Pearson correlation between same-day steps and resting HR over these four weeks. Report complete-pair count, exclude missing pairs, and separate association from causation."),
    "trimmed_comparison": ("最近两次活动量特别大的日子可能拉高均值。请去掉这四周里步数最高的两天，再分别比较前14天、后14天的日均步数，并列出排除日期。",
                           "Exclude the two highest-step days in these four weeks, then give the observed daily mean for the first and second 14-day halves and the two excluded dates."),
    "longest_streak": ("这四周我最长连续多少天达到8000步？告诉我起止日期。没有记录的日子要中断连续性；并列时取最早的一段。",
                       "What was my longest consecutive streak of at least 8,000 steps in these four weeks? Give start/end dates; missing days break a streak, and ties use the earliest interval."),
    "observed_weighted_mean": ("这四周称重次数不均匀，帮我算按实际有记录的天数加权后的整体平均体重，再报前后两个14天各有几天记录。别直接平均两段的均值。",
                               "Weigh-ins are uneven. Compute the overall mean over observed daily weights, and count observed days in each 14-day half. Do not average the two half-window means equally."),
    "personal_baseline": ("准备复诊：以前三周静息心率的中位数作为我的个人基线，最近一周均值偏了多少、方向怎样？把基线、近期均值和差值分清，不套人群正常范围。",
                          "For my appointment, use the earlier three weeks' median resting HR as my personal baseline. Give the final week's mean, absolute difference and direction, without substituting population ranges."),
}
