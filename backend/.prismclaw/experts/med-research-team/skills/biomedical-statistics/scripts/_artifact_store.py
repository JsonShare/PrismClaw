"""Versioned publication helper; no sandbox or immutable-store guarantee."""
import argparse, hashlib, json, os, shutil, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import subprocess
import sys
import xml.etree.ElementTree as ET

def digest(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()

def package_snapshot(stage):
    """Package the complete run before publication, including its implementation."""
    members = sorted(path for path in stage.iterdir()
                     if path.name != 'reproduction.zip'
                     and not (path.name == '__pycache__' and path.is_dir()))
    if any(not path.is_file() for path in members):
        raise ValueError('Publication snapshot requires flat files; asset directories must be explicitly packaged')
    contents = [{'file': path.name, 'sha256': digest(path)} for path in members]
    with zipfile.ZipFile(stage / 'reproduction.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            archive.write(path, path.name)
        archive.writestr('reproduction-contents.json', json.dumps(contents, ensure_ascii=False, indent=2))
    return members + [stage / 'reproduction.zip']


def render_variant(stage, renderer_bytes):
    """Execute one caller-selected renderer before publication, retaining checked analysis inputs."""
    from PIL import Image
    script = stage / 'render_variant.py'
    script.write_bytes(renderer_bytes)
    protected = {path.name: digest(path) for path in stage.iterdir() if path.is_file()}
    with tempfile.TemporaryDirectory(prefix='medical-render-') as folder:
        output = Path(folder)
        try:
            process = subprocess.run([sys.executable, str(script), '--run-dir', str(stage),
                                      '--out-dir', str(output)], cwd=stage, capture_output=True,
                                     text=True, timeout=90, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Custom renderer exceeded 90 seconds; nothing published') from exc
        if process.returncode:
            raise ValueError('Custom renderer failed; nothing published: ' + process.stderr[-2000:])
        for name, before in protected.items():
            path = stage / name
            if not path.is_file() or digest(path) != before:
                raise ValueError('Custom renderer changed protected analysis file: ' + name)
        added = {path.name for path in stage.iterdir()
                 if not (path.name == '__pycache__' and path.is_dir())} - set(protected)
        if added:
            raise ValueError('Custom renderer must write only to --out-dir: ' + ', '.join(sorted(added)))
        svgs = list(output.glob('*.svg'))
        tiffs = list(output.glob('*.tiff')) + list(output.glob('*.tif'))
        if len(svgs) != 1 or len(tiffs) != 1:
            raise ValueError('Custom renderer must produce exactly one SVG and one TIFF in --out-dir')
        try:
            svg_root = ET.parse(svgs[0]).getroot()
        except ET.ParseError as exc:
            raise ValueError('Custom renderer produced malformed SVG') from exc
        if svg_root.tag != '{http://www.w3.org/2000/svg}svg':
            raise ValueError('Custom renderer did not produce an SVG document')
        with Image.open(tiffs[0]) as image:
            dpi = tuple(float(value) for value in image.info.get('dpi', ()))
            if image.format != 'TIFF' or image.mode != 'RGB' or len(dpi) != 2 or not all(abs(value - 300) < .01 for value in dpi):
                raise ValueError('Custom renderer must produce an RGB TIFF at 300 dpi')
            metadata = {'pixels': list(image.size), 'dpi': 300, 'mode': image.mode}
        for original, name in [('result.json', 'analysis-result.json'),
                               ('figure.svg', 'analysis-figure.svg'), ('figure.tiff', 'analysis-figure.tiff')]:
            shutil.copyfile(stage / original, stage / name)
        for source, name in [(svgs[0], 'figure.svg'), (tiffs[0], 'figure.tiff')]:
            shutil.copyfile(source, stage / name)
        receipt = {
            'renderer': script.name, 'renderer_sha256': digest(script),
            'source_result': 'analysis-result.json', 'source_result_sha256': digest(stage / 'analysis-result.json'),
            'original_coordinate_check': 'rendered-points.json',
            'figure': metadata,
            'figure_svg_sha256': digest(stage / 'figure.svg'),
            'figure_tiff_sha256': digest(stage / 'figure.tiff'),
            'validation_scope': 'Renderer executed; original analysis files unchanged; SVG and TIFF format checked. '
                                'Original coordinate checks describe analysis-figure only. Custom labels, geometry '
                                'and scientific meaning require independent review.',
        }
        (stage / 'render-variant.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        return receipt


def publish(root, inputs, files):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = [Path(p).resolve(strict=True) for p in files]
    inputs = [Path(p).resolve(strict=True) for p in inputs]
    names = [p.name for p in files]
    if len(names) != len(set(names)) or 'manifest.json' in names:
        raise ValueError('Duplicate or reserved output filename')
    if any(not p.is_file() for p in files + inputs):
        raise ValueError('Inputs and artifacts must be regular files')
    run_id = str(uuid.uuid4())
    stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=str(root)))
    try:
        artifacts = []
        for source in files:
            before = digest(source)
            target = stage / source.name
            with source.open('rb') as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            after = digest(target)
            if after != before or digest(source) != before:
                raise ValueError('Source changed during publication')
            artifacts.append({'file': source.name, 'sha256': after, 'bytes': target.stat().st_size})
        manifest = {'schema': 1, 'run_id': run_id,
                    'created_utc': datetime.now(timezone.utc).isoformat(),
                    'inputs': [{'file': str(p), 'sha256': digest(p), 'bytes': p.stat().st_size} for p in inputs],
                    'artifacts': artifacts, 'storage_guarantee': 'unique-path-and-checksums-only'}
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        # mkdir is exclusive: never rename over an existing destination.
        destination = root / run_id
        destination.mkdir(exist_ok=False)
        try:
            for p in stage.iterdir():
                with p.open('rb') as src, (destination / p.name).open('xb') as dst:
                    shutil.copyfileobj(src, dst)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination
    finally:
        shutil.rmtree(stage)

if __name__ == '__main__':
    raise SystemExit('Internal module: use describe.py, which already publishes its results.')
