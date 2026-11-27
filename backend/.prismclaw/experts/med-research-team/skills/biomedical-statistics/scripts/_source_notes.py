import copy
import hashlib
import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import openpyxl


def normalized(text):
    return ' '.join(text.split())


def article_reference(tree):
    """Transcribe this article's front matter, never bibliography/back matter."""
    local = lambda node: node.tag.rsplit('}', 1)[-1]
    front = next((node for node in tree if local(node) == 'front'), None)
    if front is None:
        return None
    meta = next((node for node in front if local(node) == 'article-meta'), None)
    if meta is None:
        return None
    text = lambda node: normalized(''.join(node.itertext()))
    fields = {local(node): text(node) for node in meta
              if local(node) in ('volume', 'issue', 'fpage', 'lpage', 'elocation-id')}
    title_group = next((node for node in meta if local(node) == 'title-group'), ())
    title = next((text(node) for node in title_group if local(node) == 'article-title'), None)
    fields['title'] = title
    fields['journal'] = next((text(node) for node in front.iter() if local(node) == 'journal-title'), None)
    fields['doi'] = next((text(node) for node in meta
                          if local(node) == 'article-id' and node.get('pub-id-type') == 'doi'), None)
    dates = [node for node in meta if local(node) == 'pub-date']
    dates.sort(key=lambda node: 0 if node.get('pub-type') == 'ppub' or node.get('publication-format') == 'print' else 1)
    fields['year'] = next((text(child) for node in dates for child in node if local(child) == 'year'), None)
    names = []
    for group in meta:
        if local(group) != 'contrib-group':
            continue
        for person in group:
            if local(person) != 'contrib' or (person.get('contrib-type') or group.get('content-type')) != 'author':
                continue
            name = next((node for node in person if local(node) in ('name', 'string-name', 'collab')), None)
            if name is not None:
                names.append(normalized(' '.join(name.itertext())))
    fields['first_author'] = names[0] if names else None
    fields['author_count'] = len(names)
    if not title:
        return None
    parts = []
    if names:
        parts.append(names[0] + (' et al.' if len(names) > 1 else ''))
    parts.extend(value for value in [title, fields['journal'], fields['year']] if value)
    volume = fields.get('volume', '')
    if fields.get('issue'):
        volume += '(' + fields['issue'] + ')'
    pages = fields.get('fpage') or fields.get('elocation-id') or ''
    if fields.get('lpage') and fields.get('fpage') != fields['lpage']:
        pages += '–' + fields['lpage']
    if volume or pages:
        parts.append(volume + (':' if volume and pages else '') + pages)
    if fields['doi']:
        parts.append('https://doi.org/' + fields['doi'])
    return {'fields': fields, 'text': '; '.join(parts), 'locator': 'front/article-meta'}


def caption_text_and_labels(paragraph):
    parts = []
    labels = []
    length = 0

    def append(text):
        nonlocal length
        parts.append(text)
        length += len(text)

    def visit(node):
        start = length
        append(node.text or '')
        for child in node:
            visit(child)
            append(child.tail or '')
        if node.tag.rsplit('}', 1)[-1] in {'bold', 'b'}:
            value = ''.join(node.itertext())
            if re.fullmatch(r'[a-z](?:\s*[,–-]\s*[a-z])*[,.]?', value):
                labels.extend(start + match.start() for match in re.finditer('[a-z]', value))

    visit(paragraph)
    return ''.join(parts), labels


def marker_labels(marker):
    labels = set()
    for group in re.finditer(r'([a-z])(?:\s*[–-]\s*([a-z]))?', marker):
        first, last = group.group(1), group.group(2) or group.group(1)
        labels.update(chr(value) for value in range(ord(first), ord(last) + 1))
    return labels


def panel_contexts(figure, panel):
    """Return matching caption segments with their original explicit label scopes."""
    if not re.fullmatch(r'[a-z]', str(panel)):
        raise ValueError('Use one explicit lowercase source panel letter')
    local = lambda node: node.tag.rsplit('}', 1)[-1]
    caption = next((node for node in figure if local(node) == 'caption'), None)
    if caption is None:
        raise ValueError('The original figure has no caption')
    paragraphs = [node for node in caption if local(node) == 'p']
    if not paragraphs:
        paragraphs = [caption]
    matches = []
    for paragraph in paragraphs:
        text, labels = caption_text_and_labels(paragraph)
        markers = {}
        for start in re.finditer(r'(?:^|(?<=[.!?。])\s+)([a-z](?:\s*[,–-]\s*[a-z])*)\s*[,.](?=\s|$)', text):
            markers[start.start(1)] = marker_labels(start.group(1))
        for offset in labels:
            if text[:offset].strip() and re.search(r'[.!?。]\s+$', text[:offset]) is None:
                continue
            marker = re.match(r'[a-z](?:\s*[,–-]\s*[a-z])*(?:[,.])?(?=\s|$)', text[offset:])
            if marker is None:
                continue
            letter_offsets = [offset + letter.start() for letter in re.finditer('[a-z]', marker.group())]
            if not all(position in labels for position in letter_offsets):
                continue
            markers[offset] = marker_labels(marker.group())
        starts = sorted(markers)
        for index, start in enumerate(starts):
            if panel in markers[start]:
                end = starts[index + 1] if index + 1 < len(starts) else len(text)
                matches.append({'labels': sorted(markers[start]), 'text': normalized(text[start:end])})
    return matches


def panel_excerpt(figure, panel):
    """Select one unambiguous segment for strict evidence validation."""
    matches = panel_contexts(figure, panel)
    if len(matches) != 1:
        raise ValueError('Source panel caption missing or ambiguous; do not substitute another panel or the whole figure')
    return matches[0]['text']


def checked_article(path):
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('Article source exceeds 25 MiB')
    data = path.read_bytes()
    if b'<!ENTITY' in data.upper():
        raise ValueError('XML entity declarations are not supported')
    try:
        tree = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError('Invalid source XML') from error
    if tree.tag.rsplit('}', 1)[-1] != 'article':
        raise ValueError('Source must be an actual JATS article')
    return tree, hashlib.sha256(data).hexdigest(), data


def verify_notes(raw, suffix, plan, plan_directory):
    verified = copy.deepcopy(plan)
    receipts = []
    snapshots = {}
    workbook = None
    xml_cache = {}
    try:
        notes = verified.get('caption_notes', [])
        if not isinstance(notes, list) or len(notes) > 12:
            raise ValueError('caption_notes must contain at most 12 notes')
        entries = [('caption_notes', index, note) for index, note in enumerate(notes)]
        verified.pop('_source_label_differences', None)
        requirements = {}
        if plan.get('source_origin') == 'published_data':
            for block in plan.get('blocks', []):
                sheet = str(block.get('sheet', ''))
                match = re.fullmatch(r'Fig(?:ure)?[. ]*([1-9][0-9]*)[. _-]([a-z])', sheet, re.I)
                if match:
                    requirements[sheet] = (int(match.group(1)), match.group(2).lower())
        scopes = verified.get('figure_sources', [])
        if not isinstance(scopes, list) or len(scopes) > 12:
            raise ValueError('figure_sources must contain at most 12 actual figure/panel source bindings')
        by_sheet = {}
        for scope in scopes:
            if not isinstance(scope, dict) or set(scope) != {'sheet', 'file', 'element_id', 'panel'}:
                raise ValueError('Each figure_sources entry requires sheet, file, element_id, panel')
            if scope['sheet'] in by_sheet:
                raise ValueError('Use one unambiguous original panel for each source sheet')
            by_sheet[scope['sheet']] = scope
        for sheet, (number, panel) in requirements.items():
            scope = by_sheet.get(sheet)
            if scope is None:
                raise ValueError('Published figure sheet ' + sheet + ' requires figure_sources with its actual XML file, '
                                 'figure element_id and panel. Read source_caption.py --figure NUMBER --panel LETTER; '
                                 'a neighbouring panel or general model-method paragraph cannot establish this panel group identity.')
            if scope['panel'] != panel:
                raise ValueError('figure_sources panel differs from the actual source worksheet')
            path = (Path(plan_directory) / scope['file']).resolve()
            tree, sha, data = checked_article(path)
            xml_cache[path] = (tree, sha)
            snapshots['source-' + sha + '.xml'] = data
            figures = [node for node in tree.iter()
                       if node.tag.rsplit('}', 1)[-1] == 'fig' and node.get('id') == scope['element_id']]
            if len(figures) != 1:
                raise ValueError('figure_sources must identify one original figure')
            figure = figures[0]
            label = next((''.join(node.itertext()) for node in figure
                          if node.tag.rsplit('}', 1)[-1] == 'label'), scope['element_id'])
            if not re.fullmatch(r'(?:Fig(?:ure)?[. ]*|F)' + str(number), label.strip(), re.I):
                raise ValueError('Original figure label differs from the source worksheet figure')
            excerpt = panel_excerpt(figure, panel)
            evidence = {'kind': 'article_xml', 'file': scope['file'], 'element_id': scope['element_id'],
                        'quote': excerpt}
            entries.append(('figure_sources', len(entries), {'evidence': [evidence]}))
            scope['file'] = 'source-' + sha + '.xml'
            labels = set()
            for block in plan.get('blocks', []):
                if block.get('sheet') == sheet:
                    labels.update(scientific_identifiers(block.get('group', '')))
            original_ids = scientific_identifiers(excerpt)
            for identifier in sorted(labels - original_ids):
                verified.setdefault('_source_label_differences', []).append({
                    'sheet': sheet, 'input_identifier': identifier,
                    'panel_identifiers': sorted(original_ids), 'panel_excerpt': excerpt,
                    'source_sha256': sha, 'locator': scope['element_id'] + ', panel ' + panel})
        comparisons = verified.get('reported_comparisons', [])
        if not isinstance(comparisons, list) or len(comparisons) > 50:
            raise ValueError('At most 50 reported comparisons supported')
        for index, comparison in enumerate(comparisons):
            mapping = comparison.get('source_group_mapping')
            if isinstance(mapping, dict) and mapping.get('kind') == 'original_source':
                evidence = mapping.get('evidence')
                claim = mapping.get('quote')
                if not evidence or not isinstance(claim, str) or not claim.strip():
                    raise ValueError('original_source comparison mapping requires actual source evidence and a verbatim quote. '
                                     'An authored interpretation cannot stand in for original source text; '
                                     'ask only the necessary scientific mapping if no direct source supports it.')
                entries.append(('reported_comparison_mapping', index, {'evidence': evidence, 'verbatim_claim': claim}))
            status = comparison.get('adjustment', 'adjusted' if comparison.get('adjusted') is True else None)
            if status in ('adjusted', 'unadjusted'):
                evidence = comparison.get('adjustment_evidence')
                if not evidence:
                    raise ValueError('Explicit P adjustment status requires adjustment_evidence from the actual source; '
                                     'if the source does not state adjustment, use unknown, never unadjusted')
                entries.append(('reported_comparisons', index, {'evidence': evidence}))
        for field, index, note in entries:
            if not isinstance(note, dict):
                raise ValueError('Each caption note must be an object')
            if field == 'caption_notes' and note.get('topic') == 'original_method' and re.search(
                    r'本次|脚本|模拟|复现|重算|复算|this (?:run|script)', note.get('text', ''), re.I):
                raise ValueError('original_method describes only the original study method. '
                                 'Remove current-run, script or recalculation claims; the report generates actual execution facts.')
            evidence = note.get('evidence')
            if not isinstance(evidence, list) or not 1 <= len(evidence) <= 6:
                raise ValueError('Each caption note requires 1-6 evidence entries with actual source quotes')
            quotes = []
            source_labels = []
            article_bound = False
            for item in evidence:
                if not isinstance(item, dict):
                    raise ValueError('Source evidence must be an object')
                quote = item.get('quote')
                if not isinstance(quote, str) or not quote.strip() or len(quote) > 6000:
                    raise ValueError('Source quote must be nonempty and at most 6000 characters')
                kind = item.get('kind')
                if kind == 'input_cell':
                    if suffix != '.xlsx':
                        raise ValueError('input_cell evidence requires the actual XLSX input')
                    if workbook is None:
                        workbook = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
                    cell = item.get('cell', '')
                    if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', cell):
                        raise ValueError('Source evidence requires one explicit cell')
                    sheet = item.get('sheet')
                    if sheet not in workbook.sheetnames:
                        raise ValueError('Source evidence sheet not found')
                    value = workbook[sheet][cell].value
                    actual = '' if value is None else str(value)
                    if quote != actual:
                        raise ValueError(f'Source quote differs from actual cell {sheet}!{cell}: {actual!r}; do not invent or silently repair a header')
                    source_hash = hashlib.sha256(raw).hexdigest()
                    locator = f'{sheet}!{cell}'
                elif kind == 'input_text':
                    if suffix != '.csv' or quote not in raw.decode('utf-8-sig'):
                        raise ValueError('Source quote not found in the actual CSV input')
                    source_hash = hashlib.sha256(raw).hexdigest()
                    locator = 'CSV input'
                elif kind == 'article_xml':
                    filename = item.get('file')
                    if not isinstance(filename, str) or not filename:
                        raise ValueError('article_xml evidence requires an explicit file')
                    path = (Path(plan_directory) / filename).resolve()
                    if path not in xml_cache:
                        tree, sha, data = checked_article(path)
                        xml_cache[path] = (tree, sha)
                        snapshots['source-' + sha + '.xml'] = data
                    tree, source_hash = xml_cache[path]
                    bibliography = article_reference(tree)
                    element_id = item.get('element_id')
                    if not isinstance(element_id, str) or not element_id:
                        raise ValueError('article_xml evidence requires the actual figure/paragraph element_id')
                    matches = [element for element in tree.iter() if element.get('id') == element_id]
                    if len(matches) != 1:
                        raise ValueError('Source element_id must identify exactly one original element')
                    actual = normalized(''.join(matches[0].itertext()))
                    if normalized(quote) not in actual:
                        raise ValueError('Source quote not found in the specified original XML element: ' + element_id)
                    item['file'] = 'source-' + source_hash + '.xml'
                    locator = element_id
                    if bibliography:
                        article_bound = True
                        source_labels.append(bibliography['text'] + '; ' + element_id)
                else:
                    raise ValueError('Unsupported source evidence kind; use input_cell, input_text, or article_xml')
                quotes.append(quote)
                receipt = {'field': field, 'note_index': index, 'kind': kind, 'locator': locator,
                           'quote': quote, 'source_sha256': source_hash}
                if kind == 'article_xml' and bibliography:
                    receipt['bibliography'] = bibliography
                elif kind in ('input_cell', 'input_text'):
                    source_labels.append(locator)
                receipts.append(receipt)
            if field == 'caption_notes' and article_bound:
                note['source'] = '；'.join(dict.fromkeys(source_labels))
            claim = note.get('verbatim_claim')
            if claim is not None and not any(normalized(claim) in normalized(quote) for quote in quotes):
                raise ValueError('Comparison mapping quote is absent from verified original source evidence. '
                                 'Do not label a constructed mapping as an original quotation.')
            # Quotation marks claim literal wording, so validate it separately
            # from a translated interpretation, which still needs human review.
            for literal in re.findall(r'“([^”]+)”|「([^」]+)」', note.get('text', '')):
                text = next(part for part in literal if part)
                if not any(normalized(text) in normalized(quote) for quote in quotes):
                    raise ValueError('Quoted wording in caption note is absent from its source evidence: ' + text)
        return verified, receipts, snapshots
    finally:
        if workbook is not None:
            workbook.close()


def scientific_identifiers(text):
    """Return distinctive literal identifiers, without guessing their meaning."""
    tokens = re.findall(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]{3,}(?![A-Za-z0-9])', str(text))
    return {token for token in tokens
            if re.search(r'[0-9]', token) or re.search(r'[a-z][A-Z]', token)}


def verify_figure_definitions(plan):
    """Require coverage declarations for figures using an original article.

    This checks declared topics only, not the truth or completeness of definitions.
    """
    notes = plan.get('caption_notes', [])
    if plan.get('source_origin') != 'published_data' and not any(
            item.get('kind') == 'article_xml' for note in notes for item in note.get('evidence', [])):
        return
    allowed = {'quantity', 'group', 'observation_unit', 'measurement_material', 'timepoint', 'original_method', 'other'}
    topics = set()
    for note in notes:
        if note.get('purpose') != 'reading_definition':
            continue
        if note.get('topic') not in allowed:
            raise ValueError('Article-based figure definitions require topic: quantity, group, observation_unit, measurement_material, '
                             'timepoint, original_method, or other; expand necessary scientific abbreviations from the source')
        topics.add(note['topic'])
    reading_length = sum(len(str(note.get('text', ''))) + sum(len(str(value)) for value in note.get('group_explanations', {}).values())
                         for note in notes if note.get('purpose') == 'reading_definition')
    if reading_length > 650:
        raise ValueError('Sourced reading definitions exceed 650 characters. Keep the essential quantity, group identity, '
                         'observation unit, measured material and method; remove repeated sample counts, repeated statistical '
                         'boilerplate, speculative exclusions and unnecessary protocol background. Do not remove essential evidence.')
    required = {'quantity', 'group', 'observation_unit', 'measurement_material'}
    if plan.get('reported_comparisons'):
        required.add('original_method')
    missing = sorted(required - topics)
    if missing:
        raise ValueError('Missing sourced figure definition topics: ' + ', '.join(missing) +
                         '. Read the actual caption/methods; explain labels and abbreviations, distinguish measurement units '
                         'from observation identity and measured specimen/material. Read the actual methods to distinguish '
                         'the experimental unit from the material measured. If a source really does not specify a fact, state that limited gap '
                         'against the relevant source rather than inventing it.')
    # This is a coverage check, not an abbreviation dictionary or semantic proof.
    symbols = set()
    for block in plan.get('blocks', []):
        name = str(block.get('panel', ''))
        if '_' in name:
            continue
        symbols.update(re.findall(r'(?<![A-Za-z0-9])(?:[A-Z]{2,}[0-9]*|[0-9]+[A-Z][A-Za-z0-9]*)(?![a-z])', name))
    definitions = ' '.join(str(note.get('text', '')) for note in notes
                           if note.get('purpose') == 'reading_definition')
    unresolved = set()
    displayed_labels = ' '.join(str(block.get(key, '')) for block in plan.get('blocks', [])
                                for key in ('panel', 'group'))
    for note in notes:
        declared = note.get('unresolved_expansions', [])
        if (not isinstance(declared, list) or len(declared) > 12
                or any(not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 30
                       or symbol not in displayed_labels for symbol in declared)):
            raise ValueError('unresolved_expansions must list actual displayed labels whose full meaning has not been verified')
        if declared and note.get('purpose') != 'reading_definition':
            raise ValueError('An unresolved abbreviation must be disclosed in the visible reading definition, not hidden in an audit')
        if any(symbol in note.get('expansions', {}) for symbol in declared):
            raise ValueError('The same abbreviation cannot be both expanded and unresolved')
        if any(symbol in note.get('text', '') for symbol in declared):
            raise ValueError('An unresolved label cannot also receive a meaning or interpretation in the same definition. '
                             'Remove that label from the authored text; the report already prints the original label and '
                             'its unverified long-form limitation. Obtain a real term definition before adding an explanation.')
        unresolved.update(declared)
    definitions += ' ' + ' '.join(unresolved)
    absent = sorted(symbol for symbol in symbols if symbol not in definitions)
    if absent:
        raise ValueError('Symbolic panel labels absent from reading definitions: ' + ', '.join(absent) +
                         '. Read the actual source for their full meaning and add one concise sourced definition. '
                         'Do not guess, silently rename the source labels, or treat this coverage check as semantic validation.')
    # A repeated abbreviation is not its expansion. Require an explicit, quoted
    # long form for conspicuous abbreviations in published figure/group labels.
    abbreviations = set()
    for block in plan.get('blocks', []):
        for key in ('panel', 'group'):
            value = str(block.get(key, ''))
            if '_' not in value:
                abbreviations.update(re.findall(
                    r'(?<![A-Za-z0-9])(?:[A-Z]{3,}[0-9]*|[0-9]+[A-Z]{2,})(?![A-Za-z0-9])', value))
    expanded = set()
    for note in notes:
        if note.get('purpose') != 'reading_definition':
            continue
        declarations = note.get('expansions', {})
        if not isinstance(declarations, dict):
            raise ValueError('expansions must map a displayed abbreviation to its original source long form')
        quotes = [normalized(str(item.get('quote', ''))) for item in note.get('evidence', [])]
        for symbol, meaning in declarations.items():
            if not isinstance(symbol, str) or symbol not in displayed_labels:
                raise ValueError('An expansion must explain an actual displayed label, not a related reagent or a different abbreviation')
            without_parenthesis = re.sub(r'\(\s*' + re.escape(symbol) + r'\s*\)', '', str(meaning), flags=re.I)
            if re.search(r'(?<![A-Za-z0-9])' + re.escape(symbol) + r'(?![A-Za-z0-9])', without_parenthesis, re.I):
                raise ValueError('A long form cannot repeat the unexplained abbreviation. Retrieve the actual full term; '
                                 'a modifier such as treated or soluble plus the same symbol is not an expansion.')
            if (not isinstance(symbol, str) or not isinstance(meaning, str) or len(meaning.strip()) < 3
                    or normalized(meaning).casefold() == symbol.casefold()
                    or symbol not in note.get('text', '') or meaning not in note.get('text', '')
                    or not any(normalized(meaning) in quote for quote in quotes)):
                raise ValueError('An abbreviation expansion must appear in its reading definition and exact source quote; '
                                 'copy the actual long form, never infer a meaning or supply an unrelated definition.')
            expanded.add(symbol)
    if expanded & unresolved:
        raise ValueError('The same abbreviation cannot be both expanded and unresolved')
    missing_expansions = sorted(abbreviations - expanded - unresolved)
    if missing_expansions:
        raise ValueError('Missing explicit sourced abbreviation expansions: ' + ', '.join(missing_expansions) +
                         '. Add expansions={symbol: original long form} to the matching reading_definition, '
                         'include both in its text and bind the long form to actual source evidence. '
                         'If its long form is genuinely unverified, keep the original label and declare it in '
                         'unresolved_expansions on a visible reading definition; do not guess, rename the label, '
                         'or ask the user for permission merely to preserve it. This literal check is not semantic proof.')
    group_notes = [note for note in notes
                   if note.get('purpose') == 'reading_definition' and note.get('topic') == 'group']
    if plan.get('figure_sources'):
        declared = set()
        displayed = [str(block.get('group', '')) for block in plan.get('blocks', [])]
        mismatched_ids = {item['input_identifier'] for item in plan.get('_source_label_differences', [])}
        for note in group_notes:
            meanings = note.get('group_definitions', {})
            if not isinstance(meanings, dict):
                raise ValueError('group_definitions must map each source group name to its actual biological definition quote')
            explanations = note.get('group_explanations', {})
            if not isinstance(explanations, dict) or set(explanations) != set(meanings):
                raise ValueError('Every group_definitions source phrase requires a matching group_explanations entry '
                                 'with the concise biological meaning in the report language. These explanations are printed '
                                 'in the actual caption; do not leave biological identity only in hidden plan fields.')
            for name, explanation in explanations.items():
                if (not isinstance(explanation, str) or not explanation.strip()
                        or len(explanation.strip()) > 140
                        or normalized(explanation).casefold() == str(name).casefold()):
                    raise ValueError('group_explanations requires a nonempty description beyond the group label, at most 140 characters')
            quotes = [normalized(str(item.get('quote', ''))) for item in note.get('evidence', [])]
            for name, meaning in meanings.items():
                if (not isinstance(name, str) or not name or not isinstance(meaning, str) or not meaning.strip()
                        or normalized(meaning).casefold() == name.casefold()
                        or not any(normalized(meaning) in quote for quote in quotes)):
                    raise ValueError('group_definitions require an actual source phrase explaining the biological or experimental '
                                     'group identity, not only an alias or a repeated label. Read the matching model/method definition.')
                if scientific_identifiers(name) & mismatched_ids:
                    raise ValueError('A source-table name absent from the target panel cannot acquire another model definition')
                for group in displayed:
                    if re.match(re.escape(name) + r'(?![A-Za-z0-9])', group):
                        declared.add(group)
        missing = [group for group in displayed
                   if group not in declared and not (scientific_identifiers(group) & mismatched_ids)]
        if missing:
            raise ValueError('Missing biological group definitions for: ' + ', '.join(dict.fromkeys(missing)) +
                             '. Add group_definitions={actual group name or leading group token: exact source definition phrase} '
                             'on the matching group note, cite the actual phrase, and explain its meaning concisely in the '
                             'report language. An alias alone does not explain a control, intervention or genetic model.')
    group_definitions = ' '.join(str(note.get('text', '')) for note in group_notes)
    labels = set()
    for block in plan.get('blocks', []):
        labels.update(scientific_identifiers(block.get('group', '')))
    mismatches = {item['input_identifier'] for item in plan.get('_source_label_differences', [])}
    imported = scientific_identifiers(group_definitions) & mismatches
    if imported:
        raise ValueError('Input group identifiers absent from the actual target-panel caption cannot be assigned '
                         'biological identities using another panel or a general model paragraph: ' + ', '.join(sorted(imported)) +
                         '. Remove that identity assignment from group definitions. The report will visibly preserve '
                         'the source-table label and record its difference from the actual target-panel identifiers. '
                         'Describe only this panel study groups with their own source; do not infer a typo or equivalence.')
    missing_labels = sorted(labels - scientific_identifiers(group_definitions) - mismatches)
    if missing_labels:
        raise ValueError('Scientific group identifiers absent from group definitions: ' + ', '.join(missing_labels) +
                         '. Preserve each actual source label. When the article uses a different identifier, '
                         'state both names with their separate evidence; do not silently merge them or infer a typo.')
    for note in group_notes:
        evidence = ' '.join(str(item.get('quote', '')) for item in note.get('evidence', []))
        unsupported = sorted(scientific_identifiers(note.get('text', '')) - scientific_identifiers(evidence))
        if unsupported:
            raise ValueError('Scientific group identifiers absent from this definition\'s source quotations: ' +
                             ', '.join(unsupported) + '. Read the actual matching panel or methods and cite its exact '
                             'definition. A name from a different panel must not be imported without support.')
