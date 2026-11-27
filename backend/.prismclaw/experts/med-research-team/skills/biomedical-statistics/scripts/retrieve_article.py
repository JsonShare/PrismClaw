"""Retrieve one DOI-matched open-access JATS article; never infer source facts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


BASE = 'https://www.ebi.ac.uk/europepmc/webservices/rest/'
MAX_XML = 25 * 1024 * 1024


def normalize_doi(value):
    value = value.strip()
    value = re.sub(r'^https://(?:dx\.)?doi\.org/', '', value, flags=re.I)
    if not re.fullmatch(r'10\.[0-9]{4,9}/[^\s"<>\x00-\x1f]+', value) or len(value) > 512:
        raise ValueError('Supply an explicit valid DOI, not a title or search query')
    return value.casefold()


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'www.ebi.ac.uk'
            or not parsed.path.startswith('/europepmc/webservices/rest/')):
        raise ValueError('Only the official Europe PMC REST endpoint is supported')


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_bytes(url, limit):
    validate_url(url)
    opener = urllib.request.build_opener(OfficialRedirects())
    request = urllib.request.Request(url, headers={'User-Agent': 'DoctorBuddy-SourceReader/1.0'})
    with opener.open(request, timeout=45) as response:
        validate_url(response.url)
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Source exceeds retrieval size limit; not truncated')
    return raw


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def verify_article(raw, doi):
    if len(raw) > MAX_XML:
        raise ValueError('Source XML exceeds 25 MiB')
    tree = ET.fromstring(raw)
    if local_name(tree.tag) != 'article':
        raise ValueError('Full text is not a JATS article')
    front = next((e for e in tree if local_name(e.tag) == 'front'), None)
    meta = next((e for e in front if local_name(e.tag) == 'article-meta'), None) if front is not None else None
    if meta is None:
        raise ValueError('Article metadata missing')
    article_dois = {normalize_doi(''.join(e.itertext())) for e in meta
                    if local_name(e.tag) == 'article-id' and e.get('pub-id-type') == 'doi'}
    if article_dois != {doi}:
        raise ValueError('Downloaded article DOI does not uniquely match the requested DOI')
    if not any(local_name(e.tag) == 'body' for e in tree):
        raise ValueError('Full article body missing; metadata is not full text')


def retrieve_article(doi, output_dir, fetch=fetch_bytes):
    doi = normalize_doi(doi)
    output_dir = Path(output_dir).resolve()
    if not output_dir.is_relative_to(Path.cwd().resolve()):
        raise ValueError('Output directory must be inside the current workspace')
    skill_dir = Path(__file__).resolve().parents[1]
    if output_dir.is_relative_to(skill_dir):
        raise ValueError('Do not write source downloads into skill resources')
    query = urllib.parse.urlencode({'query': 'DOI:"' + doi + '"', 'format': 'json', 'pageSize': 25})
    search_url = BASE + 'search?' + query
    search_raw = fetch(search_url, 1024 * 1024)
    if len(search_raw) > 1024 * 1024:
        raise ValueError('Search response exceeds limit')
    search = json.loads(search_raw)
    if int(search.get('hitCount', 0)) > 25:
        raise ValueError('DOI search ambiguous; refusing a partial result list')
    matches = []
    for row in search.get('resultList', {}).get('result', []):
        if str(row.get('doi', '')).casefold() == doi:
            matches.append(row)
    pmcids = {row.get('pmcid') for row in matches}
    if len(pmcids) != 1:
        raise ValueError('No unique DOI-matched PMC article; no substitute selected')
    pmcid = next(iter(pmcids))
    if not isinstance(pmcid, str) or not re.fullmatch(r'PMC[0-9]+', pmcid):
        raise ValueError('Matched article has no usable PMCID')
    if not any(row.get('isOpenAccess') == 'Y' for row in matches):
        raise ValueError('Matched article is not marked open access')
    source_url = BASE + pmcid + '/fullTextXML'
    raw = fetch(source_url, MAX_XML)
    verify_article(raw, doi)
    digest = hashlib.sha256(raw).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = output_dir / (pmcid + '-' + digest[:16] + '.xml')
    if source.exists():
        if source.is_symlink() or source.read_bytes() != raw:
            raise ValueError('Existing source differs; refusing to overwrite')
    else:
        with source.open('xb') as stream:
            stream.write(raw)
    receipt = {
        'doi': doi, 'pmcid': pmcid, 'sourceFile': str(source),
        'sourceUrl': source_url, 'searchUrl': search_url,
        'sourceSha256': digest, 'sourceBytes': len(raw),
        'searchResponseSha256': hashlib.sha256(search_raw).hexdigest(),
        'retrievedAt': datetime.now(timezone.utc).isoformat(),
        'doiVerifiedInArticleMetadata': True,
        'scope': '原文获取与身份核对；未推断图注、实验设计或统计含义',
    }
    # Each retrieval has its own receipt; preserve earlier retrieval timestamps.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    receipt_file = output_dir / (pmcid + '-' + stamp + '.json')
    with receipt_file.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    receipt['receiptFile'] = str(receipt_file)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--doi', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(retrieve_article(args.doi, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as error:
        print('Article retrieval stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
