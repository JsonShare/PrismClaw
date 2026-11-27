"""Render source-authored card text, one physical A4 page per card; no medical defaults."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from PIL import Image, ImageDraw, ImageFont

VERSION = '1.0.0'
SIZE = (2480, 3508)
INK = '#173449'


def wrap(text, font, width):
    lines = []
    line = ''
    for char in text:
        if char == '\n':
            lines.append(line)
            line = ''
        elif font.getlength(line + char) > width:
            if not line:
                raise ValueError('Unrenderable glyph width')
            lines.append(line)
            line = char
        else:
            line += char
    if line:
        lines.append(line)
    return lines


def icon(draw, kind, x, y):
    if kind == 'water':
        segments = [((85,0),(65,40),(5,70),(15,110)),
                    ((15,110),(5,155),(40,180),(85,180)),
                    ((85,180),(130,180),(165,155),(155,110)),
                    ((155,110),(165,70),(105,40),(85,0))]
        points = []
        for a,b,c,d in segments:
            for step in range(21):
                t = step/20
                points.append((x+(1-t)**3*a[0]+3*(1-t)**2*t*b[0]+3*(1-t)*t*t*c[0]+t**3*d[0],
                               y+(1-t)**3*a[1]+3*(1-t)**2*t*b[1]+3*(1-t)*t*t*c[1]+t**3*d[1]))
        draw.line(points, fill=INK, width=8, joint='curve')
    elif kind == 'salt':
        draw.polygon([(x+35,y+45),(x+135,y+45),(x+160,y+165),(x+10,y+165),(x+35,y+45)], outline=INK, width=8)
        draw.rounded_rectangle((x+35,y+10,x+135,y+45), radius=8, outline=INK, width=8)
        for offset in (60,85,110):
            draw.ellipse((x+offset,y+22,x+offset+5,y+27),fill=INK)
    elif kind == 'weight':
        draw.rounded_rectangle((x,y,x+170,y+170), radius=20, outline=INK, width=8)
        draw.arc((x+25,y+20,x+145,y+120),180,360, fill=INK, width=7)
        draw.line((x+85,y+95,x+120,y+50), fill=INK, width=8)
    elif kind == 'medicine':
        draw.rounded_rectangle((x,y+40,x+175,y+125), radius=40, outline=INK, width=8)
        draw.line((x+88,y+42,x+88,y+124), fill=INK, width=7)
    elif kind == 'note':
        draw.rectangle((x+10,y,x+160,y+175), outline=INK, width=8)
        for row in (40,80,120):
            draw.line((x+35,y+row,x+135,y+row), fill=INK, width=7)
    else:
        raise ValueError('Unsupported icon; use water/salt/weight/medicine/note')


def render(plan, font_path):
    cards = plan.get('cards')
    if not isinstance(cards, list) or not 1 <= len(cards) <= 12:
        raise ValueError('Provide one to twelve explicit cards')
    title_font = ImageFont.truetype(str(font_path), 150)
    body_font = ImageFont.truetype(str(font_path), 110)
    footer_font = ImageFont.truetype(str(font_path), 70)
    pages = []
    for index, card in enumerate(cards):
        if not isinstance(card.get('title'), str) or not card['title'].strip() or len(card['title']) > 200:
            raise ValueError('Explicit card title required')
        sections = card.get('sections')
        if not isinstance(sections, list) or not 1 <= len(sections) <= 4:
            raise ValueError('Provide one to four concise sections per card')
        image = Image.new('RGB', SIZE, 'white')
        draw = ImageDraw.Draw(image)
        title = wrap(card['title'], title_font, 2160)
        if len(title) > 2:
            raise ValueError('Title overflows; shorten text, do not shrink font')
        y = 160
        for line in title:
            draw.text((160,y), line, font=title_font, fill=INK)
            y += 190
        y += 90
        for section in sections:
            text = section.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 1000:
                raise ValueError('Explicit bounded section text required')
            lines = wrap(text, body_font, 1800)
            height = max(200, len(lines)*150)
            if y + height > 3100:
                raise ValueError('Card body overflows A4; shorten or explicitly split card, do not shrink font')
            icon(draw, section.get('icon', 'note'), 160, y+25)
            for line in lines:
                draw.text((470,y), line, font=body_font, fill=INK)
                y += 150
            y += max(0, height-len(lines)*150) + 90
        footer = card.get('footer', '')
        if not isinstance(footer, str) or len(wrap(footer, footer_font, 1800)) > 1:
            raise ValueError('Footer must fit one line')
        draw.line((160,3240,2320,3240), fill=INK, width=3)
        draw.text((160,3280), footer, font=footer_font, fill=INK)
        draw.text((2100,3280), f'{index+1}/{len(cards)}', font=footer_font, fill=INK)
        pages.append(image)
    return pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--out-root', type=Path, required=True)
    args = parser.parse_args()
    raw = args.plan.read_bytes()
    if len(raw) > 100000:
        raise ValueError('Card plan too large')
    pages = render(json.loads(raw), args.font)
    args.out_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out_root) as tmp:
        stage = Path(tmp)/'cards'
        stage.mkdir()
        for index, image in enumerate(pages):
            image.save(stage/f'card-{index+1}.png', dpi=(300,300))
        pages[0].save(stage/'cards.pdf', save_all=True, append_images=pages[1:], resolution=300)
        (stage/'plan.json').write_bytes(raw)
        manifest = {'renderer_version':VERSION, 'physical_pages':len(pages), 'pixels':SIZE,
                    'body_font_points':26.4, 'font_sha256':hashlib.sha256(args.font.read_bytes()).hexdigest(),
                    'scope':'Layout and pagination only; medical content and source support require review',
                    'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir()}}
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        destination = args.out_root/str(uuid.uuid4())
        stage.rename(destination)
    print(destination.resolve())


if __name__ == '__main__':
    main()
