"""Display published RCT estimates without calculating a pooled effect."""
import argparse
import csv
import html
import importlib.metadata
import json
import math
import platform
import tempfile
import textwrap
from pathlib import Path

from _artifact_store import digest, publish, package_snapshot
from _delivery import render_delivery

VERSION = '1.1.1'
FIELDS = ['study_id', 'label', 'study_type', 'outcome', 'timepoint', 'population', 'unit',
          'measure', 'ci_level', 'estimate', 'ci_low', 'ci_high', 'source', 'location']
RATIOS = {'RR', 'aRR', 'OR', 'aOR', 'HR', 'aHR'}
MEASURES = RATIOS | {'RD', 'MD'}


def validate(data):
    if set(data) != {'studies'} or not isinstance(data['studies'], list) or not data['studies']:
        raise ValueError('Provide a non-empty studies list only; pooling is not supported')
    seen = set()
    for row in data['studies']:
        if set(row) != set(FIELDS):
            raise ValueError('Each study must supply exactly the documented fields')
        for field in set(FIELDS) - {'ci_level', 'estimate', 'ci_low', 'ci_high'}:
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(field + ' must be a non-empty string')
        if row['study_type'] != 'RCT':
            raise ValueError('Only individual RCT results are supported; exclude reviews and pooled/IPD records')
        if row['measure'] not in MEASURES:
            raise ValueError('Unsupported effect measure')
        for field in ['ci_level', 'estimate', 'ci_low', 'ci_high']:
            value = row[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(field + ' must be a finite number')
        if not 0 < row['ci_level'] < 1 or not row['ci_low'] <= row['estimate'] <= row['ci_high']:
            raise ValueError('Invalid reported confidence interval; do not derive missing intervals')
        if row['measure'] in RATIOS and row['ci_low'] <= 0:
            raise ValueError('Ratio estimates and confidence limits must be positive')
        identity = (row['study_id'].strip().casefold(), *panel_key(row))
        if identity in seen:
            raise ValueError('Duplicate trial in the same outcome/population/measure panel')
        seen.add(identity)
    return data['studies']


def panel_key(row):
    return tuple(row[field] for field in ['measure', 'outcome', 'timepoint', 'population', 'unit', 'ci_level'])


def render_report(studies):
    """Reuse the plotted records without inferring a pooled or clinical conclusion."""
    def text(value):
        return html.escape(str(value)).replace('\n', ' ')

    lines = ['# 逐研究已报告结果', '',
             '以下结果与同次森林图使用相同输入，逐项展示，不计算合并效应。',
             '不同结局、人群、时点和效应量类型分别保留；获益方向、研究身份及原文支持仍须核对。', '']
    for row in studies:
        lines += ['## ' + text(row['label']), '',
                  '- 研究身份：' + text(row['study_id']) + '（RCT）。',
                  '- 人群：' + text(row['population']) + '。',
                  '- 结局：' + text(row['outcome']) + '；时点：' + text(row['timepoint']) + '。',
                  '- 原文效应量：' + text(row['measure']) + ' ' + str(row['estimate'])
                  + '（' + str(row['ci_level'] * 100) + '% CI ' + str(row['ci_low'])
                  + ' 至 ' + str(row['ci_high']) + '）；单位：' + text(row['unit']) + '。',
                  '- 来源：' + text(row['source']) + '；定位：' + text(row['location']) + '。', '']
    return '\n'.join(lines) + '\n'


def plot(studies, destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import MaxNLocator, NullLocator, ScalarFormatter

    available = {font.name for font in font_manager.fontManager.ttflist}
    candidates = ['Arial Unicode MS', 'PingFang SC', 'Noto Sans CJK SC', 'DejaVu Sans']
    fonts = [font for font in candidates if font in available]
    with plt.rc_context({'font.family': fonts, 'svg.fonttype': 'none'}):
        panels = list(dict.fromkeys(panel_key(row) for row in studies))
        heights = [max(2.3, sum(panel_key(row) == key for row in studies) * .9 + 1.4) for key in panels]
        fig, axes = plt.subplots(len(panels), 1, figsize=(9, sum(heights)), squeeze=False,
                                 gridspec_kw={'height_ratios': heights})
        for ax, key in zip(axes.flat, panels):
            rows = [row for row in studies if panel_key(row) == key]
            positions = list(range(len(rows)))
            values = [row['estimate'] for row in rows]
            errors = [[row['estimate'] - row['ci_low'] for row in rows],
                      [row['ci_high'] - row['estimate'] for row in rows]]
            ax.errorbar(values, positions, xerr=errors, fmt='o', color='#24576c', capsize=4)
            ratio = key[0] in RATIOS
            ax.axvline(1 if ratio else 0, color='#777777', linewidth=.8, linestyle='--')
            if ratio:
                ax.set_xscale('log')
                lower, upper = ax.get_xlim()
                if upper / lower < 10:
                    ticks = MaxNLocator(nbins=4, min_n_ticks=3).tick_values(lower, upper)
                    ax.set_xticks(sorted({1, *(tick for tick in ticks if lower <= tick <= upper)}))
                    ax.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
                    ax.xaxis.set_minor_locator(NullLocator())
            labels = [textwrap.fill(row['label'], width=36) + '\n' +
                      f"{row['estimate']:g} [{row['ci_low']:g}, {row['ci_high']:g}]" for row in rows]
            ax.set_yticks(positions, labels)
            ax.set_ylim(len(rows) - .5, -.5)
            title = f'{key[1]} | {key[2]} | {key[3]}'
            ax.set_title(textwrap.fill(title, width=75), fontsize=10, pad=12)
            scale = ', log scale' if ratio else ''
            ax.set_xlabel(f'{key[0]}, {key[4]}{scale} ({100 * key[5]:g}% CI); published estimates, no pooling')
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='x', alpha=.2)
        fig.tight_layout(h_pad=3)
        fig.savefig(destination / 'figure.svg', bbox_inches='tight')
        fig.savefig(destination / 'figure.tiff', dpi=300, bbox_inches='tight')
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out-root', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    studies = validate(json.loads(raw))
    requirements = Path(__file__).with_name('requirements.txt')
    if not requirements.exists():
        requirements = Path(__file__).resolve().parents[1] / 'requirements.txt'
    implementation = {'render_study_forest.py': Path(__file__),
                      '_artifact_store.py': Path(__file__).with_name('_artifact_store.py'),
                      '_delivery.py': Path(__file__).with_name('_delivery.py'),
                      'requirements.txt': requirements}
    with tempfile.TemporaryDirectory(prefix='study-forest-') as temp:
        stage = Path(temp)
        snapshot = stage / 'input.json'
        snapshot.write_bytes(raw)
        with (stage / 'effects.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(studies)
        plot(studies, stage)
        (stage / 'report.md').write_text(render_report(studies), encoding='utf-8')
        (stage / 'delivery.md').write_text(render_delivery(stage), encoding='utf-8')
        result = {'version': VERSION, 'mode': 'reported-rct-estimates-no-pooling', 'studies': studies,
                  'implementation': {name: digest(path) for name, path in implementation.items()},
                  'boundary': 'Source interpretation and trial identity require original-paper verification.'}
        (stage / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        environment = {'python': platform.python_version(),
                       'packages': {name: importlib.metadata.version(name) for name in ['matplotlib', 'numpy', 'Pillow']}}
        (stage / 'runtime-environment.json').write_text(json.dumps(environment, indent=2) + '\n')
        for name, path in implementation.items():
            (stage / name).write_bytes(path.read_bytes())
        replay = ['import argparse', 'import subprocess', 'import sys', 'from pathlib import Path',
                  'p = argparse.ArgumentParser()', "p.add_argument('--out-root', required=True)",
                  'args = p.parse_args()', 'root = Path(__file__).resolve().parent',
                  "subprocess.run([sys.executable, str(root / 'render_study_forest.py'),",
                  "                '--input', str(root / 'input.json'), '--out-root', args.out_root], check=True)"]
        (stage / 'reproduce.py').write_text('\n'.join(replay) + '\n')
        package_snapshot(stage)
        destination = publish(args.out_root, [snapshot], list(stage.iterdir()))
        manifest_path = destination / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['inputs'][0]['file'] = 'input.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        print(destination)


if __name__ == '__main__':
    main()
