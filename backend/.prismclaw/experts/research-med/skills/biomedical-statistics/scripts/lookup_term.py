"""Find bounded, real source passages for a supplied term; never infer its study identity."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from retrieve_article import BASE, OfficialRedirects, normalize_doi, validate_url, verify_article
from _source_notes import article_reference, normalized

MAX_SOURCE = 8 * 1024 * 1024


def validate_term(term, label):
    if not isinstance(term, str) or not 1 <= len(term.strip()) <= 80 or re.search(r'["\\\n\r\x00-\x1f]', term):
        raise ValueError(label + ' requires a short literal scientific term, not a query expression')
    return term.strip()


def passages(raw, term, context=None):
    if b'<!ENTITY' in raw.upper():
        raise ValueError('XML entity declarations are not supported')
    root = ET.fromstring(raw)
    parents = {child: parent for parent in root.iter() for child in parent}
    local = lambda node: node.tag.rsplit('}', 1)[-1]
    pieces = [piece for piece in re.split(r'[\s‐‑–-]+', term) if piece]
    phrase = r'[\s‐‑–-]+'.join(re.escape(piece) for piece in pieces)
    token = re.compile(r'(?<![A-Za-z0-9])' + phrase + r'(?![A-Za-z0-9])', re.I)
    output = []
    for node in root.iter():
        if local(node) != 'p':
            continue
        text = normalized(''.join(node.itertext()))
        if not token.search(text) or len(text) > 5000:
            continue
        cursor = node
        locator = None
        in_references = False
        while cursor is not None:
            if local(cursor) in ('back', 'ref-list', 'ref'):
                in_references = True
            if locator is None and cursor.get('id'):
                locator = cursor.get('id')
            cursor = parents.get(cursor)
        if in_references or not locator:
            continue
        parenthetical = bool(re.search(r'\(\s*' + re.escape(term) + r'\s*\)', text, re.I))
        contextual = bool(context and context.casefold() in text.casefold())
        output.append({'element_id': locator, 'quote': text,
                       'parentheticalTerm': parenthetical, 'contextMentioned': contextual})
    output.sort(key=lambda item: (not item['parentheticalTerm'], not item['contextMentioned'], len(item['quote'])))
    return output[:4], article_reference(root)


def lookup_term(term, output_dir, context=None, fetch=None, clock=time.monotonic):
    term = validate_term(term, 'term')
    context = validate_term(context, 'context') if context else None
    output_dir = Path(output_dir).resolve()
    if not output_dir.is_relative_to(Path.cwd().resolve()) or output_dir.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Save evidence inside the current task workspace, outside skill resources')
    deadline = clock() + 60

    def bounded_fetch(url, limit):
        validate_url(url)
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('Term source lookup reached its 60 second budget')
        opener = urllib.request.build_opener(OfficialRedirects())
        request = urllib.request.Request(url, headers={'User-Agent': 'PrismClaw-TermReader/1.0'})
        with opener.open(request, timeout=min(15, remaining)) as response:
            validate_url(response.url)
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Term source exceeds size limit')
        return raw

    fetch = fetch or bounded_fetch
    query = 'OPEN_ACCESS:Y AND "' + term + '"'
    if context:
        query += ' AND "' + context + '"'
    search_url = BASE + 'search?' + urllib.parse.urlencode({'query': query, 'format': 'json', 'pageSize': 5})
    search_raw = fetch(search_url, 1024 * 1024)
    search = json.loads(search_raw)
    rows = search.get('resultList', {}).get('result', [])
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []
    attempted = set()
    for row in rows:
        if len(attempted) >= 3 or clock() >= deadline:
            break
        pmcid = row.get('pmcid')
        if (not isinstance(pmcid, str) or not re.fullmatch(r'PMC[0-9]+', pmcid)
                or pmcid in attempted or row.get('isOpenAccess') != 'Y' or not row.get('doi')):
            continue
        attempted.add(pmcid)
        source_url = BASE + pmcid + '/fullTextXML'
        try:
            doi = normalize_doi(row['doi'])
            raw = fetch(source_url, MAX_SOURCE)
            if len(raw) > MAX_SOURCE:
                raise ValueError('Term source exceeds size limit')
            verify_article(raw, doi)
            contexts, bibliography = passages(raw, term, context)
            sha = hashlib.sha256(raw).hexdigest()
            path = output_dir/(pmcid + '-' + sha[:16] + '.xml')
            if path.exists():
                if path.is_symlink() or path.read_bytes() != raw:
                    raise ValueError('Existing source differs; refusing to overwrite')
            else:
                with path.open('xb') as stream:
                    stream.write(raw)
            results.append({'doi': doi, 'pmcid': pmcid, 'sourceFile': str(path), 'sourceUrl': source_url,
                            'sourceSha256': sha, 'sourceBytes': len(raw), 'bibliography': bibliography,
                            'passages': contexts, 'doiVerifiedInArticleMetadata': True})
        except (ValueError, OSError, ET.ParseError) as error:
            failures.append({'pmcid': pmcid, 'error': type(error).__name__ + ': ' + str(error)})
    receipt = {'term': term, 'context': context, 'searchUrl': search_url,
               'searchResponseSha256': hashlib.sha256(search_raw).hexdigest(),
               'hitCount': search.get('hitCount'), 'articlesAttempted': len(attempted),
               'results': results, 'failures': failures,
               'retrievedAt': datetime.now(timezone.utc).isoformat(),
               'scope': '候选原文片段，不是自动释义。核对词义后才能引用；额外文献不能确定原研究异构体、材料、模型或实验方法。未取得片段不代表没有可靠定义。',
               'limits': {'maxArticles': 3, 'maxArticleBytes': MAX_SOURCE, 'totalBudgetSeconds': 60, 'retryCount': 0}}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (output_dir/('term-lookup-' + stamp + '.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--term', required=True)
    parser.add_argument('--context')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(lookup_term(args.term, args.output_dir, args.context), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as error:
        print('Term lookup stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
