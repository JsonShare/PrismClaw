"""Versioned publication helper; no sandbox or immutable-store guarantee."""
import argparse, hashlib, json, os, shutil, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

def digest(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()

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
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--input', action='append', required=True)
    p.add_argument('--file', action='append', required=True)
    a = p.parse_args()
    print(publish(a.root, a.input, a.file))
