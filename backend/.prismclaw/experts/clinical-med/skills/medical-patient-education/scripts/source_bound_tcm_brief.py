"""Validate and format this turn's authored communication; no stored clinical answers."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
import subprocess
import shutil
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def bounded(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(name + ' must be nonempty text within its size limit')
    return value


def source_quote(value, source, name, limit=300):
    value = bounded(value, name, limit)
    if value not in source:
        raise ValueError(name + ' must be an exact fragment of the current input')
    offset = source.find(value)
    prefix = source[max(0, offset - 5):offset]
    if re.search(r'(?:未见|否认|没有|不是|疑似|待排|考虑|可能为|无|未|不)\s*$', prefix):
        raise ValueError(name + ' omits adjacent negation or uncertainty')
    return value


def normalized_source_text(value):
    text = ' '.join(unicodedata.normalize('NFKC', value).split())
    return re.sub(r'(?<=[\u3400-\u9fff0-9、,;:()])\s+(?=[\u3400-\u9fff0-9、,;:()])', '', text)


def verified_bibliography(entries, source):
    """Check every printed bibliographic value against its actual source excerpt."""
    if not isinstance(entries, list) or not 1 <= len(entries) <= 12:
        raise ValueError('bibliography requires 1-12 fields with value and exact quote')
    allowed = {'title', 'author', 'reviewer', 'editor', 'organization', 'journal', 'year', 'date',
               'volume', 'issue', 'first_page', 'last_page', 'doi'}
    fields = {}
    receipts = []
    normalized = normalized_source_text
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'field', 'value', 'quote'}:
            raise ValueError('Each bibliography entry requires field, value, quote')
        name = entry['field']
        if name not in allowed or name in fields:
            raise ValueError('Bibliography field must be supported and unique')
        value = bounded(entry['value'], 'bibliography value', 500)
        quote = bounded(entry['quote'], 'bibliography quote', 3000)
        if normalized(quote) not in normalized(source) or normalized(value) not in normalized(quote):
            raise ValueError('Bibliography value or quote absent from retrieved source: ' + name)
        if name in {'year', 'volume', 'issue', 'first_page', 'last_page'} and not re.fullmatch(r'[0-9A-Za-z.-]{1,20}', unicodedata.normalize('NFKC', value)):
            raise ValueError('Use the actual individual bibliographic field, not a composed range: ' + name)
        if name == 'author' and re.search(
                r'(?:审稿(?:人)?|审核|审校|医学审阅|reviewed by|reviewer|编辑|editor)\s*[:：]?\s*' + re.escape(value),
                quote, re.I):
            raise ValueError('The source labels this person as reviewer/editor, not author. Preserve the stated role '
                             'using reviewer/editor or omit the optional person field; do not turn a reviewed page into their authorship.')
        fields[name] = value
        receipts.append(entry)
    if 'title' not in fields:
        raise ValueError('A retrieved source title is required; do not invent an excerpt heading')
    if 'last_page' in fields and 'first_page' not in fields:
        raise ValueError('A final page needs a verified first page')
    parts = [fields[key] for key in ('author', 'organization', 'title', 'journal', 'year', 'date') if key in fields]
    if fields.get('reviewer'):
        parts.append('审稿：' + fields['reviewer'])
    if fields.get('editor'):
        parts.append('编辑：' + fields['editor'])
    number = fields.get('volume', '')
    if 'issue' in fields:
        number += '(' + fields['issue'] + ')'
    pages = fields.get('first_page', '')
    if 'last_page' in fields and fields['last_page'] != pages:
        pages += '–' + fields['last_page']
    if number or pages:
        parts.append(number + (':' if number and pages else '') + pages)
    if fields.get('doi'):
        parts.append('DOI ' + fields['doi'])
    return '；'.join(parts), receipts


class VisibleSourceText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []
        self.heading = None
        self.heading_parts = []
        self.titles = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if tag in ('title', 'h1'):
            self.heading = tag
            self.heading_parts = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.hidden:
            self.hidden -= 1
        if tag == self.heading:
            self.titles.append(''.join(self.heading_parts))
            self.heading = None

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.heading:
                self.heading_parts.append(data)


def fetch_source(url, directory):
    """Fetch original bytes independently; a model-edited local text cannot validate itself."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Source requires a public HTTP(S) URL without credentials')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0 DoctorBuddy-source-check/1.0'})
    with urlopen(request, timeout=25) as response:
        raw = response.read(6 * 1024 * 1024 + 1)
        charset = response.headers.get_content_charset() or 'utf-8'
        final_url = response.geturl()
    if len(raw) > 6 * 1024 * 1024:
        raise ValueError('Source exceeds 6 MiB; select an appropriate original source')
    if raw.startswith(b'%PDF'):
        binary = shutil.which('pdftotext')
        if not binary:
            raise ValueError('PDF source requires the existing pdftotext tool')
        # Reading order keeps a column's sentences contiguous; layout order can
        # interleave unrelated sentences across two-column journal pages.
        result = subprocess.run([binary, '-', '-'], input=raw, capture_output=True, timeout=25)
        if result.returncode:
            raise ValueError('Original PDF extraction failed; do not supply a hand-written replacement')
        text = result.stdout.decode('utf-8')
        first = subprocess.run([binary, '-f', '1', '-l', '1', '-', '-'],
                               input=raw, capture_output=True, timeout=25)
        if first.returncode:
            raise ValueError('Cannot extract the original PDF first page for title verification')
        title_evidence = {'kind': 'pdf_first_page', 'text': first.stdout.decode('utf-8')[:20000]}
        suffix = '.pdf'
    else:
        body = raw.decode(charset, errors='replace')
        if '<html' in body.lower() or '<!doctype html' in body.lower():
            parser = VisibleSourceText()
            parser.feed(body)
            text = ' '.join(parser.parts)
            title_evidence = {'kind': 'html_headings', 'titles': parser.titles[:20]}
            suffix = '.html'
        else:
            text = body
            title_evidence = {'kind': 'plain_text', 'text': body[:6000]}
            suffix = '.txt'
    if not text.strip() or len(text) > 2 * 1024 * 1024:
        raise ValueError('Original source text unavailable or too long; no fabricated metadata permitted')
    sha = hashlib.sha256(raw).hexdigest()
    folder = directory / 'verified-source-bytes'
    folder.mkdir(exist_ok=True)
    snapshot = folder / (sha + suffix)
    snapshot.write_bytes(raw)
    return text, {'source_sha256': sha, 'source_bytes': len(raw), 'requested_url': url,
                  'resolved_url': final_url, 'snapshot': str(snapshot.relative_to(directory)),
                  'text_extraction': 'pdftotext-reading-order' if suffix == '.pdf' else 'visible-source-text',
                  'title_evidence': title_evidence,
                  'verification': 'fresh source download and actual PDF/HTML extraction; no model-edited text trusted'}


def verify_title(entries, receipt):
    title = next(row['value'] for row in entries if row['field'] == 'title')
    normalize = normalized_source_text
    evidence = receipt.get('title_evidence', {})
    if evidence.get('kind') == 'html_headings':
        valid = normalize(title) in {normalize(value) for value in evidence.get('titles', [])}
    elif evidence.get('kind') in ('pdf_first_page', 'plain_text'):
        valid = normalize(title) in normalize(evidence.get('text', ''))
    else:
        valid = False
    if not valid:
        raise ValueError('Title must be supported by the original PDF first page or exact webpage title/h1. '
                         'A journal name, later-page heading, or hand-written title cannot replace the article title. '
                         'Use a complete, genuinely relevant source; do not rewrite source bytes.')


def verified_source_locator(text, quote, receipt, provided_locator):
    """Locate the actual quoted passage in extracted PDF file pages, not authored page claims."""
    if receipt.get('text_extraction') != 'pdftotext-reading-order':
        return provided_locator, {'method': 'provided-source-locator', 'page_numbers_verified': False}
    compact = lambda value: re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
    pages = [compact(page) for page in text.split('\f')]
    joined = ''.join(pages)
    needle = compact(quote)
    if not needle:
        raise ValueError('PDF quote must contain visible source text')
    boundaries = []
    offset = 0
    for number, page in enumerate(pages, 1):
        boundaries.append((number, offset, offset + len(page)))
        offset += len(page)
    found = set()
    start = joined.find(needle)
    while start >= 0:
        end = start + len(needle)
        for number, left, right in boundaries:
            if left < end and right > start and right > left:
                found.add(number)
        start = joined.find(needle, start + 1)
    if not found:
        raise ValueError('Cannot locate the quoted passage in original PDF pages; do not invent a locator')
    numbers = sorted(found)
    locator = '引句见PDF第' + '、'.join(str(number) for number in numbers) + '页（按原件页序）'
    return locator, {'method': 'actual-pdf-quote-pages', 'page_numbers_verified': True, 'pdf_pages': numbers}


def single_clause(value, label, limit):
    value = bounded(value, label, limit).strip().rstrip('。.!！?？')
    if not value or re.search(r'[。！？!?\n\r；;]', value):
        raise ValueError(label + ' must be one short clause, without additional sentences or hidden qualifications')
    if re.search(r'(?<!不)(?:必然|必定|肯定|一定会)|certainly|definitely', value, re.I):
        raise ValueError(label + ' cannot assert certainty in a conditional explanation')
    return value


def verify_action_fragments(condition, action, quotes):
    """Bind an action trigger and response to one actual quoted passage.

    This conservative extractive mode does not prove the source's applicability.
    """
    condition = normalized_source_text(condition)
    action = normalized_source_text(action)
    for quote in quotes:
        original = normalized_source_text(quote)
        if condition not in original or action not in original:
            continue
        for fragment in (condition, action):
            start = original.find(fragment)
            prefix = original[max(0, start - 8):start]
            if re.search(r'(?:无需|不必|不应|不得|不建议|不能|不|未)\s*$', prefix):
                raise ValueError('Action fragment omits adjacent negation from its actual source')
        return
    raise ValueError('Action condition and action must both be exact short fragments of the same cited source quote. '
                     'Use the source\'s actual trigger and response; do not add warning signs to a general discussion. '
                     'If no applicable source supports this extra action, omit it rather than fabricate a quote.')


def verify_exclusive_language(text, quotes):
    pattern = r'才适合|只有.{0,40}才|唯有|\bonly\b'
    if re.search(pattern, text, re.I) and not any(re.search(pattern, quote, re.I) for quote in quotes):
        raise ValueError('Exclusive wording is absent from the cited source; preserve its scope without adding only/才适合')


def contains_clinical_action(text):
    return bool(re.search(
        r'(?:需要|需|应该|应当|应结合|应完善|应进行|应接受|仍应|建议|请|必须|务必|宜|should|must|need to)[^。；;\n]{0,24}'
        r'(?:就医|就诊|急诊|复诊|检查|复查|查清|停用|停药|服用|用药|治疗|转诊|住院|seek care|consult)',
        text, re.I))


def render(plan, directory=None):
    directory = (directory or Path.cwd()).resolve()
    allowed = {'source_text', 'fact_quotes', 'clinical_points', 'patient_paragraphs', 'sources'}
    if not isinstance(plan, dict) or set(plan) != allowed:
        raise ValueError('Provide source_text, fact_quotes, clinical_points, patient_paragraphs, and sources. '
                         'Preset modes and stored clinical answers are not supported.')
    source = bounded(plan['source_text'], 'source_text', 20000)
    facts = plan['fact_quotes']
    if not isinstance(facts, list) or not 1 <= len(facts) <= 12:
        raise ValueError('Select 1-12 exact facts from this turn; preserve uncertainty')
    facts = [source_quote(x, source, 'fact_quote') for x in facts]
    refs = plan['sources']
    if not isinstance(refs, list) or not 1 <= len(refs) <= 12:
        raise ValueError('Supply 1-12 actually retrieved sources')
    mapped = {}
    receipts = []
    labels = []
    locators = []
    downloaded = {}
    for index, ref in enumerate(refs, 1):
        if not isinstance(ref, dict) or set(ref) != {'id', 'bibliography', 'url', 'locator', 'source_file', 'quote'}:
            raise ValueError('Each source requires id, bibliography, url, locator, source_file, and exact quote; '
                             'bibliography fields require field, value, quote from the actual source, not a freehand label')
        key = bounded(ref['id'], 'source id', 40)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', key) or key in mapped:
            raise ValueError('Source ids must be unique identifiers')
        for field, limit in [('url', 2000), ('locator', 300), ('quote', 6000)]:
            bounded(ref[field], field, limit)
        if urlparse(ref['url']).scheme not in {'https', 'http'}:
            raise ValueError('Use an actual public source URL')
        path = (directory / ref['source_file']).resolve()
        if not path.is_relative_to(directory):
            raise ValueError('Source text must be selected inside the plan workspace')
        if ref['url'] not in downloaded:
            downloaded[ref['url']] = fetch_source(ref['url'], directory)
        text, source_receipt = downloaded[ref['url']]
        if normalized_source_text(ref['quote']) not in normalized_source_text(text):
            raise ValueError('Quote absent from actual retrieved source text: ' + key)
        label, bibliographic_receipts = verified_bibliography(ref['bibliography'], text)
        verify_title(ref['bibliography'], source_receipt)
        labels.append(label)
        locator, locator_receipt = verified_source_locator(text, ref['quote'], source_receipt, ref['locator'])
        locators.append(locator)
        mapped[key] = index
        receipts.append({'id': key, **source_receipt,
                         'quote': ref['quote'], 'locator': locator, 'provided_locator': ref['locator'],
                         'locator_verification': locator_receipt, 'bibliography': bibliographic_receipts})
    claim_receipts = []

    def authored(rows, label, clinical=False):
        max_rows = 3 if clinical else 5
        if not isinstance(rows, list) or not 1 <= len(rows) <= max_rows:
            raise ValueError(label + ' requires 1-' + str(max_rows) + ' concise newly authored paragraphs')
        if clinical and sum(len(str(row.get('text', ''))) for row in rows if isinstance(row, dict)) > 360:
            raise ValueError('A short clinical explanation must fit 360 characters in total. Keep the core sourced analysis; '
                             'remove unrelated background, duplicated caveats and additional advice, not necessary evidence or conditions.')
        if not clinical and len(rows) >= 3 and sum(
                row.get('kind') in ('conditional', 'action') for row in rows if isinstance(row, dict)) > 2:
            raise ValueError('A short patient explanation must not become a chain of hypothetical clauses. '
                             'Use natural general statements for sourced general knowledge, and at most two genuinely unknown '
                             'conditions/actions. Do not move patient hypotheses into unconditional facts to satisfy this format.')
        output = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Authored paragraph must be an object')
            if clinical:
                fields = {'text', 'source_ids', 'scope'}
                text = bounded(row.get('text'), label, 200)
            elif row.get('kind') == 'general':
                fields = {'kind', 'text', 'source_ids'}
                text = single_clause(row.get('text'), label, 220)
                if re.search(r'您|你|本例|该患者|这位患者|your\b|you\b', text, re.I):
                    raise ValueError('General knowledge must not assert this patient has findings; '
                                     'use conditional condition/effect for individual hypotheses')
                text = text + '。'
            elif row.get('kind') == 'conditional':
                fields = {'kind', 'condition', 'effect', 'source_ids'}
                condition = single_clause(row.get('condition'), 'unverified condition', 120)
                effect = single_clause(row.get('effect'), 'possible effect', 220)
                if condition in source or any(condition in fact for fact in facts):
                    raise ValueError('A conditional premise must be an actual unverified applicability condition, '
                                     'not merely repeat a known input fact')
                if not re.search(r'可能|或许|或可|may\b|might\b|could\b', effect, re.I):
                    raise ValueError('A hypothetical effect must keep uncertainty in its own clause')
                text = '如果' + condition + '，' + effect + '。'
            elif row.get('kind') == 'action':
                fields = {'kind', 'condition', 'action', 'source_ids'}
                condition = single_clause(row.get('condition'), 'action condition', 120)
                action = single_clause(row.get('action'), 'supported action', 220)
                text = '如果' + condition + '，' + action + '。'
            else:
                raise ValueError('Patient statements require kind=general with text, or kind=conditional '
                                 'with condition/effect, or kind=action with condition/action; '
                                 'unqualified free-form patient paragraphs are not supported')
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError('Invalid authored paragraph fields')
            ids = row['source_ids']
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or any(x not in mapped for x in ids):
                raise ValueError('Every paragraph requires existing source ids')
            quotes = [refs[mapped[key] - 1]['quote'] for key in ids]
            verify_exclusive_language(text, quotes)
            if row.get('kind') != 'action' and contains_clinical_action(text):
                raise ValueError('Clinical action advice cannot be disguised as general knowledge or a hypothetical effect. '
                                 'Keep analysis focused on sourced explanation. Move an applicable action to a separate '
                                 'kind=action statement with its actual source trigger and response, or omit unsupported extra advice.')
            if not clinical and row.get('kind') == 'action':
                verify_action_fragments(condition, action, quotes)
            if re.search(r'\[[0-9]+\]', text):
                raise ValueError('Citation numbers are generated from source_ids, not handwritten')
            prefix = ''
            if clinical:
                if row['scope'] not in {'general', 'conditional'}:
                    raise ValueError('Clinical points must be general or conditional, not invented patient facts')
                prefix = '一般特点：' if row['scope'] == 'general' else '条件解释：'
            output.append(prefix + text + ''.join('[' + str(mapped[x]) + ']' for x in ids))
            claim_receipts.append({'audience': 'clinician' if clinical else 'patient',
                                   'claim': text, 'source_ids': ids})
        return output
    lines = ['## 本例已知', '', '；'.join(facts).rstrip('。；') + '。', '', '## 医生参考', '']
    for paragraph in authored(plan['clinical_points'], 'clinical_points', True):
        lines.extend([paragraph, ''])
    lines.extend(['## 患者话术', ''])
    lines.extend([' '.join(authored(plan['patient_paragraphs'], 'patient_paragraphs')), ''])
    used = {key for item in claim_receipts for key in item['source_ids']}
    unused = sorted(set(mapped) - used)
    if unused:
        raise ValueError('References listed but not linked to any statement: ' + ', '.join(unused) +
                         '. Match each actual claim to the source passage that supports it; remove genuinely unused references. '
                         'Do not merely attach an unrelated source to silence this check.')
    lines.extend(['## 依据', ''])
    for index, ref in enumerate(refs, 1):
        lines.append(f"[{index}] {labels[index - 1]}；{locators[index - 1]}。{ref['url']}")
    lines.extend(['', '草稿，待医生确认。'])
    mapping = {'input_sha256': hashlib.sha256(source.encode()).hexdigest(), 'fact_quotes': facts,
               'claims': claim_receipts,
               'sources': receipts, 'validation_scope': 'literal quotations, reference linkage and formatting only',
               'not_validated': ['source authenticity', 'clinical truth', 'interpretation', 'individual applicability']}
    return '\n'.join(lines) + '\n', mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(Path.cwd().resolve()) or destination.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Output must be inside the current task workspace, outside Skill resources')
    if args.plan.stat().st_size > 128 * 1024:
        raise ValueError('Plan exceeds size limit')
    body, mapping = render(json.loads(args.plan.read_text()), args.plan.resolve().parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding='utf-8')
    destination.with_suffix('.sources.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2)+'\n')
    audit = {'claims': mapping['claims'],
             'supporting_quotes': [{'id': item['id'], 'quote': item['quote'], 'locator': item['locator']}
                                   for item in mapping['sources']]}
    print('INTERNAL SUPPORT REVIEW — not patient-facing content. Literal checks passed; semantic support is NOT verified.')
    print('逐项核对下列正文与支撑引句。只谈同一主题、介绍研究背景或说明诊察流程，不足以支持具体病机、饮食建议或个案结论。缺支持时删除该断言或找到实际适用来源后重新生成；不要另写一份与文件不同的正文。')
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print('FINAL DRAFT FILE CONTENT:')
    print(body, end='')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print('Source-bound communication draft stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
