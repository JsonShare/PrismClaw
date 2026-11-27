"""Extract a complete, identified main-figure caption from an explicitly supplied JATS XML."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET

from _source_notes import panel_contexts, panel_excerpt


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def figure_number(value):
    compact = re.sub(r'[\s.:-]+', '', unicodedata.normalize('NFKC', value)).casefold()
    match = re.fullmatch(r'(?:figure|fig|f|图)?([1-9][0-9]{0,2})', compact)
    return int(match.group(1)) if match else None


def node_text(element):
    return ''.join(element.itertext()).strip() if element is not None else None


def article_metadata(tree):
    meta = next((e for e in tree.iter() if local_name(e.tag) == 'article-meta'), None)
    journal = next((e for e in tree.iter() if local_name(e.tag) == 'journal-meta'), None)
    result = {}
    if journal is not None:
        result['journalTitle'] = next((node_text(e) for e in journal.iter()
                                       if local_name(e.tag) == 'journal-title'), None)
    if meta is not None:
        authors = []
        for group in meta:
            if local_name(group.tag) != 'contrib-group':
                continue
            for contrib in group:
                role = contrib.get('contrib-type') or group.get('content-type')
                if local_name(contrib.tag) != 'contrib' or role != 'author':
                    continue
                name = next((e for e in contrib if local_name(e.tag) in ('name', 'string-name')), None)
                collaboration = next((node_text(e) for e in contrib if local_name(e.tag) == 'collab'), None)
                if name is not None:
                    fields = {local_name(e.tag): node_text(e) for e in name}
                    display = ' '.join(fields[k] for k in ('given-names', 'surname') if fields.get(k))
                    authors.append({'name': display or node_text(name), **fields})
                elif collaboration:
                    authors.append({'name': collaboration, 'collaboration': True})
        result['firstAuthor'] = authors[0] if authors else None
        result['authors'] = authors[:100]
        result['authorCount'] = len(authors)
        result['authorsTruncated'] = len(authors) > 100
        for tag in ('volume', 'issue', 'fpage', 'lpage', 'elocation-id'):
            result[tag] = next((node_text(e) for e in meta if local_name(e.tag) == tag), None)
        result['publicationDates'] = [
            {'attributes': dict(e.attrib), 'parts': {local_name(c.tag): node_text(c) for c in e}}
            for e in meta if local_name(e.tag) == 'pub-date']
    return result


def matching_paragraphs(tree, terms):
    if not terms:
        return []
    if len(terms) > 5 or any(not term.strip() or len(term) > 80 for term in terms):
        raise ValueError('Use one through five specific source terms of at most 80 characters')
    parents = {child: parent for parent in tree.iter() for child in parent}
    results = []
    for paragraph in tree.iter():
        if local_name(paragraph.tag) != 'p':
            continue
        text = node_text(paragraph)
        if not all(term.casefold() in text.casefold() for term in terms):
            continue
        xml = ET.tostring(paragraph, encoding='unicode')
        if len(xml.encode('utf-8')) > 16000:
            raise ValueError('Matching paragraph too long; not truncated')
        current = paragraph
        section = None
        while current in parents:
            current = parents[current]
            if local_name(current.tag) == 'sec':
                section = next((node_text(e) for e in current if local_name(e.tag) == 'title'), None)
                break
        results.append({'paragraphId': paragraph.get('id'), 'sectionTitle': section,
                        'text': text, 'xml': xml,
                        'superscripts': [node_text(e) for e in paragraph.iter() if local_name(e.tag) == 'sup'],
                        'subscripts': [node_text(e) for e in paragraph.iter() if local_name(e.tag) == 'sub'],
                        'completeParagraph': True})
    if len(results) > 8 or sum(len(item['xml'].encode('utf-8')) for item in results) > 50000:
        raise ValueError('Too many matching contexts; narrow terms instead of truncating source text')
    return results


def extract_caption(source, number, terms=(), panel=None):
    source = Path(source)
    if type(number) is not int or not 1 <= number <= 999:
        raise ValueError('A main figure number from 1 through 999 is required')
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('Source XML exceeds 25 MiB')
    raw = source.read_bytes()
    tree = ET.fromstring(raw)
    candidates = []
    figures = [element for element in tree.iter() if local_name(element.tag) == 'fig']
    for figure in figures:
        labels = [child for child in figure if local_name(child.tag) == 'label']
        label = ''.join(labels[0].itertext()) if len(labels) == 1 else ''
        identifier = figure.get('id', '')
        if figure_number(label) == number or figure_number(identifier) == number:
            if labels and figure_number(label) is None:
                # A supplementary label must not be overridden by a numeric ID.
                continue
            if figure_number(label) not in [None, number] or figure_number(identifier) not in [None, number]:
                raise ValueError('Figure ID and label disagree')
            candidates.append((figure, label, identifier))
    if len(candidates) != 1:
        raise ValueError('Main figure caption missing or ambiguous; no other figure substituted')
    figure, label, identifier = candidates[0]
    captions = [child for child in figure if local_name(child.tag) == 'caption']
    if len(captions) != 1:
        raise ValueError('A single complete caption is required')
    caption = captions[0]
    text = ''.join(caption.itertext()).strip()
    serialized = ET.tostring(caption, encoding='unicode')
    if not text or len(serialized.encode('utf-8')) > 100_000:
        raise ValueError('Caption empty or exceeds limit; not truncated')
    article_meta = next((e for e in tree.iter() if local_name(e.tag) == 'article-meta'), None)
    doi = None
    title = None
    if article_meta is not None:
        doi_node = next((e for e in article_meta if local_name(e.tag) == 'article-id'
                         and e.get('pub-id-type') == 'doi'), None)
        if doi_node is not None:
            doi = ''.join(doi_node.itertext()).strip()
        title_node = next((e for e in article_meta.iter() if local_name(e.tag) == 'article-title'), None)
        if title_node is not None:
            title = ''.join(title_node.itertext()).strip()
    contexts = panel_contexts(figure, panel) if panel is not None else []
    selection = 'not_requested'
    if panel is not None:
        if len(contexts) == 1:
            selection = 'unique'
        elif contexts:
            selection = 'multiple_segments'
        else:
            selection = 'not_found'
    return {
        'schema': 1,
        'scope': '原文提取；不证明病例适用性、研究设计或统计方法已确认',
        'sourceFile': source.name,
        'sourceSha256': hashlib.sha256(raw).hexdigest(),
        'sourceBytes': len(raw),
        'doi': doi,
        'figureElementId': identifier,
        'figureLabel': label,
        'mainFigureNumber': number,
        'caption': text,
        'completeCaption': True,
        'selectedPanel': panel,
        'panelSelectionStatus': selection,
        'panelCaption': contexts[0]['text'] if len(contexts) == 1 else None,
        'panelContexts': contexts,
        'panelContextScope': 'Only explicit caption markers are matched. Shared labels remain shared; inline references and unlabelled conditions may need the full caption. This does not certify complete panel coverage or scientific applicability.',
        'articleTitle': title,
        'articleMetadata': article_metadata(tree),
        'contextSearchTerms': list(terms),
        'sourceParagraphs': matching_paragraphs(tree, terms),
        'captionXml': serialized,
        'sourceFigureElements': len(figures),
        'inferencePerformed': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--figure', type=int, required=True)
    parser.add_argument('--panel', help='Actual subpanel letter; full caption remains available separately')
    parser.add_argument('--term', action='append', default=[], help='All supplied literal terms must occur in a complete source paragraph')
    args = parser.parse_args()
    print(json.dumps(extract_caption(args.input, args.figure, args.term, args.panel), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as error:
        print('Source caption extraction stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
