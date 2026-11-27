"""Bounded descriptive statistics only. No inferential tests or dependency installs."""
import argparse, csv, html, io, json, math, platform, statistics, sys, tempfile
from pathlib import Path
from _artifact_store import publish, digest

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100000

def calculate(raw, plan):
    if not isinstance(plan, dict):
        raise ValueError('Plan must be a JSON object')
    required = {'design': 'independent', 'method': 'descriptive', 'error_bar': 'SD', 'missing': 'stop'}
    for k, v in required.items():
        if plan.get(k) != v:
            raise ValueError('Unsupported or unconfirmed plan field: ' + k)
    if not isinstance(plan.get('unit'), str) or not plan['unit'].strip():
        raise ValueError('Explicit measurement unit required')
    if len(raw) > MAX_BYTES:
        raise ValueError('Input exceeds size limit')
    rows = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
    fields = rows.fieldnames or []
    if len(fields) != len(set(fields)) or not {'group', 'value'}.issubset(fields):
        raise ValueError('Unique headers group and value required')
    groups, seen, count = {}, set(), 0
    for line, row in enumerate(rows, 2):
        count += 1
        if count > MAX_ROWS or None in row or any(v is None for v in row.values()):
            raise ValueError('Invalid CSV shape or row limit at line ' + str(line))
        g = row['group'].strip()
        if not g or not row['value'].strip():
            raise ValueError('Missing group/value at line ' + str(line))
        if 'id' in fields:
            ident = row['id'].strip()
            if not ident or (g, ident) in seen:
                raise ValueError('Missing/duplicate group-id at line ' + str(line))
            seen.add((g, ident))
        x = float(row['value'])
        if not math.isfinite(x):
            raise ValueError('Nonfinite value at line ' + str(line))
        groups.setdefault(g, []).append(x)
    if len(groups) > 50 or any(len(g) > 80 for g in groups):
        raise ValueError('Plot supports up to 50 groups with labels up to 80 characters')
    if not groups or any(len(v) < 2 for v in groups.values()):
        raise ValueError('Every group requires at least two observations')
    summary = []
    for g, v in sorted(groups.items()):
        mean, sd = statistics.mean(v), statistics.stdev(v)
        if not all(math.isfinite(x) for x in [mean, sd, mean-sd, mean+sd]):
            raise ValueError('Nonfinite derived value')
        summary.append({'group': g, 'n': len(v), 'mean': mean, 'sample_sd': sd, 'min': min(v), 'max': max(v)})
    return {'schema': 1, 'status': 'executed', 'validation_scope': 'descriptive-program-regression',
            'design': 'independent', 'method': 'descriptive', 'unit': plan['unit'],
            'error_bar': 'sample SD (n-1)', 'missing_policy': 'stop', 'cleaning': 'none',
            'inference': 'not performed', 'groups': summary,
            'runtime': {'executable': sys.executable, 'python': platform.python_version(), 'implementation': 'stdlib statistics'}}

def plot(result):
    groups = result['groups']
    low = min(0, min(g['mean']-g['sample_sd'] for g in groups))
    high = max(0, max(g['mean']+g['sample_sd'] for g in groups))
    span = high-low
    if not math.isfinite(span):
        raise ValueError('Plot range is nonfinite')
    if span == 0:
        span = 1
    def y(v):
        return 320 - (v-low)/span*260
    width = max(480, 130*len(groups)+100)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="420" viewBox="0 0 {width} 420">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#111">',
             '<text x="30" y="25">Descriptive means +/- sample SD; no inference</text>',
             f'<text x="30" y="398">Unit: {html.escape(result["unit"])}; SD uses n-1</text>']
    for i in range(6):
        v = low + span*i/5
        parts.append(f'<text x="5" y="{y(v)+4:.2f}">{v:.3g}</text><line x1="65" y1="{y(v):.2f}" x2="{width-20}" y2="{y(v):.2f}" stroke="#ddd"/>')
    for i, g in enumerate(groups):
        x = 120+i*(width-160)/max(1,len(groups)-1)
        a,b,c = y(g['mean']+g['sample_sd']), y(g['mean']-g['sample_sd']), y(g['mean'])
        parts.append(f'<path d="M{x},{a} V{b} M{x-10},{a} H{x+10} M{x-10},{b} H{x+10}" stroke="#205b99" fill="none" stroke-width="2"/>')
        parts.append(f'<circle cx="{x}" cy="{c}" r="5" fill="#205b99"/><text x="{x-20}" y="350">{html.escape(g["group"])}</text><text x="{x-20}" y="372">n={g["n"]}</text>')
    return '\n'.join(parts+['</g></svg>'])

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--png', action='store_true', help='Requires already installed matplotlib; never installs packages')
    p.add_argument('--csv', required=True); p.add_argument('--plan', required=True); p.add_argument('--out-root', required=True)
    a = p.parse_args()
    source, planpath = Path(a.csv), Path(a.plan)
    if source.stat().st_size > MAX_BYTES or planpath.stat().st_size > 65536:
        raise ValueError('Input exceeds size limit')
    raw, planraw = source.read_bytes(), planpath.read_bytes()
    result = calculate(raw, json.loads(planraw))
    result['implementation'] = {'describe_sha256': digest(Path(__file__)), 'artifact_store_sha256': digest(Path(__file__).with_name('_artifact_store.py'))}
    # Snapshot the exact bytes used; manifest refers to these copies as well as the caller paths.
    with tempfile.TemporaryDirectory(prefix='med-descriptive-') as td:
        stage = Path(td)
        (stage/'input.csv').write_bytes(raw); (stage/'plan.json').write_bytes(planraw)
        (stage/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        with (stage/'summary.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['group','n','mean','sample_sd','min','max']); w.writeheader(); w.writerows(result['groups'])
        (stage/'mean_sd.svg').write_text(plot(result), encoding='utf-8')
        if a.png:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            result['runtime']['matplotlib'] = matplotlib.__version__
            fig, ax = plt.subplots(figsize=(6, 4))
            gs = result['groups']
            ax.errorbar(range(len(gs)), [g['mean'] for g in gs], yerr=[g['sample_sd'] for g in gs], fmt='o', capsize=6)
            ax.set_xticks(range(len(gs)), [g['group'] for g in gs])
            ax.set_ylabel(result['unit']); ax.set_title('Mean +/- sample SD; no inference')
            ax.margins(x=0.3); fig.tight_layout(); fig.savefig(stage/'mean_sd.png', dpi=160); plt.close(fig)
            (stage/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        # Deliver the exact tested implementation and a local-input replay entrypoint.
        (stage/'describe.py').write_bytes(Path(__file__).read_bytes())
        (stage/'_artifact_store.py').write_bytes(Path(__file__).with_name('_artifact_store.py').read_bytes())
        replay = "import argparse, subprocess, sys\nfrom pathlib import Path\np=argparse.ArgumentParser()\np.add_argument('--out-root',required=True)\na=p.parse_args()\nd=Path(__file__).resolve().parent\ncmd=[sys.executable,str(d/'describe.py'),'--csv',str(d/'input.csv'),'--plan',str(d/'plan.json'),'--out-root',a.out_root]\n"
        if a.png:
            replay += "cmd.append('--png')\n"
        replay += "subprocess.run(cmd,check=True)\n"
        (stage/'reproduce.py').write_text(replay,encoding='utf-8')
        dest = publish(a.out_root, [stage/'input.csv', stage/'plan.json'], list(stage.iterdir()))
        # Input copies live within the published run, not transient staging paths.
        manifest = json.loads((dest/'manifest.json').read_text(encoding='utf-8'))
        for entry, name in zip(manifest['inputs'], ['input.csv','plan.json']):
            entry['file'] = name
        (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print(dest)

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, OverflowError, ImportError) as exc:
        print('Analysis stopped: '+str(exc), file=sys.stderr)
        sys.exit(2)
