"""Generate mutually exclusive AND-condition branches, without clinical thresholds."""
import argparse
import json
from pathlib import Path
import sys


STATES = {'below', 'within', 'above', 'unknown'}
LABELS = {'below': '低于适用界值', 'within': '适用区间内', 'above': '高于适用界值', 'unknown': '无法判定'}
BRANCHES = {'unknown': '资料不足', 'all': '完整命中', 'partial': '部分命中', 'none': '未命中'}


def conditions(plan):
    if not isinstance(plan, dict) or set(plan) != {'conditions'}:
        raise ValueError('Only an explicit conditions list is accepted')
    rows = plan['conditions']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise ValueError('Supply one through eight conditions')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'name', 'target', 'role'} or not {'name', 'target'} <= set(row):
            raise ValueError('Each condition requires name and target')
        name = row['name']
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(c in name for c in '|\n\r'):
            raise ValueError('Condition names must be short single-line text')
        if name in seen or not isinstance(row['target'], str) or row['target'] not in {'below', 'above'}:
            raise ValueError('Condition names must be unique and targets below/above')
        if row.get('role', 'core') not in {'core', 'auxiliary', 'alert'}:
            raise ValueError('Condition role must be core, auxiliary, or alert')
        seen.add(name)
    if not any(row.get('role', 'core') == 'core' for row in rows):
        raise ValueError('At least one core condition is required')
    return rows


def classify(rows, states):
    if len(states) != len(rows) or any(s not in STATES for s in states):
        raise ValueError('Every condition needs exactly one valid state')
    core = [(row, state) for row, state in zip(rows, states) if row.get('role', 'core') == 'core']
    if not core:
        raise ValueError('At least one core condition is required')
    if any(state == 'unknown' for _, state in core):
        branch = 'unknown'
    else:
        matches = sum(state == row['target'] for row, state in core)
        branch = 'all' if matches == len(core) else 'partial' if matches else 'none'
    return {'branch': branch, 'allWithinRange': all(s == 'within' for s in states),
            'missingAuxiliary': [row['name'] for row, state in zip(rows, states)
                                 if row.get('role') == 'auxiliary' and state == 'unknown'],
            'alertMatches': [row['name'] for row, state in zip(rows, states)
                             if row.get('role') == 'alert' and state == row['target']]}


def generate(plan):
    rows = conditions(plan)
    target = [row['target'] for row in rows]
    first_core = next(index for index, row in enumerate(rows) if row.get('role', 'core') == 'core')
    missing_core = target.copy()
    missing_core[first_core] = 'unknown'
    examples = [target, missing_core, ['within'] * len(rows)]
    if any(row.get('role', 'core') != 'core' for row in rows):
        examples.append([row['target'] if row.get('role', 'core') == 'core' else 'unknown' for row in rows])
    if len(rows) > 1:
        partial = ['within'] * len(rows)
        partial[first_core] = target[first_core]
        examples.append(partial)
    opposite = ['above' if row['target'] == 'below' else 'below' for row in rows]
    examples.append(opposite)
    results = [{'states': states, **classify(rows, states)} for states in examples]
    lines = ['## 组合条件判定', '',
             '仅核心条件按AND组合。核心条件任一无法判定→资料不足；全部可判定且全部命中→完整命中；命中部分→部分命中；零命中→未命中。', '',
             '“未命中”不等于“各项正常”。只有所有字段均处于各自适用区间内，才可报告所列字段均在区间内。该状态不等于患者整体无异常。', '',
             '危险线索先行提示，随后仍保留上述主分支；危险提示不把未知条件改成已命中。病因分析的可选字段缺失不改变已经确定的目标组合状态。', '',
             '核心条件：' + '、'.join(row['name'] for row in rows if row.get('role', 'core') == 'core') + '。', '',
             '辅助字段（不参与主体AND）：' + ('、'.join(row['name'] for row in rows if row.get('role') == 'auxiliary') or '本方案未列') + '。', '',
             '危险提示字段（独立提示，不改写主分支）：' + ('、'.join(row['name'] for row in rows if row.get('role') == 'alert') or '本方案未列') + '。', '',
             '## 由同一判定函数生成的走读表', '',
             '| ' + ' | '.join(row['name'] for row in rows) + ' | 主分支 | 所列字段均在区间内 |',
             '| ' + ' | '.join(['---'] * (len(rows) + 2)) + ' |']
    for result in results:
        lines.append('| ' + ' | '.join(LABELS[s] for s in result['states']) + ' | '
                     + BRANCHES[result['branch']] + ' | ' + ('是' if result['allWithinRange'] else '否') + ' |')
    lines.extend(['', '状态由已核且适用的界值、单位、方法和人群解释后输入；缺来源或不匹配必须为无法判定。程序不提供临床阈值，不证明诊断或风险已排除。'])
    return '\n'.join(lines), {'conditions': rows, 'examples': results, 'scope': 'logical consistency only; no patient evaluation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--format', choices=['markdown', 'json'], default='markdown')
    args = parser.parse_args()
    if args.plan.stat().st_size > 65536:
        raise ValueError('Plan too large')
    markdown, data = generate(json.loads(args.plan.read_text()))
    print(markdown if args.format == 'markdown' else json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('Condition table stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
