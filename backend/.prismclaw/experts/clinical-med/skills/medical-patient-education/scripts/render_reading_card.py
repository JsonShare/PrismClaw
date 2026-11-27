"""Render one readable A4 card from explicit text; reject overflow without shrinking."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import uuid

from PIL import Image, ImageDraw, ImageFont
from render_cards import INK, SIZE


DPI = 300
BODY_POINTS = 12
MARGIN = 165
GAP = 36
PAD = 32
NO_LINE_START = '，。、；：！？）》】〕〉」』”’)]}'
NO_LINE_END = '（《【〔〈「『“‘([{'


def wrap_readable(value, font, width):
    # Keep a numeric comparison/range intact; a split digit changes how a card is read.
    # shortcut: decimal comparisons/ranges only; extend before supporting scientific notation or compound units.
    tokens = re.findall(r'[<>≤≥=±+\-−]*[0-9]+(?:[.,~～–—/-][0-9]+)*[%％]?|[^\n]|\n', value)
    lines = []
    line = []
    for token in tokens:
        if token == '\n':
            lines.append(''.join(line))
            line = []
            continue
        if font.getlength(token) > width:
            raise ValueError('Unbreakable value exceeds readable line width')
        candidate = line + [token]
        if line and font.getlength(''.join(candidate)) > width:
            split = len(line)
            while split and (candidate[split - 1] in NO_LINE_END or candidate[split] in NO_LINE_START):
                split -= 1
            if not split or font.getlength(''.join(candidate[split:])) > width:
                raise ValueError('Unbreakable value exceeds readable line width')
            lines.append(''.join(candidate[:split]))
            line = candidate[split:]
        else:
            line = candidate
    if line:
        lines.append(''.join(line))
    return lines


def text(plan, field, required=True):
    value = plan.get(field, '')
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError(f'{field}: bounded text required')
    if required and not value.strip():
        raise ValueError(f'{field}: explicit text required')
    return value


def render(plan, font_path):
    if not isinstance(plan, dict):
        raise ValueError('Provide a card object')
    panels = plan.get('sections')
    if not isinstance(panels, list) or not 1 <= len(panels) <= 6:
        raise ValueError('Provide one to six necessary sections, without extra record pages')
    fonts = {pt: ImageFont.truetype(str(font_path), round(pt * DPI / 72)) for pt in (10, 11, 12, 13, 20)}
    line_heights = {pt: round(pt * DPI / 72 * 1.4) for pt in fonts}
    width = SIZE[0] - 2 * MARGIN
    col_width = (width - GAP) // 2
    placements = []

    def layout(value, points, x, y, available):
        lines = wrap_readable(value, fonts[points], available)
        placements.append((lines, points, x, y))
        return len(lines) * line_heights[points]

    y = MARGIN
    y += layout(text(plan, 'title'), 20, MARGIN, y, width)
    y += 20
    y += layout(text(plan, 'subtitle', required=False), 11, MARGIN, y, width)
    y += GAP
    boxes = []
    for offset in range(0, len(panels), 2):
        row_heights = []
        for col, panel in enumerate(panels[offset:offset + 2]):
            if not isinstance(panel, dict):
                raise ValueError('Each section needs a title and explicit text')
            x = MARGIN + col * (col_width + GAP)
            top = y + PAD
            top += layout(text(panel, 'title'), 13, x + PAD, top, col_width - 2 * PAD)
            top += 18
            top += layout(text(panel, 'text'), 12, x + PAD, top, col_width - 2 * PAD)
            row_heights.append(top - y + PAD)
        height = max(row_heights)
        for col in range(len(row_heights)):
            x = MARGIN + col * (col_width + GAP)
            boxes.append((x, y, x + col_width, y + height))
        y += height + GAP
    y += layout(text(plan, 'safety', required=False), 12, MARGIN, y, width)
    y += 24
    y += layout(text(plan, 'sources', required=False), 10, MARGIN, y, width)
    if y > SIZE[1] - MARGIN:
        raise ValueError('Content exceeds one readable A4 page; shorten nonessential text, do not shrink fonts or add pages')

    image = Image.new('RGB', SIZE, 'white')
    draw = ImageDraw.Draw(image)
    for box in boxes:
        draw.rounded_rectangle(box, radius=16, outline='#baccc8', width=3)
    for lines, points, x, top in placements:
        for index, line in enumerate(lines):
            draw.text((x, top + index * line_heights[points]), line, font=fonts[points], fill=INK, anchor='lt')
    return image, {'physical_pages': 1, 'body_font_points': BODY_POINTS,
                   'source_font_points': 10, 'dpi': DPI,
                   'content_bottom_pixels': y, 'bottom_limit_pixels': SIZE[1] - MARGIN}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--out-root', type=Path, required=True)
    args = parser.parse_args()
    raw = args.plan.read_bytes()
    if len(raw) > 100000:
        raise ValueError('Card plan too large')
    image, metadata = render(json.loads(raw), args.font)
    args.out_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out_root) as temp:
        stage = Path(temp) / 'card'
        stage.mkdir()
        image.save(stage / 'card.png', dpi=(DPI, DPI))
        image.save(stage / 'card.pdf', resolution=DPI)
        (stage / 'plan.json').write_bytes(raw)
        metadata['font_sha256'] = hashlib.sha256(args.font.read_bytes()).hexdigest()
        metadata['scope'] = 'Layout only; source fidelity, medical content and actual visual review remain required'
        metadata['files'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir()}
        (stage / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        destination = args.out_root / str(uuid.uuid4())
        stage.rename(destination)
    print(destination.resolve())


if __name__ == '__main__':
    main()
