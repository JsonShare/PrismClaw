"""Create a traceable, pixel-preserving reading crop of an explicitly selected image."""
import argparse
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

from PIL import Image


def inspect_region(
    source: str | Path,
    bounds: Sequence[int],
    scale: int,
    out_root: str | Path,
) -> Path:
    source = Path(source)
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('Image exceeds 25 MiB')
    if len(bounds) != 4 or any(type(v) is not int for v in bounds):
        raise ValueError('Four integer pixel coordinates required')
    if type(scale) is not int or not 1 <= scale <= 4:
        raise ValueError('Scale must be 1 through 4')
    with Image.open(source) as image:
        if image.format not in {'JPEG', 'PNG', 'WEBP'}:
            raise ValueError('Only static JPEG/PNG/WEBP images supported')
        if getattr(image, 'n_frames', 1) != 1:
            raise ValueError('Multi-frame images are not supported; select a verified static source')
        if image.width * image.height > 40_000_000:
            raise ValueError('Image exceeds 40 million pixels')
        left, top, right, bottom = bounds
        if not 0 <= left < right <= image.width or not 0 <= top < bottom <= image.height:
            raise ValueError('Crop coordinates outside source image')
        width = (right - left) * scale
        height = (bottom - top) * scale
        if width * height > 16_000_000:
            raise ValueError('Select a smaller region; output exceeds 16 million pixels')
        crop = image.crop((left, top, right, bottom))
        enlarged = crop.resize((width, height), resample=Image.Resampling.NEAREST)
        original_size = [image.width, image.height]
        source_format = image.format
        orientation = image.getexif().get(274, 1)
        enlarged.info.pop('exif', None)
    metadata = {
        'schema': 1,
        'purpose': 'reading aid, not a new medical image or clinical interpretation',
        'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'sourceFormat': source_format,
        'sourcePixels': original_size,
        'boundsPixels': list(bounds),
        'scale': scale,
        'sourceExifOrientation': orientation,
        'coordinateFrame': 'stored source pixels; no automatic rotation',
        'outputPixels': [width, height],
        'resampling': 'nearest; decoded source pixels repeated without interpolation',
    }
    root = Path(out_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / str(uuid.uuid4())
    target.mkdir(mode=0o700)
    try:
        enlarged.save(target / 'region.png')
        metadata['regionSha256'] = hashlib.sha256((target / 'region.png').read_bytes()).hexdigest()
        (target / 'region.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    except Exception:
        shutil.rmtree(target)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--box', required=True, help='left,top,right,bottom in original pixels')
    parser.add_argument('--scale', type=int, default=2)
    parser.add_argument('--out-root', type=Path, required=True)
    args = parser.parse_args()
    bounds = [int(value) for value in args.box.split(',')]
    print(inspect_region(args.input, bounds, args.scale, args.out_root))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, Image.DecompressionBombError) as error:
        print('Image reading aid stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
