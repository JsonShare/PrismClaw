"""Render only exact source quotes; not a semantic/OCR/coverage validator."""
import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from publish_artifacts import publish

LIMIT = 1024 * 1024

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def positions(source, quote):
    at, found = 0, []
    while True:
        at = source.find(quote, at)
        if at < 0:
            return found
        found.append([at, at + len(quote)])
        at += 1

def literal(quote):
    longest = max([len(m[0]) for m in re.finditer(r'`+', quote)] or [0])
    fence = '`' * max(3, longest + 1)
    return fence + 'text\n' + quote + '\n' + fence

def render(source, facts):
    bundled = isinstance(source, dict)
    sources = source if bundled else {'source': {'text': source, 'kind': 'user_text'}}
    if not sources or len(sources) > 12:
        raise ValueError('One through twelve source documents required')
    for source_id, document in sources.items():
        text = document.get('text')
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > LIMIT:
            raise ValueError('Source text missing or too large')
        if document.get('kind') not in {'user_text', 'attachment_transcription'}:
            raise ValueError('Unknown source kind')
    if not isinstance(facts, dict) or set(facts) - {'format', 'facts'}:
        raise ValueError('Only format and facts are accepted; no free-form headings or subject counts')
    fmt = facts.get('format')
    if fmt not in {'timeline', 'soap', 'hospital_course'}:
        raise ValueError('Unsupported format')
    rows = facts.get('facts')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError('Facts must be a nonempty bounded list')
    sections = {'timeline': ['timeline'], 'soap': ['S', 'O', 'A', 'P'],
                'hospital_course': ['admission', 'hospital_course']}[fmt]
    mapped, seen = [], set()
    for item in rows:
        if not isinstance(item, dict) or set(item) - {'section', 'quote', 'subject_quote', 'source_id'}:
            raise ValueError('Unsupported fact field')
        section, quote = item.get('section'), item.get('quote')
        if section not in sections or not isinstance(quote, str) or not quote.strip() or len(quote) > 10000:
            raise ValueError('Invalid section or quote')
        source_id = item.get('source_id') if bundled else 'source'
        if source_id not in sources:
            raise ValueError('Every bundled fact requires a valid source_id')
        document = sources[source_id]
        text = document['text']
        spans = positions(text, quote)
        if not spans:
            raise ValueError('Quote is absent from the selected source')
        if fmt == 'hospital_course' and section == 'admission' and re.search(r'住院期间|住院后|入院后', quote):
            raise ValueError('Explicit inpatient event cannot be put in the admission-state section; check the current episode and use hospital_course')
        if (section, quote) in seen:
            raise ValueError('Duplicate quote in section')
        seen.add((section, quote))
        mapped_item = {'section': section, 'quote': quote, 'source_spans_chars': spans}
        if bundled:
            mapped_item.update(source_id=source_id, source_kind=document['kind'])
        if 'subject_quote' in item:
            subject = item['subject_quote']
            if not isinstance(subject, str) or not subject.strip() or len(subject) > 10000:
                raise ValueError('Invalid subject quote')
            subject_spans = positions(text, subject)
            if not subject_spans:
                raise ValueError('Subject quote is absent from selected source')
            mapped_item.update(subject_quote=subject, subject_source_spans_chars=subject_spans)
        mapped.append(mapped_item)
    if fmt == 'hospital_course':
        body = ['本次入院时的病情与入院背景放在“入院情况”；入院后发生的转科、检查和治疗经历放在“住院诊疗经过”，不因旧稿放在前栏就沿用错误归属。', '']
        headings = {'admission': '入院情况中的已知背景', 'hospital_course': '住院诊疗经过草稿'}
        for section in sections:
            quotes = [x['quote'].strip().rstrip('。；;') for x in mapped if x['section'] == section
                      and x.get('source_kind') != 'attachment_transcription']
            records = [x for x in mapped if x['section'] == section
                       and x.get('source_kind') == 'attachment_transcription']
            if quotes or records:
                body.extend(['## ' + headings[section], ''])
            if quotes:
                body.extend(['；'.join(quotes) + '。', ''])
            if records:
                body.extend(['附件可读记录（逐字片段）：' + '；'.join(x['quote'].strip().rstrip('。；;') for x in records) + '。',
                             '以上日期与检查名称按附件可见内容保留，记录时间不自动视为实际操作时间；不补写被遮挡内容。', ''])
        body.append('草稿，待医生确认。未在所选原文中明确记载的科室、目的、术式和结果未补写。')
        source_map = {'schema': 1, 'source_sha256_utf8': None if bundled else sha(source.encode('utf-8')),
                      'facts': mapped, 'validation_scope': 'exact-source-quotes-and-explicit-inpatient-marker-check',
                      'not_validated': ['clinical truth', 'OCR', 'source identity', 'fact coverage', 'implicit timing']}
        if bundled:
            source_map['sources'] = {key: {k: v for k, v in doc.items() if k != 'text'} for key, doc in sources.items()}
        return '\n'.join(body), source_map
    body = ['# 来源事实草稿', '', '草稿，待医生确认。以下为选定文本的原文片段；不推断患者人数或主体关联。', '']
    for section in sections:
        body.extend(['## ' + ('时间线原文' if section == 'timeline' else section), ''])
        items = [x for x in mapped if x['section'] == section]
        if not items:
            body.extend(['未提供所选字段的原文片段。', ''])
        for item in items:
            body.extend([literal(item['quote']), ''])
            if 'subject_quote' in item:
                body.extend(['主体标识原文片段（仅直接引用）：', literal(item['subject_quote']), ''])
    source_map = {'schema': 1, 'source_sha256_utf8': None if bundled else sha(source.encode('utf-8')),
                  'facts': mapped, 'validation_scope': 'exact-source-quotes-and-closed-rendering-only',
                  'not_validated': ['clinical truth', 'OCR', 'source identity', 'fact coverage', 'section assignment']}
    if bundled:
        source_map['sources'] = {key: {k: v for k, v in doc.items() if k != 'text'} for key, doc in sources.items()}
    return '\n'.join(body), source_map


def load_bundle(path):
    if path.stat().st_size > LIMIT:
        raise ValueError('Source bundle exceeds size limit')
    bundle = json.loads(path.read_text())
    if not isinstance(bundle, dict) or set(bundle) != {'sources'}:
        raise ValueError('Source bundle requires only sources')
    entries = bundle['sources']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 12:
        raise ValueError('One through twelve source documents required')
    sources = {}
    snapshots = {}
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or set(entry) - {'id', 'kind', 'text_file', 'attachment_file', 'region'}:
            raise ValueError('Unsupported source bundle field')
        key = entry.get('id')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', key) or key in sources:
            raise ValueError('Source ids must be unique simple identifiers')
        text_path = path.parent / entry['text_file']
        if text_path.stat().st_size > LIMIT:
            raise ValueError('Source transcript exceeds size limit')
        raw = text_path.read_bytes()
        text_name = f'source-{index}.txt'
        snapshots[text_name] = raw
        document = {'text': raw.decode('utf-8'), 'kind': entry.get('kind'),
                    'text_file': text_name, 'text_sha256': sha(raw)}
        if document['kind'] == 'attachment_transcription':
            from PIL import Image
            attachment = path.parent / entry['attachment_file']
            if attachment.stat().st_size > 35 * LIMIT:
                raise ValueError('Attachment exceeds size limit')
            image_raw = attachment.read_bytes()
            with Image.open(attachment) as image:
                if image.format not in {'JPEG', 'PNG'}:
                    raise ValueError('Only selected JPEG/PNG attachments supported')
                width, height = image.size
                if width * height > 40000000:
                    raise ValueError('Attachment exceeds pixel limit')
                suffix = '.jpg' if image.format == 'JPEG' else '.png'
            region = entry.get('region')
            if not isinstance(region, list) or len(region) != 4 or any(type(x) is not int for x in region):
                raise ValueError('Transcription requires an explicit original-pixel region')
            left, top, right, bottom = region
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError('Transcription region lies outside the original image')
            image_name = f'attachment-{index}' + suffix
            snapshots[image_name] = image_raw
            document.update(attachment_file=image_name, attachment_sha256=sha(image_raw), region=region,
                            validation_scope='file bytes and region only; transcription is not program-verified OCR')
        elif document['kind'] != 'user_text' or 'attachment_file' in entry or 'region' in entry:
            raise ValueError('Invalid source kind or unexpected attachment fields')
        sources[key] = document
    return sources, snapshots


def main():
    p = argparse.ArgumentParser()
    source_group = p.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--source')
    source_group.add_argument('--source-bundle')
    p.add_argument('--facts', required=True)
    destination = p.add_mutually_exclusive_group(required=True)
    destination.add_argument('--out-root')
    destination.add_argument('--stdout', action='store_true')
    a = p.parse_args()
    source_path, facts_path = Path(a.source or a.source_bundle), Path(a.facts)
    if source_path.stat().st_size > LIMIT or facts_path.stat().st_size > LIMIT:
        raise ValueError('Input exceeds size limit')
    raw, facts_raw = source_path.read_bytes(), facts_path.read_bytes()
    if a.source_bundle:
        source, snapshots = load_bundle(source_path)
    else:
        source = raw.decode('utf-8')
        snapshots = {'input.txt': raw}
    facts = json.loads(facts_raw)
    draft, mapping = render(source, facts)
    if a.stdout:
        print(draft)
        return
    mapping['source_sha256_bytes'] = sha(raw)
    mapping['renderer_sha256'] = sha(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='clinical-source-') as work:
        stage = Path(work)
        for name, content in snapshots.items():
            (stage/name).write_bytes(content)
        if a.source_bundle:
            (stage/'source-bundle-original.json').write_bytes(raw)
        (stage/'facts.json').write_bytes(facts_raw)
        (stage/'source-map.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding='utf-8')
        (stage/'draft.md').write_text(draft, encoding='utf-8')
        result = publish(a.out_root, [source_path, facts_path], list(stage.iterdir()))
        print(result)

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, UnicodeError) as exc:
        print('Draft stopped: ' + str(exc), file=sys.stderr)
        sys.exit(2)
