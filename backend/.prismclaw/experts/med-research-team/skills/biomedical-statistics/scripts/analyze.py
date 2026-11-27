import argparse
import hashlib
import csv
from collections import Counter
import io
import itertools
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from _artifact_store import digest, publish, package_snapshot, render_variant
from _anova import one_way_anova
from _delivery import render_delivery
from _source_notes import verify_notes, verify_figure_definitions

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100000
VERSION = '1.6.0'
# Set in the packaged replay that the self-check starts, so the replay does not check itself again.
REPLAY_ENVIRONMENT = 'BIOMEDICAL_STATISTICS_REPLAY'
PREVIEW_WIDTH = 1600


def number(value, label):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a numeric observation: ' + label)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Nonfinite value: ' + label)
    return result


def read_rows(raw, plan, suffix):
    """Return explicit observations and rejected blank locations without imputing."""
    if len(raw) > MAX_BYTES:
        raise ValueError('Input exceeds size limit')
    rows = []
    if suffix == '.csv':
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not {'group', 'value'}.issubset(fields):
            raise ValueError('Unique CSV headers group,value required')
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError('Invalid CSV shape at line ' + str(line))
            rows.append({**row, 'source_cell': 'CSV:' + str(line)})
            if len(rows) > MAX_ROWS:
                raise ValueError('Row limit exceeded')
    elif suffix == '.xlsx':
        from openpyxl import load_workbook
        from openpyxl.utils.cell import range_boundaries, get_column_letter
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 200 * 1024 * 1024:
                raise ValueError('Workbook expanded size limit exceeded')
        blocks = plan.get('blocks')
        if not isinstance(blocks, list) or not blocks or len(blocks) > 100:
            raise ValueError('Explicit workbook blocks required (maximum 100)')
        book = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        try:
            for block in blocks:
                sheet = block['sheet']
                if sheet not in book.sheetnames:
                    raise ValueError('Sheet not found: ' + sheet)
                left, top, right, bottom = range_boundaries(block['range'])
                if any(v is None or v < 1 for v in (left, top, right, bottom)):
                    raise ValueError('Bounded cell range required')
                if right < left or bottom < top or len(rows) + (right-left+1)*(bottom-top+1) > MAX_ROWS:
                    raise ValueError('Cell range or total row limit invalid')
                times = block.get('time_values')
                x_cell = block.get('x_cell')
                x_value = ''
                if x_cell is not None:
                    if times is not None:
                        raise ValueError('Use either x_cell or time_values per block')
                    x_left, x_top, x_right, x_bottom = range_boundaries(x_cell)
                    if x_left != x_right or x_top != x_bottom or None in (x_left, x_top):
                        raise ValueError('x_cell requires one bounded source cell')
                    x_value = number(book[sheet].cell(x_top, x_left).value, sheet + '!' + x_cell)
                if times is not None and (len(times) != bottom-top+1 or block.get('id_axis') != 'column'):
                    raise ValueError('Time rows require aligned time_values and id_axis=column')
                for ri, values in enumerate(book[sheet].iter_rows(min_row=top, max_row=bottom,
                                                               min_col=left, max_col=right, values_only=True)):
                    for ci, value in enumerate(values):
                        cell = get_column_letter(left+ci) + str(top+ri)
                        ident = get_column_letter(left+ci) if times is not None else str(top+ri) + ':' + get_column_letter(left+ci)
                        ids = block.get('ids')
                        if ids is not None:
                            if right != left or len(ids) != bottom-top+1:
                                raise ValueError('Explicit IDs require a single column and one ID per row')
                            ident = str(ids[ri])
                        rows.append({'panel': block.get('panel', 'analysis'), 'group': block['group'],
                                     'time': times[ri] if times is not None else x_value, 'id': ident,
                                     'value': value, 'source_cell': sheet + '!' + cell})
        finally:
            book.close()
    else:
        raise ValueError('Only CSV or XLSX input supported')
    return rows


def validate_measurement_label(raw, plan, suffix):
    """Require a source-workbook anchor for asserted measurement-scale qualifiers."""
    label = str((plan.get('plot_labels') or {}).get('y', '')).lower()
    patterns = [pattern for pattern in (r'相对|relative', r'归一化|normali[sz]ed', r'无量纲|无单位|unitless|dimensionless')
                if re.search(pattern, label)]
    if not patterns:
        return None
    anchor = (plan.get('plot_label_sources') or {}).get('y')
    if suffix != '.xlsx' or not isinstance(anchor, dict) or set(anchor) != {'sheet', 'cell'}:
        raise ValueError('Axis relative/normalized/unitless scale requires plot_label_sources.y with an actual source XLSX sheet/cell; otherwise use neutral measured-value wording, not invented units')
    if anchor['sheet'] not in {block['sheet'] for block in plan.get('blocks', [])}:
        raise ValueError('Axis scale anchor must be in an explicitly selected source sheet')
    if not isinstance(anchor['cell'], str) or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,5}', anchor['cell']):
        raise ValueError('Axis scale anchor requires one explicit source cell')
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    try:
        value = book[anchor['sheet']][anchor['cell']].value
    finally:
        book.close()
    if not isinstance(value, str) or not all(re.search(pattern, value.lower()) for pattern in patterns):
        raise ValueError('Axis scale qualifier is absent from its claimed source cell; retain neutral measurement wording')
    return {**anchor, 'text': value, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'scope': 'literal scale qualifier only; not normalization-method or scientific-design verification'}


def prepare(rows, plan):
    if plan.get('missing') not in ('stop', 'omit'):
        raise ValueError('Explicit missing=stop or omit required')
    if not isinstance(plan.get('unit'), str) or not plan['unit'].strip():
        raise ValueError('Explicit unit/unknown-unit label required')
    groups = {}
    seen = set()
    omitted = []
    tidy = []
    for index, row in enumerate(rows):
        panel = str(row.get('panel') or 'analysis').strip()
        group = str(row.get('group') or '').strip()
        if not panel or not group or max(len(panel), len(group)) > 100:
            raise ValueError('Nonempty panel/group labels up to 100 characters required')
        time = row.get('time', '')
        time = None if time in ('', None) else number(time, 'time')
        ident = str(row.get('id') or '').strip()
        if plan.get('design') in ('paired', 'longitudinal') and not ident:
            raise ValueError('Subject/observation IDs required for paired/longitudinal input')
        ident = ident or 'row-' + str(index+1)
        key = (panel, group, time, ident)
        if key in seen:
            raise ValueError('Duplicate panel/group/time/id: ' + repr(key))
        seen.add(key)
        value = row.get('value')
        if value is None or isinstance(value, str) and not value.strip():
            if plan['missing'] == 'stop':
                raise ValueError('Blank value: ' + str(row.get('source_cell', index)))
            omitted.append({'panel': panel, 'group': group, 'time': time, 'id': ident,
                            'source_cell': row.get('source_cell'), 'reason': 'blank'})
            continue
        value = number(value, str(row.get('source_cell', index)))
        item = {'panel': panel, 'group': group, 'time': time, 'id': ident, 'value': value,
                'source_cell': row.get('source_cell', '')}
        groups.setdefault((panel, group, time), []).append(item)
        tidy.append(item)
    if not groups or len(groups) > 200:
        raise ValueError('Between 1 and 200 group/time cells required')
    summary = []
    for (panel, group, time), observations in groups.items():
        values = [o['value'] for o in observations]
        if len(values) < 2:
            raise ValueError('At least two observations per group/time cell required')
        mean = statistics.mean(values)
        sd = statistics.stdev(values)
        if not all(math.isfinite(x) for x in (mean, sd, mean-sd, mean+sd)):
            raise ValueError('Nonfinite summary')
        summary.append({'panel': panel, 'group': group, 'time': time, 'n': len(values),
                        'mean': mean, 'sample_sd': sd, 'min': min(values), 'max': max(values)})
    return groups, summary, tidy, omitted


def holm(values):
    """Holm step-down adjusted P values, returned in original comparison order."""
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    previous = 0.0
    for rank, index in enumerate(ordered):
        previous = max(previous, min(1.0, (len(values)-rank)*values[index]))
        result[index] = previous
    return result


def temporal_quantities(text):
    """Literal time quantities only; no age/elapsed-time inference or unit conversion."""
    units = {'w': 'week', 'week': 'week', 'weeks': 'week', '周': 'week',
             'd': 'day', 'day': 'day', 'days': 'day', '天': 'day', '日': 'day',
             'h': 'hour', 'hour': 'hour', 'hours': 'hour', '小时': 'hour',
             'month': 'month', 'months': 'month', '月': 'month',
             'year': 'year', 'years': 'year', '年': 'year'}
    pattern = (r'(?<![\d.])((?:\d+(?:\.\d+)?)(?:\s*(?:,|and|&|、|和|及)\s*'
               r'\d+(?:\.\d+)?)*)\s*(weeks?|months?|years?|days?|hours?|[wdh]|小时|周|天|日|月|年)'
               r'(?![A-Za-z])')
    found = set()
    for match in re.finditer(pattern, str(text), flags=re.I):
        for value in re.findall(r'\d+(?:\.\d+)?', match.group(1)):
            found.add((float(value), units[match.group(2).lower()]))
    return found


def comparison_label_mapping(label, a, b, mapping=None):
    """Reject an explicit source pair whose scientific labels were silently remapped."""
    pair = re.split(r'\s+(?:vs\.?|versus)\s+', label.strip(), flags=re.IGNORECASE)
    if len(pair) != 2:
        return 'label is not an explicit two-group pair; source scope still requires review'
    def canonical(text):
        return re.sub(r'[^\w]+', '', text.casefold())
    targets = {canonical(a), canonical(b)}
    if {canonical(value) for value in pair} == targets:
        return 'source pair matches selected group labels'
    if not isinstance(mapping, dict) or mapping.get('kind') not in ('user_confirmation', 'original_source'):
        raise ValueError('Source comparison labels differ from selected groups: confirm the scientific '
                         'mapping with the user or original source before transcribing P; '
                         'do not append an unconfirmed mapping after plotting')
    if not all(isinstance(mapping.get(key), str) and mapping[key].strip()
               for key in ('quote', 'locator')):
        raise ValueError('Comparison mapping requires the actual confirmation/source quote and locator')
    if mapping['quote'].strip().casefold() == label.strip().casefold():
        raise ValueError('The original comparison label alone does not establish a mapping to different '
                         'target groups. Obtain the actual definition or user clarification; do not infer it from order.')
    aliases = mapping.get('aliases')
    if not isinstance(aliases, dict) or len(aliases) != 2:
        raise ValueError('Comparison mapping requires both source labels and their target groups')
    normalized = {canonical(key): canonical(value) for key, value in aliases.items()
                  if isinstance(key, str) and isinstance(value, str)}
    if set(normalized) != {canonical(value) for value in pair} or set(normalized.values()) != targets:
        raise ValueError('Comparison mapping must cover the exact source pair and selected groups')
    quantities = set()
    for original, selected in aliases.items():
        quantities.update(temporal_quantities(original))
        quantities.update(temporal_quantities(selected))
    if quantities - temporal_quantities(mapping['quote']):
        raise ValueError('Temporal mapping quote omits time quantities from the source or selected labels. '
                         'Read the actual direct definition or obtain the necessary user mapping; '
                         'do not infer age from treatment duration or subtract timepoints. '
                         'Matching quantities alone still do not prove the scientific relationship.')
    return 'explicit mapping declared; program does not verify the truth of its quote'


def comparison_label(record):
    endpoints = []
    for key in ('a', 'b'):
        text = record[key]
        coordinate = record.get('time_' + key)
        if coordinate is not None:
            text += ' @ ' + format(coordinate, '.6g')
        endpoints.append(text)
    return ' vs '.join(endpoints)


def reported_comparisons(raw, plan, suffix, summary):
    """Transcribe selected original adjusted P cells; never compute an exact P from a bound."""
    specs = plan.get('reported_comparisons', [])
    if not specs:
        return []
    if suffix != '.xlsx' or plan['method'] not in ('descriptive', 'row_z_descriptive'):
        raise ValueError('Reported P cells require descriptive XLSX input; no recomputation')
    if not isinstance(specs, list) or len(specs) > 50:
        raise ValueError('At most 50 reported comparisons supported')
    from openpyxl import load_workbook
    groups = {(g['panel'], g['group'], g['time']) for g in summary}
    timed_panels = {g['panel'] for g in summary if g['time'] is not None}
    book = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    records = []
    seen = set()
    try:
        for spec in specs:
            panel, a, b = spec['panel'], spec['a'], spec['b']
            time_a, time_b = spec.get('time_a'), spec.get('time_b')
            if panel in timed_panels:
                if time_a is None or time_b is None:
                    raise ValueError('Timed reported comparisons require time_a and time_b')
                time_a = number(time_a, 'time_a')
                time_b = number(time_b, 'time_b')
            elif time_a is not None or time_b is not None:
                raise ValueError('Untimed groups cannot have reported comparison times')
            first, second = (panel, a, time_a), (panel, b, time_b)
            key = frozenset((first, second))
            if first == second or first not in groups or second not in groups or key in seen:
                raise ValueError('Invalid or duplicate reported comparison endpoints')
            seen.add(key)
            adjustment = spec.get('adjustment', 'adjusted' if spec.get('adjusted') is True else None)
            if adjustment not in ('adjusted', 'unadjusted', 'unknown'):
                raise ValueError('Explicit original P adjustment status required')
            sheet = spec['sheet']
            for field in ('label_cell', 'p_cell'):
                if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', spec[field]):
                    raise ValueError('Explicit single cell required for reported P')
            label = book[sheet][spec['label_cell']].value
            original = book[sheet][spec['p_cell']].value
            if not isinstance(label, str) or not label.strip() or len(label) > 300:
                raise ValueError('Original comparison label required')
            mapping_a = a if time_a is None else a + ' @ ' + format(time_a, '.6g')
            mapping_b = b if time_b is None else b + ' @ ' + format(time_b, '.6g')
            mapping_status = comparison_label_mapping(label, mapping_a, mapping_b, spec.get('source_group_mapping'))
            if isinstance(original, bool) or original is None:
                raise ValueError('Invalid reported P cell')
            normalized = str(original).strip().replace(',', '.')
            match = re.fullmatch(r'(<=|>=|<|>|=)?\s*(\d*\.?\d+(?:[eE][-+]?\d+)?)', normalized)
            if not match:
                raise ValueError('Invalid reported P syntax')
            operator = match[1] or '='
            bound = number(match[2], sheet + '!' + spec['p_cell'])
            if not 0 <= bound <= 1 or operator == '<' and bound == 0 or operator == '>' and bound == 1:
                raise ValueError('Reported P outside valid probability bounds')
            records.append({'panel': panel, 'a': a, 'b': b, 'time_a': time_a, 'time_b': time_b,
                            'reported_p': original,
                            'operator': operator, 'value_or_bound': bound,
                            'display': ('Adjusted P' if adjustment == 'adjusted' else 'P') + operator + format(bound, '.6g'),
                            'adjustment': adjustment,
                            'source_comparison_label': label,
                            'source_label_cell': sheet + '!' + spec['label_cell'],
                            'source_p_cell': sheet + '!' + spec['p_cell'],
                            'source_group_mapping': spec.get('source_group_mapping'),
                            'source_group_mapping_status': mapping_status,
                            'origin': 'original P cell; transcribed, not recomputed or adjusted'})
        validate_reported_comparison_coverage(book, specs, plan.get('reported_comparison_subset'))
    finally:
        book.close()
    return records


def validate_reported_comparison_coverage(book, specs, subset=None):
    """Do not silently drop adjacent explicit source comparisons to avoid a mapping conflict."""
    from openpyxl.utils.cell import coordinate_from_string
    selected = {(spec['sheet'], spec['label_cell'], spec['p_cell']) for spec in specs}
    missing = set()
    for spec in specs:
        label_col, label_row = coordinate_from_string(spec['label_cell'])
        p_col, p_row = coordinate_from_string(spec['p_cell'])
        if label_row != p_row:
            continue
        sheet = book[spec['sheet']]
        for direction in (-1, 1):
            row = label_row + direction
            for _ in range(50):
                if row < 1 or row > sheet.max_row:
                    break
                label_cell = f'{label_col}{row}'
                p_cell = f'{p_col}{row}'
                label = sheet[label_cell].value
                value = sheet[p_cell].value
                if not isinstance(label, str) or not re.search(r'\s+(?:vs\.?|versus)\s+', label, re.I):
                    break
                if value is None:
                    break
                key = (spec['sheet'], label_cell, p_cell)
                if key not in selected:
                    missing.add('!'.join(key))
                row += direction
    if not missing:
        return
    if isinstance(subset, dict) and all(isinstance(subset.get(key), str) and subset[key].strip()
                                        for key in ('user_quote', 'locator')):
        restriction = r'(?i)\b(?:only|exclusively|solely)\b|仅(?:限|绘|画|显示|转录|比较|保留|标)|只(?:绘|画|显示|转录|比较|保留|标)|不要(?:显示|绘|画|转录|比较)'
        if re.search(restriction, subset['user_quote']):
            return
    raise ValueError('Adjacent original comparisons were omitted: ' + ', '.join(sorted(missing))
                     + '. Transcribe the complete requested source block after confirming scientific mappings; '
                     + 'do not remove a comparison to bypass a mapping conflict. An explicitly requested '
                     + 'subset requires reported_comparison_subset with the actual user_quote and locator; '
                     + 'the program cannot verify that quote.')


def row_z_description(rows, tidy, plan, suffix):
    """One physical worksheet row per panel; columns retain original sample positions."""
    if suffix != '.xlsx' or type(plan.get('z_ddof')) is not int or plan['z_ddof'] not in (0, 1):
        raise ValueError('Row Z requires XLSX and explicit z_ddof=0 or 1')
    panels = []
    group_order = []
    source_rows = {}
    owners = {}
    locations = set()
    sheets = set()
    for row in rows:
        panel = str(row.get('panel') or 'analysis').strip()
        group = str(row['group']).strip()
        sheet, cell = row['source_cell'].rsplit('!', 1)
        match = re.fullmatch(r'([A-Z]+)([0-9]+)', cell)
        column, number_ = match[1], int(match[2])
        if row['source_cell'] in locations:
            raise ValueError('Duplicate physical source cell in row-Z matrix')
        locations.add(row['source_cell'])
        sheets.add(sheet)
        if panel not in panels:
            panels.append(panel)
        if group not in group_order:
            group_order.append(group)
        if panel in source_rows and source_rows[panel] != number_:
            raise ValueError('Each row-Z panel must map to one physical worksheet row')
        source_rows[panel] = number_
        if column in owners and owners[column] != group:
            raise ValueError('A source column cannot change groups between row-Z panels')
        owners[column] = group
    if len(sheets) != 1 or len(panels) > 40 or len(owners) > 100:
        raise ValueError('Row Z requires one worksheet, at most 40 panels and 100 columns')

    def column_number(column):
        value = 0
        for char in column:
            value = value * 26 + ord(char) - 64
        return value

    columns = [{'group': group, 'source_column': column}
               for group in group_order for column in sorted(owners, key=column_number) if owners[column] == group]
    # A cell not selected is not evidence of an original blank. Require the
    # matrix footprint explicitly before constructing any missing-value tiles.
    sheet = next(iter(sheets))
    expected = {sheet + '!' + column['source_column'] + str(source_rows[panel])
                for panel in panels for column in columns}
    unselected = sorted(expected - locations)
    if unselected:
        raise ValueError('Row-Z matrix has unselected source cells: ' + ', '.join(unselected[:12])
                         + '. Keep the same original sample columns for every row, including blank cells; '
                         + 'use missing=omit for true blanks. Do not shorten ranges to bypass missing=stop.')
    observed = {(item['panel'], item['source_cell'].rsplit('!', 1)[1]): item['value'] for item in tidy}
    matrix = []
    centers = []
    for panel in panels:
        values = [item['value'] for item in tidy if item['panel'] == panel]
        if len(values) < 2:
            raise ValueError('Insufficient nonblank observations for row Z')
        center = statistics.mean(values)
        sd = statistics.pstdev(values) if plan['z_ddof'] == 0 else statistics.stdev(values)
        if not math.isfinite(sd) or sd <= 0:
            raise ValueError('Row Z undefined for zero/nonfinite variance')
        standardized = []
        for column in columns:
            value = observed.get((panel, column['source_column'] + str(source_rows[panel])))
            z = None if value is None else (value - center) / sd
            if z is not None and not math.isfinite(z):
                raise ValueError('Nonfinite row Z')
            standardized.append(z)
        matrix.append(standardized)
        centers.append({'panel': panel, 'n': len(values), 'center': center, 'scale': sd})
    return {'panels': panels, 'columns': columns, 'values': matrix, 'centers': centers,
            'source_sheet': next(iter(sheets)), 'source_rows': source_rows, 'ddof': plan['z_ddof'],
            'selected_source_cell_count': len(locations),
            'scope': 'Within-row descriptive standardization over all nonblank groups; zeros retained; no inference.'}


def row_z_records(result, tidy):
    data = result.get('row_z')
    if not data:
        return []
    observed = {(x['panel'], x['source_cell']): x['value'] for x in tidy}
    records = []
    for i, panel in enumerate(data['panels']):
        for j, column in enumerate(data['columns']):
            cell = data['source_sheet'] + '!' + column['source_column'] + str(data['source_rows'][panel])
            records.append({'panel': panel, 'group': column['group'], 'source_cell': cell,
                            'value': observed.get((panel, cell)), 'z': data['values'][i][j]})
    return records


def sample_size(plan):
    p = number(plan['proportion'], 'proportion')
    margin = number(plan['absolute_margin'], 'absolute_margin')
    alpha = number(plan.get('alpha', .05), 'alpha')
    if not 0 < p < 1 or not 0 < margin < 1 or not 0 < alpha < 1:
        raise ValueError('Require 0<p, absolute_margin, alpha<1')
    z = statistics.NormalDist().inv_cdf(1-alpha/2)
    digits = plan.get('z_round_digits')
    if digits is not None:
        if type(digits) is not int or not 2 <= digits <= 10:
            raise ValueError('z_round_digits must be null or an integer from 2 through 10')
        z = round(z, digits)
    raw_n = z*z*p*(1-p)/(margin*margin)
    if not math.isfinite(raw_n) or raw_n > 1e12:
        raise ValueError('Sample size outside supported range')
    loss = number(plan.get('attrition', 0), 'attrition')
    if not 0 <= loss < 1:
        raise ValueError('Require 0<=attrition<1')
    n = math.ceil(raw_n)
    return {'formula': 'z_(1-alpha/2)^2 * p * (1-p) / absolute_margin^2',
            'proportion': p, 'absolute_margin': margin, 'alpha': alpha, 'z': z,
            'z_round_digits': digits, 'unrounded_n': raw_n, 'effective_n': n,
            'attrition': loss, 'recruitment_n': math.ceil(n/(1-loss)),
            'scope': 'single-proportion precision, normal approximation; not regression/prediction-model sample size'}


def infer(groups, plan):
    from scipy import stats
    import scipy
    if scipy.__version__ != '1.17.1':
        raise ValueError('Validated inference requires scipy==1.17.1')
    method = plan['method']
    design = plan['design']
    if design not in ('independent', 'paired'):
        raise ValueError('Inference requires explicit independent or paired design')
    if any(key[2] is not None for key in groups):
        raise ValueError('Longitudinal inference is not supported; use descriptive summaries')
    alpha = number(plan.get('alpha', .05), 'alpha')
    if not 0 < alpha < 1:
        raise ValueError('Require 0<alpha<1')
    if method == 'tukey_hsd':
        if design != 'independent' or plan.get('normal_equal_variance_confirmed') is not True:
            raise ValueError('Tukey requires independent groups and confirmed normal/equal-variance assumptions')
    if method == 'paired_t' and design != 'paired':
        raise ValueError('paired_t requires paired design')
    if method in ('student_t', 'welch_t') and design != 'independent':
        raise ValueError('Independent t test cannot be applied to paired data')
    if method == 'student_t' and plan.get('equal_variance_confirmed') is not True:
        raise ValueError('Student t requires explicit equal_variance_confirmed=true')
    if method == 'student_t':
        basis = plan.get('equal_variance_basis')
        if not isinstance(basis, dict) or set(basis) != {'source', 'quote'}:
            raise ValueError('Student t requires equal_variance_basis source and exact quote; original use of Student alone does not confirm equal variance. Use Welch when this basis is absent')
        quote = basis.get('quote')
        if not isinstance(quote, str) or not isinstance(basis.get('source'), str) or not basis['source'].strip() or len(quote) > 500:
            raise ValueError('Equal-variance basis requires a bounded exact quote and source locator')
        if not re.search(r'\bequal\b.{0,30}\bvariances?\b|方差.{0,8}(?:齐|相等|一致)|同方差', quote, re.I):
            raise ValueError('Equal-variance quote must explicitly concern equal variance, not merely name Student t')
        if re.search(r'\b(?:not|unknown|unconfirmed)\b|未确认|未证实|不满足|不相等|不齐', quote, re.I):
            raise ValueError('Equal-variance quote contains an unresolved or negative statement; do not mark it confirmed')
    if method == 'pairwise_t_holm' and plan.get('family_scope') != 'panel':
        raise ValueError('Explicit family_scope=panel required; every within-panel pair is included')
    panels = list(dict.fromkeys(key[0] for key in groups))
    results = []
    for panel in panels:
        names = [key[1] for key in groups if key[0] == panel]
        if method in ('student_t', 'welch_t', 'paired_t') and len(names) != 2:
            raise ValueError('Two groups per panel required for a single t test')
        if len(names) < 2 or len(names) > 20:
            raise ValueError('Inference supports 2 through 20 groups per panel')
        values = [[o['value'] for o in groups[(panel, name, None)]] for name in names]
        tukey = stats.tukey_hsd(*values) if method == 'tukey_hsd' else None
        tukey_ci = tukey.confidence_interval(1-alpha) if tukey is not None else None
        family = []
        for i, j in itertools.combinations(range(len(names)), 2):
            a, b = values[i], values[j]
            if design == 'paired':
                a_map = {o['id']: o['value'] for o in groups[(panel, names[i], None)]}
                b_map = {o['id']: o['value'] for o in groups[(panel, names[j], None)]}
                if set(a_map) != set(b_map):
                    raise ValueError('Paired subject IDs differ; no silent pairwise deletion')
                ids = sorted(a_map)
                a = [a_map[x] for x in ids]
                b = [b_map[x] for x in ids]
            if tukey is not None:
                statistic = float(tukey.statistic[i, j])
                pvalue = float(tukey.pvalue[i, j])
                low = float(tukey_ci.low[i, j])
                high = float(tukey_ci.high[i, j])
                df = None
            else:
                if design == 'paired':
                    test = stats.ttest_rel(a, b, alternative='two-sided', nan_policy='raise')
                else:
                    test = stats.ttest_ind(a, b, equal_var=method == 'student_t',
                                           alternative='two-sided', nan_policy='raise')
                ci = test.confidence_interval(1-alpha)
                statistic = float(test.statistic)
                pvalue = float(test.pvalue)
                df = float(test.df)
                low = float(ci.low)
                high = float(ci.high)
            if not all(math.isfinite(x) for x in (statistic, pvalue, low, high)):
                raise ValueError('Degenerate inference; no finite statistic/P/CI')
            family.append({'panel': panel, 'a': names[i], 'b': names[j], 'n_a': len(a), 'n_b': len(b),
                           'mean_difference_a_minus_b': statistics.mean(a)-statistics.mean(b),
                           'statistic': statistic, 'df': df, 'p_value': pvalue,
                           'ci_low': low, 'ci_high': high,
                           'ci_scope': 'simultaneous Tukey' if tukey is not None else 'unadjusted mean-difference'})
        adjusted = holm([x['p_value'] for x in family]) if method == 'pairwise_t_holm' else [x['p_value'] for x in family]
        for item, p in zip(family, adjusted):
            item['p_adjusted'] = p
            item['correction'] = 'Holm within panel' if method == 'pairwise_t_holm' else 'Tukey within panel' if tukey is not None else 'none'
            results.append(item)
    return results


def calculate(raw, plan, suffix='.csv'):
    if not isinstance(plan, dict):
        raise ValueError('Plan JSON object required')
    requested_method = plan.get('method')
    if requested_method == 'two_sample_t':
        resolved = {'independent': 'welch_t', 'paired': 'paired_t'}.get(plan.get('design'))
        if resolved is None:
            raise ValueError('two_sample_t requires actual independent or paired design; unknown design cannot be guessed')
        plan = {**plan, 'method': resolved}
    method = plan.get('method')
    supported = ('descriptive', 'row_z_descriptive', 'proportion_precision', 'student_t', 'welch_t', 'paired_t', 'pairwise_t_holm', 'tukey_hsd', 'one_way_anova')
    if method not in supported:
        raise ValueError('Unsupported method: ' + str(method))
    if plan.get('design') not in ('independent', 'paired', 'longitudinal', 'unknown', 'single_proportion'):
        raise ValueError('Explicit supported design required')
    result = {'schema': 1, 'implementation_version': VERSION, 'status': 'executed',
              'method': method, 'requested_method': requested_method, 'design': plan['design'], 'unit': plan.get('unit'),
              'validation_scope': 'bounded-registered-methods; not clinical approval',
              'runtime': {'python': platform.python_version(), 'executable': sys.executable}}
    notes = plan.get('caption_notes', [])
    if not isinstance(notes, list) or len(notes) > 12:
        raise ValueError('caption_notes requires at most 12 sourced notes')
    for note in notes:
        if not isinstance(note, dict) or set(note) - {'text', 'source', 'purpose', 'evidence', 'topic', 'expansions', 'unresolved_expansions', 'group_definitions', 'group_explanations'} or not {'text', 'source'} <= set(note):
            raise ValueError('Each caption note requires text and source, and purpose')
        if any(not isinstance(note[key], str) or not note[key].strip() or len(note[key]) > limit
               for key, limit in (('text', 1500), ('source', 3000))):
            raise ValueError('Caption note text/source must be nonempty and bounded')
        if note.get('purpose') not in ('source_audit', 'reading_definition'):
            raise ValueError('Caption note purpose is required: reading_definition for concise sourced definitions that readers need in the report; source_audit for internal processing notes. Missing purpose must not silently discard definitions')
        if note.get('purpose') == 'reading_definition' and len(note['text']) > 160:
            raise ValueError('A reading definition must fit 160 characters')
        unresolved = note.get('unresolved_expansions', [])
        if (not isinstance(unresolved, list) or len(unresolved) > 12
                or any(not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 30
                       or any(character in symbol for character in '\n\r') for symbol in unresolved)):
            raise ValueError('unresolved_expansions requires short, single-line original labels')
    result['caption_notes'] = notes
    rationale = plan.get('plot_rationale', '')
    if not isinstance(rationale, str):
        raise ValueError('plot_rationale must be text describing the current plot choice')
    result['plot_rationale'] = rationale
    analysis_notes = plan.get('analysis_notes', [])
    if not isinstance(analysis_notes, list) or any(not isinstance(note, str) for note in analysis_notes):
        raise ValueError('analysis_notes must contain text describing the current input or analysis status')
    result['analysis_notes'] = analysis_notes
    result['source_label_differences'] = plan.get('_source_label_differences', [])
    result['significance_style'] = plan.get('significance_style', 'p')
    if result['significance_style'] not in ('p', 'p_and_stars'):
        raise ValueError('significance_style must be p or p_and_stars')
    result['plot_blocks'] = plan.get('plot_blocks', {})
    result['plot_columns'] = plan.get('plot_columns')
    result['plot_raw_points'] = plan.get('plot_raw_points', False)
    if type(result['plot_raw_points']) is not bool:
        raise ValueError('plot_raw_points requires a boolean')
    result['coordinate_kind'] = 'numeric' if any('x_cell' in block for block in plan.get('blocks', [])) else 'time'
    if not isinstance(result['plot_blocks'], dict):
        raise ValueError('Plot blocks require a panel mapping')
    result['runtime']['packages'] = {}
    for name in ('scipy', 'numpy', 'openpyxl', 'matplotlib', 'pillow'):
        try:
            result['runtime']['packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['runtime']['packages'][name] = 'not installed'
    if method == 'proportion_precision':
        if plan['design'] != 'single_proportion':
            raise ValueError('Sample-size method requires single_proportion design')
        result['sample_size'] = sample_size(plan)
        return result, []
    result['measurement_scale_source'] = validate_measurement_label(raw, plan, suffix)
    rows = read_rows(raw, plan, suffix)
    groups, summary, tidy, omitted = prepare(rows, plan)
    plot_labels = plan.get('plot_labels') or {}
    if 'y_by_panel' in plot_labels:
        y_by_panel = plot_labels['y_by_panel']
        if not isinstance(y_by_panel, dict) or set(y_by_panel) != {key[0] for key in groups}:
            raise ValueError('plot_labels.y_by_panel must cover every data panel')
        if any(not isinstance(value, str) or not value.strip() or len(value) > 160
               for value in y_by_panel.values()):
            raise ValueError('Panel axis labels must be nonempty text up to 160 characters')
        sources = (plan.get('plot_label_sources') or {}).get('y_by_panel', {})
        if not isinstance(sources, dict) or set(sources) - set(y_by_panel):
            raise ValueError('plot_label_sources.y_by_panel must reference existing panels')
        result['panel_measurement_scale_sources'] = {
            panel: validate_measurement_label(raw, {**plan, 'plot_labels': {'y': value},
                'plot_label_sources': {'y': sources.get(panel)}}, suffix)
            for panel, value in y_by_panel.items()
        }
    result.update(groups=summary, omitted_blanks=omitted, cleaning='none; explicit blank omissions only',
                  missing_policy=plan['missing'], error_bar='sample SD (n-1)', comparisons=[])
    if set(result['plot_blocks']) - {g['panel'] for g in summary}:
        raise ValueError('Plot blocks reference an unknown panel')
    if method not in ('descriptive', 'row_z_descriptive'):
        if method == 'one_way_anova':
            result.update(one_way_anova(groups, plan))
        else:
            result['comparisons'] = infer(groups, plan)
        result['runtime']['scipy'] = '1.17.1'
    result['inference'] = 'not performed' if method in ('descriptive', 'row_z_descriptive') else 'executed'
    if method == 'row_z_descriptive':
        result['row_z'] = row_z_description(rows, tidy, plan, suffix)
    result['reported_comparisons'] = reported_comparisons(raw, plan, suffix, summary)
    result['reporting_alpha'] = number(plan.get('alpha', .05), 'alpha')
    if not 0 < result['reporting_alpha'] < 1:
        raise ValueError('alpha must be between zero and one')
    if result.get('row_z'):
        for comparison in result['reported_comparisons']:
            expected_row = result['row_z']['source_rows'][comparison['panel']]
            for key in ('source_label_cell', 'source_p_cell'):
                sheet, cell = comparison[key].rsplit('!', 1)
                if sheet != result['row_z']['source_sheet'] or int(re.search(r'[0-9]+$', cell)[0]) != expected_row:
                    raise ValueError('Row-Z reported P and label must come from the same source row')
    result['plot_labels'] = plan.get('plot_labels', {})
    return result, tidy


def bind_source_axis_labels(raw, plan, suffix, result):
    if suffix != '.xlsx' or not any(g['time'] is not None for g in result.get('groups', [])):
        return
    sources = plan.get('plot_label_sources') or {}
    if not isinstance(sources, dict):
        raise ValueError('plot_label_sources must be an object')
    anchor = sources.get('x')
    if not isinstance(anchor, dict) or set(anchor) not in ({'sheet', 'cell'}, {'sheet', 'cells', 'text'}):
        raise ValueError('plot_label_sources.x requires sheet/cell or sheet/cells/text')
    if anchor['sheet'] not in {block['sheet'] for block in plan.get('blocks', [])}:
        raise ValueError('Axis label source must be in an explicitly selected sheet')
    cells = [anchor['cell']] if 'cell' in anchor else anchor['cells']
    if not isinstance(cells, list) or not 1 <= len(cells) <= 50:
        raise ValueError('Axis label source requires 1..50 cells')
    if any(not isinstance(cell, str) or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,5}', cell) for cell in cells):
        raise ValueError('Axis label source requires explicit cell addresses')
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    try:
        values = [book[anchor['sheet']][cell].value for cell in cells]
    finally:
        book.close()
    if any(value is None or isinstance(value, bool) for value in values):
        raise ValueError('Axis label source cells must contain explicit values')
    value = values[0] if 'cell' in anchor else anchor['text']
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
        raise ValueError('Axis label source must contain nonempty text up to 160 characters')
    result['plot_labels'] = {**result.get('plot_labels', {}), 'x': value.strip()}
    result['plot_label_sources'] = {'x': {**anchor, 'text': value.strip(), 'source_values': values}}


def render_plot_report(result, tidy):
    def fmt(value):
        return format(value, '.6g')
    def label(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    def stat(value):
        return format(value, '.3e' if 0 < abs(value) < .001 else '.3f')
    groups = result['groups']
    reported = result.get('reported_comparisons', [])
    comparisons = result.get('comparisons', [])
    names = list(dict.fromkeys(g['group'] for g in groups))
    coordinate = '横坐标' if result.get('coordinate_kind') == 'numeric' else '时间'
    point_label = '横坐标' if coordinate == '横坐标' else '时点'
    sampling_label = '各组各' + point_label if any(g['time'] is not None for g in groups) else '各面板各组'
    caption = ['图示' + str(len(names)) + '组：' + '、'.join(label(name) for name in names) + '。',
               '数据汇总为均值±样本标准差（SD，分母n−1）。']
    if not any(note.get('purpose') == 'reading_definition' and note.get('topic') == 'quantity'
               for note in result.get('caption_notes', [])):
        labels = result['plot_labels']
        if labels.get('y_by_panel'):
            axes = '、'.join(label(panel) + '为' + label(value) for panel, value in labels['y_by_panel'].items())
            caption.append('各面板纵轴：' + axes + '。')
        else:
            caption.append('纵轴为' + label(labels['y']) + '。')
    note_text = ' '.join(note['text'] for note in result.get('caption_notes', [])
                         if note.get('purpose') == 'reading_definition')
    if re.search(r'(?<![A-Za-z])(?:SEM|s\.e\.m\.?)(?![A-Za-z])', note_text, flags=re.I):
        caption.append('SEM（亦写作s.e.m.）指均值标准误，与样本标准差SD不同。')
    if re.search(r'(?<![A-Za-z])ANOVA(?![A-Za-z])', note_text, flags=re.I):
        caption.append('ANOVA指方差分析；具体方法与多重比较处理以所引来源说明为准。')
    for difference in result.get('source_label_differences', []):
        names = '、'.join(difference['panel_identifiers']) or '未提取到对应科学标识'
        caption.append('源表保留标识' + difference['input_identifier'] + '；所核对的原文' + difference['locator'] +
                       '未出现该标识，本面板出现的科学标识为' + names +
                       '。名称差异尚未确认，不把同篇其他面板的模型定义移用于该列，也不擅自认定为笔误。')
    for note in result.get('caption_notes', []):
        if note.get('purpose') == 'reading_definition':
            if len(note['text']) > 160:
                raise ValueError('A caption reading definition must fit 160 characters; put source audit in its own section')
            caption.append(label(note['text']).rstrip('。.!！?？') + '。〔来源' + str(result['caption_notes'].index(note) + 1) + '〕')
            for name, explanation in note.get('group_explanations', {}).items():
                caption.append(label(name) + '：' + label(explanation).rstrip('。.!！?？') +
                               '。〔来源' + str(result['caption_notes'].index(note) + 1) + '〕')
            unresolved = note.get('unresolved_expansions', [])
            if unresolved:
                caption.append('保留原标签' + '、'.join(label(symbol) for symbol in unresolved)
                               + '；其全称尚未核实，不据相近缩写推定。')
    mappings = {}
    for item in reported:
        mapping = item.get('source_group_mapping')
        if isinstance(mapping, dict) and mapping.get('quote'):
            key = (mapping.get('kind'), mapping['quote'], mapping.get('locator', ''))
            mappings[key] = mapping
    for (kind, quote, locator), mapping in mappings.items():
        origin = '本轮用户确认' if kind == 'user_confirmation' else '原文直接依据'
        caption.append('原比较标签与本图组名的对应依据为' + origin + '：“' + label(quote) +
                       '”（' + label(locator) + '）。此对应不另行证明实验身份或年龄。')
    counts = Counter(g['n'] for g in groups)
    usual_n = counts.most_common(1)[0][0]
    exceptions = []
    for group in groups:
        if group['n'] == usual_n:
            continue
        location = label(group['panel']) + '的' + label(group['group'])
        if group['time'] is not None:
            location += '（' + coordinate + '值' + fmt(group['time']) + '）'
        exceptions.append(location + ' n=' + str(group['n']))
    panel_names = list(dict.fromkeys(group['panel'] for group in groups))
    if len(panel_names) == 1 and all(group['time'] is not None for group in groups) and len(counts) > 1:
        n_summary = []
        for name in dict.fromkeys(group['group'] for group in groups):
            selected = sorted((group for group in groups if group['group'] == name), key=lambda group: group['time'])
            n_summary.append(label(name) + '按' + point_label + '、'.join(fmt(group['time']) for group in selected) +
                             '的有效n依次为' + '、'.join(str(group['n']) for group in selected))
        caption.append('；'.join(n_summary) + '。')
    elif exceptions:
        caption.append('除' + '、'.join(exceptions) + '外，其余' + sampling_label + '有效n=' + str(usual_n) + '。')
    else:
        caption.append(sampling_label + '有效n=' + str(usual_n) + '。')
    if any(g['time'] is not None for g in groups):
        times = sorted({g['time'] for g in groups if g['time'] is not None})
        caption.append('横轴为' + label(result['plot_labels']['x']) + '，共' + str(len(times)) +
                       '个' + point_label + '：' + '、'.join(fmt(value) for value in times) + '；折线连接各横坐标处的均值，误差条为样本SD。')
        if result.get('plot_raw_points'):
            caption.append('散点为原始有效观测，保留原始纵坐标，未连接个体。')
    else:
        caption.append('散点为原始有效观测，黑色横杠为均值。')
    heatmap_caption = None
    if result.get('row_z'):
        ddof = result['row_z']['ddof']
        heatmap_caption = ('每行为一个指标，列沿用原始观测位置。行向Z标准化使用该行全部非空观测；'
                           '标准差自由度扣减数ddof=' + str(ddof) + '，分母为该行有效n减ddof。'
                           '原行列顺序保持，未聚类。较高Z值为暖色、较低为冷色；绿色底、黑色x表示空白，'
                           '不属于Z值色标。实测零值参与标准化。Z值只表示行内相对水平，不表示统计显著性或因果。')
    if comparisons:
        confidence = fmt(100 * (1 - result['reporting_alpha'])) + '%'
        methods = {'student_t': '双侧独立样本Student t检验', 'welch_t': '双侧独立样本Welch t检验',
                   'paired_t': '双侧配对t检验', 'pairwise_t_holm': '面板内两两t检验及Holm校正',
                   'tukey_hsd': '面板内Tukey HSD比较',
                   'one_way_anova': {'equal': '单因素ANOVA及Tukey多重比较',
                                     'unequal': 'Welch单因素ANOVA及Games–Howell多重比较',
                                     'mixed': '分面板单因素ANOVA及相应多重比较'}.get(result.get('variance_model'))}
        caption += ['本次按方案执行' + methods[result['method']] + '，计算结果及校正口径见下表。',
                    '置信区间（CI）水平为' + confidence + '（alpha=' + fmt(result['reporting_alpha']) +
                    '，实际运行参数见result.json）；实验设计和假设按plan.json记录，不由计算成功证明其成立，程序未验证其真实性。']
        if result.get('omnibus'):
            caption.append('每个指标的全部组间比较构成一个比较族，P值和区间在该族内调整；不同指标之间未作联合校正。'
                           '检验依赖独立观测和组内近似正态分布；小样本对偏态及异常值敏感，未将正态性检验不显著视为前提已证实。')
            if result['variance_model'] == 'equal':
                caption.append('本次普通ANOVA与Tukey比较采用等方差假设。')
            if result['variance_model'] == 'mixed':
                caption.append('采用普通ANOVA与Tukey的面板使用等方差假设，采用Welch ANOVA的面板使用Games–Howell比较。')
    elif reported:
        caption.append('标注P值为原表转录，未执行新的推断检验；当前有效观测数不是原P值检验所用n的证明。')
    else:
        caption.append('本图仅作描述性汇总，未执行推断检验。')
    if result.get('significance_style') == 'p_and_stars' and (reported or result.get('comparisons')):
        caption.append('星号仅为P值显示：* P<0.05，** P<0.01，*** P<0.001；保留原P值及其校正口径。')
    lines = ['# 图注与结果说明', '', '科研草稿，待发起医生确认。', '', '## 图注', '',
             *(['### 图1：分面散点图', ''] if heatmap_caption else []), '\n\n'.join(
                 ''.join(caption[index:index + 3]) for index in range(0, len(caption), 3))]
    if heatmap_caption:
        lines += ['', '### 图2：行向Z标准化热图', '', heatmap_caption]
    if result.get('analysis_notes'):
        lines += ['', '## 本次数据核对状态', '']
        lines += ['- ' + label(note).rstrip('。') + '。' for note in result['analysis_notes']]
    if result.get('plot_rationale'):
        lines += ['', '## 本次图形选择', '', label(result['plot_rationale'])]
    lines += ['', '## 结果说明', '']
    if any(g['time'] is not None for g in groups):
        panels = list(dict.fromkeys(g['panel'] for g in groups if g['time'] is not None))
        trajectories = []
        patterns = {}
        for panel in panels:
            selected_groups = [g for g in groups if g['panel'] == panel and g['time'] is not None]
            names = list(dict.fromkeys(g['group'] for g in selected_groups))
            for name in names:
                points = sorted((g for g in selected_groups if g['group'] == name), key=lambda g: g['time'])
                turning_points = [points[0]]
                previous_direction = 0
                for previous, current in zip(points, points[1:]):
                    difference = current['mean'] - previous['mean']
                    direction = 1 if difference > 0 else -1 if difference < 0 else 0
                    if direction and previous_direction and direction != previous_direction:
                        turning_points.append(previous)
                    if direction:
                        previous_direction = direction
                if turning_points[-1] is not points[-1]:
                    turning_points.append(points[-1])
                series_name = label(name) if len(panels) == 1 else label(panel)
                if len(panels) > 1 and len(names) > 1:
                    series_name += ' / ' + label(name)
                first = turning_points[0]
                description = (series_name + '：' + coordinate + fmt(first['time']) + '时为' +
                               stat(first['mean']) + '±' + stat(first['sample_sd']))
                for previous, current in zip(turning_points, turning_points[1:]):
                    change = '升至' if current['mean'] > previous['mean'] else '降至' if current['mean'] < previous['mean'] else '保持为'
                    description += ('，' + coordinate + fmt(current['time']) + '时' + change +
                                    stat(current['mean']) + '±' + stat(current['sample_sd']))
                trajectories.append(description)
                pattern = tuple(1 if b['mean'] > a['mean'] else -1
                                for a, b in zip(turning_points, turning_points[1:]) if b['mean'] != a['mean'])
                endpoint = series_name + '：' + stat(points[-1]['mean']) + '±' + stat(points[-1]['sample_sd'])
                patterns.setdefault(pattern, []).append((series_name, description, endpoint, points[-1]['time'], points[-1]['mean']))
        if len(trajectories) > 3:
            trajectories = []
            for pattern, members in patterns.items():
                direction = '→'.join('升高' if value > 0 else '降低' for value in pattern) if pattern else '不变'
                names = '、'.join(member[0] for member in members)
                example = '；例如' + members[0][1] if len(members) > 1 else '：' + members[0][1]
                if result.get('coordinate_kind') == 'numeric' and len(members) > 1:
                    example = ''
                endpoints = ''
                if len(members) > 1:
                    if len({member[3] for member in members}) == 1:
                        selected = members
                        summary = '均值±SD为'
                        if len(members) > 3:
                            lowest = min(members, key=lambda member: member[4])
                            highest = max(members, key=lambda member: member[4])
                            selected = [lowest] if lowest[4] == highest[4] else [lowest, highest]
                            summary = ('均值相同，示例均值±SD为' if len(selected) == 1
                                       else '最低与最高均值±SD分别为')
                        scope = '上述' + str(len(members)) + '条曲线的' if result.get('coordinate_kind') == 'numeric' else ''
                        endpoints = ('；' + scope + '终点（' + coordinate + fmt(members[0][3]) + '）' + summary +
                                     '、'.join(member[2] for member in selected))
                    else:
                        endpoints = '；终点分别为' + '、'.join(
                            member[2] + '（' + coordinate + fmt(member[3]) + '）' for member in members)
                trajectories.append(names + '的均值变化方向为' + direction + example + endpoints)
        unit_note = '（单位：' + label(result['unit']) + '）' if result['unit'] != 'unknown' else ''
        lines += ['各组均值±样本SD' + unit_note + '按图中横轴顺序变化：' + '；'.join(trajectories) +
                  '。完整观测位置、均值、SD及有效n见下表；不据描述性变化判断显著差异、机制或疗效。', '']
    else:
        overview = ('本次保留' + str(len(tidy)) + '个有效观测，其中实测0为' +
                  str(sum(row['value'] == 0 for row in tidy)) + '个；另有' +
                  str(len(result.get('omitted_blanks', []))) + '个空白未参与汇总。各组均值、SD和有效n如下。')
        if result.get('row_z'):
            overview += '热图色彩仅表示行内相对水平，不代表未检验的生物学通路排序。'
        lines += [overview, '']
        alpha = result.get('reporting_alpha', .05)
        selected = []
        for item in result.get('reported_comparisons', []):
            value = item['value_or_bound']
            operator = item['operator']
            below = ((operator == '=' and value < alpha) or
                     (operator == '<' and value <= alpha) or
                     (operator == '<=' and value < alpha))
            if below:
                pair = {g['group']: g for g in groups if g['panel'] == item['panel'] and g['time'] is None}
                if item['a'] in pair and item['b'] in pair:
                    a = pair[item['a']]
                    b = pair[item['b']]
                    direction = '高于' if b['mean'] > a['mean'] else '低于' if b['mean'] < a['mean'] else '等于'
                    selected.append(label(item['panel']) + '（' + label(item['b']) + '均值' + direction + label(item['a']) + '）')
        if result.get('reported_comparisons'):
            noun = '指标' if result.get('row_z') else '比较'
            lines[-2] += ('以' + fmt(alpha) + '作为原P值的展示阈值，可由给定数值或界值确定P小于该阈值的' + noun + '有' +
                          str(len(selected)) + '项：' + ('、'.join(selected) if selected else '无') +
                          '。所列方向仅为本次观测均值比较，不代表机制或因果；不新增校正，原P的校正状态见下表。')
    if comparisons:
        by_group = {(group['panel'], group['group']): group for group in groups}
        lines += ['在所登记的设计与假设成立的前提下：', '']
        for panel in dict.fromkeys(item['panel'] for item in comparisons):
            items = [item for item in comparisons if item['panel'] == panel]
            p_name = 'P' if items[0]['correction'] == 'none' else '校正后P'
            count = sum(value['p_adjusted'] < result['reporting_alpha'] for value in items)
            overview = (label(panel) + '共' + str(len(items)) + '项组间比较，其中' + str(count) + '项' +
                        p_name + '<' + fmt(result['reporting_alpha']) + '。')
            sentences = []
            for item in items:
                a = by_group[item['panel'], item['a']]
                b = by_group[item['panel'], item['b']]
                direction = '高于' if a['mean'] > b['mean'] else '低于' if a['mean'] < b['mean'] else '等于'
                if len(items) > 1:
                    sentences.append(label(panel) + '中，' + label(item['a']) + '的均值' +
                                     direction + label(item['b']) + '（均值差A−B=' +
                                     stat(item['mean_difference_a_minus_b']) + '，' + p_name + '=' +
                                     fmt(item['p_adjusted']) + '）。')
                    continue
                sentences.append(label(item['panel']) + '中，' + label(item['a']) + '的均值' +
                                 direction + label(item['b']) + '（均值±样本SD分别为' +
                                 stat(a['mean']) + '±' + stat(a['sample_sd']) + '、' +
                                 stat(b['mean']) + '±' + stat(b['sample_sd']) + '；有效n分别为' +
                                 str(a['n']) + '、' + str(b['n']) + '），均值差A−B=' +
                                 stat(item['mean_difference_a_minus_b']) + '，' + p_name + '=' +
                                 fmt(item['p_adjusted']) + '（校正：' + label(item['correction']) + '）。')
            lines += [overview + ''.join(sentences), '']
        lines += ['完整比较及区间见下表；未达到展示阈值不等于等效。'
                  '所列方向仅表示样本均值比较，不据此认定机制、因果或临床意义。', '']
    timed = any(group['time'] is not None for group in groups)
    headers = ['面板/指标', '组别'] + ([coordinate + '值'] if timed else []) + ['有效n', '均值', '样本SD']
    aligns = ['---', '---'] + (['---:'] if timed else []) + ['---:', '---:', '---:']
    lines += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(aligns) + ' |']
    for index, group in enumerate(groups):
        panel_label = '' if timed and len(panel_names) == 1 and index else label(group['panel'])
        values = [panel_label, label(group['group'])]
        if timed:
            values.append('' if group['time'] is None else fmt(group['time']))
        values += [str(group['n']), stat(group['mean']), stat(group['sample_sd'])]
        lines.append('| ' + ' | '.join(values) + ' |')
    if result.get('omnibus'):
        lines += ['', '## 总体检验', '',
                  '| 面板/指标 | 方法 | F统计量 | 分子自由度 | 分母自由度 | P值 |',
                  '| --- | --- | ---: | ---: | ---: | ---: |']
        for item in result['omnibus']:
            values = [label(item['panel']), item['method'], fmt(item['statistic']),
                      fmt(item['df_numerator']) if 'df_numerator' in item else '—',
                      fmt(item['df_denominator']) if 'df_denominator' in item else '—',
                      fmt(item['p_value'])]
            lines.append('| ' + ' | '.join(values) + ' |')
    if comparisons:
        lines += ['', '## 推断计算（' + confidence + '置信区间）', '',
                  '| 面板/指标 | A组 | B组 | 均值差（A−B） | 原始P值 | 调整后P值 | 区间下界 | 区间上界 | 区间口径 | P值校正 |',
                  '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |']
        for item in comparisons:
            values = [label(item['panel']), label(item['a']), label(item['b']),
                      format(item['mean_difference_a_minus_b'], '.6g'),
                      '—' if item['p_value'] is None else format(item['p_value'], '.6g'),
                      format(item['p_adjusted'], '.6g'), stat(item['ci_low']), stat(item['ci_high']),
                      label(item['ci_scope']), label(item['correction'])]
            lines.append('| ' + ' | '.join(values) + ' |')
        if any(item['p_value'] is None for item in comparisons):
            lines += ['', '“—”表示未另算未经多重比较调整的P值；所用方法直接返回比较族内调整后的P值。']
        lines += ['', '组名、均值差方向、P值与区间来自同一比较记录；完整精度见comparisons.csv及result.json。',
                  '计算结果不核实独立实验单位或生物学重复，不据此认定机制、因果或实际临床意义。']
    if reported:
        show_original = any(item['source_comparison_label'] != item['panel'] for item in reported)
        columns = ['指标', '比较'] + (['原表比较标签'] if show_original else []) + ['原P值', '校正状态', '来源']
        lines += ['', '## 原表P值转录', '', '| ' + ' | '.join(columns) + ' |',
                  '| ' + ' | '.join('---' for _ in columns) + ' |']
        for item in reported:
            adjustment = {'unknown': '原表未注明', 'adjusted': '已校正', 'unadjusted': '未校正'}[item['adjustment']]
            values = [label(item['panel']), label(comparison_label(item))]
            if show_original:
                values.append(label(item['source_comparison_label']))
            values += [label(item['display']), adjustment, label(item['source_p_cell'])]
            lines.append('| ' + ' | '.join(values) + ' |')
        lines += ['', 'P值计算方法、样本量与校正状态仅以原始来源为准，不由本次缺失处理反推。']
    lines += ['', '## 来源与数据处理核对', '',
              'n为有效观测数，不能由分组表头推定独立生物学重复。实测零值保留，空白按本次方案处理，不补零。']
    bibliography_first = {}
    for index, note in enumerate(result.get('caption_notes', []), 1):
        if note.get('purpose') == 'reading_definition':
            receipts = [item for item in result.get('source_note_receipts', [])
                        if item.get('field') == 'caption_notes' and item.get('note_index') == index - 1]
            bound = {}
            cells = []
            for receipt in receipts:
                bibliography = receipt.get('bibliography', {}).get('text')
                if bibliography:
                    bound.setdefault(bibliography, []).append(receipt['locator'])
                elif receipt.get('kind') in ('input_cell', 'input_text'):
                    cells.append(receipt['locator'])
            sources = []
            for bibliography, locators in bound.items():
                if bibliography in bibliography_first:
                    citation = '同〔来源' + str(bibliography_first[bibliography]) + '〕'
                else:
                    citation = bibliography
                    bibliography_first[bibliography] = index
                sources.append(citation + '；定位：' + '、'.join(dict.fromkeys(locators)))
            if cells:
                sources.append('原始输入：' + '、'.join(dict.fromkeys(cells)))
            text = '；'.join(sources) if sources else note['source']
            lines += ['', '〔来源' + str(index) + '〕' + label(text) + '。']
    lines += ['', '## 数值精度与运行环境', '',
              '表内均值和SD保留三位小数，极小非零量采用科学计数法；完整精度见result.json。',
              '本次实际Python及依赖版本见runtime-environment.json；requirements.txt记录受测依赖要求，不能将它单独作为本次实测版本证明。']
    lines += ['', '## 复现方法', '',
              '解压同次reproduction.zip，在解压目录运行：`python reproduce.py --out-root ./reproduced-output`。',
              '需使用满足requirements.txt的Python环境；包不捆绑解释器或第三方库。运行成功与数值、图面几何、文件字节一致是不同检查，未执行的比较不得声称已核验。']
    return '\n'.join(lines) + '\n'


def render_descriptive_report(result, tidy):
    """Compatibility entry point for the shared plot report."""
    return render_plot_report(result, tidy)


def render_inference_report(result, tidy):
    """Compatibility entry point; preserve one report from the same result."""
    return render_plot_report(result, tidy)


def checked_sd_segments(container, positions, means, deviations):
    try:
        segments = container.lines[2][0].get_segments()
    except (IndexError, AttributeError, TypeError) as exc:
        raise ValueError('Rendered SD bars are missing') from exc
    expected = [[[x, mean - sd], [x, mean + sd]]
                for x, mean, sd in zip(positions, means, deviations)]
    if len(segments) != len(expected) or any(len(segment) != 2 for segment in segments):
        raise ValueError('Rendered SD bars differ from statistical results')
    for segment, reference in zip(segments, expected):
        for actual_point, expected_point in zip(segment, reference):
            if len(actual_point) != 2 or any(not math.isclose(float(actual), float(value), rel_tol=1e-12, abs_tol=1e-12)
                                             for actual, value in zip(actual_point, expected_point)):
                raise ValueError('Rendered SD bars differ from statistical results')
    return [segment.tolist() for segment in segments]


def significance_label(comparison, style):
    """Format existing P values without changing a test, correction or threshold."""
    display = comparison.get('display')
    if display is None:
        display = 'P=' + format(comparison['p_adjusted'], '.4g')
    if style != 'p_and_stars':
        return display
    operator = comparison.get('operator', '=')
    value = comparison.get('value_or_bound', comparison.get('p_adjusted'))
    stars = ''
    for threshold, marker in ((.001, '***'), (.01, '**'), (.05, '*')):
        if ((operator == '<' and value <= threshold)
                or (operator in ('=', '<=') and value < threshold)):
            stars = marker
            break
    return display + (' ' + stars if stars else '')


def plot(result, tidy, stage):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from PIL import Image
    import textwrap
    labels = result.get('plot_labels') or {}
    if not isinstance(labels, dict):
        raise ValueError('Plot requires plot_labels with outcome name and unit')
    y_by_panel = labels.get('y_by_panel', {})
    common_y = labels.get('y', '')
    if not y_by_panel and (not isinstance(common_y, str) or not common_y.strip()):
        raise ValueError('Plot requires plot_labels.y or plot_labels.y_by_panel with outcome name and unit')
    if isinstance(common_y, str) and len(common_y) > 160:
        raise ValueError('Plot label too long')
    # macOS names first; Matplotlib registers the Noto CJK collection under its JP name on Linux.
    for name in ('Arial Unicode MS', 'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', 'WenQuanYi Micro Hei', 'Noto Sans CJK JP'):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            # DejaVu Sans ships with Matplotlib and supplies glyphs the CJK font lacks, such as superscript units.
            plt.rcParams['font.family'] = [name, 'DejaVu Sans']
            break
    panels = list(dict.fromkeys(g['panel'] for g in result['groups']))
    omnibus_by_panel = {item['panel']: item for item in result.get('omnibus', [])}
    ncols = result.get('plot_columns')
    if ncols is None:
        ncols = min(4, len(panels))
    if type(ncols) is not int or not 1 <= ncols <= min(10, len(panels)):
        raise ValueError('plot_columns must be an integer within the panel count and 1..10')
    nrows = math.ceil(len(panels)/ncols)
    if nrows > 10:
        raise ValueError('At most 40 plotted panels supported')
    timed_panels = {g['panel'] for g in result['groups'] if g['time'] is not None}
    sample_count_rows = max((1 if len({g['n'] for g in result['groups'] if g['panel'] == panel}) == 1
        else len({g['group'] for g in result['groups'] if g['panel'] == panel})
        for panel in timed_panels), default=0)
    comparison_counts = Counter(c['panel'] for c in result['comparisons'])
    comparison_counts.update(c['panel'] for c in result.get('reported_comparisons', []))
    dense_panels = {panel for panel in panels if panel not in timed_panels
                    and comparison_counts[panel] > 3
                    and not any(c['panel'] == panel for c in result.get('reported_comparisons', []))}
    annotation_height = .48 * max((count for panel, count in comparison_counts.items()
                                  if panel not in dense_panels), default=0) + .16 * sample_count_rows
    matrix_axes = {}
    if dense_panels:
        group_count = max(sum(g['panel'] == panel for g in result['groups']) for panel in dense_panels)
        matrix_height = .28 * (group_count + 1) + .4
        fig = plt.figure(figsize=(max(4.8, .65 * group_count)*ncols,
                                  (3.6 + annotation_height + matrix_height)*nrows))
        grid = fig.add_gridspec(nrows * 2, ncols,
                               height_ratios=[3.6 + annotation_height, matrix_height] * nrows)
        axes = [fig.add_subplot(grid[2 * (i // ncols), i % ncols]) for i in range(len(panels))]
        matrix_axes = {panel: fig.add_subplot(grid[2 * (i // ncols) + 1, i % ncols])
                       for i, panel in enumerate(panels) if panel in dense_panels}
    else:
        fig, grid_axes = plt.subplots(nrows, ncols,
                                     figsize=(4*ncols, (3.6 + annotation_height)*nrows), squeeze=False)
        axes = list(grid_axes.flat)
    rendered = []
    block_layout = {}
    comparison_tables = {}
    comparison_artists = []
    for ax, panel in zip(axes, panels):
        gs = [g for g in result['groups'] if g['panel'] == panel]
        timed = all(g['time'] is not None for g in gs)
        if any(g['time'] is not None for g in gs) and not timed:
            raise ValueError('Mixed timed and untimed cells in a panel')
        if timed and result.get('plot_blocks', {}).get(panel):
            raise ValueError('Plot blocks apply only to untimed scatter panels')
        if timed:
            if not isinstance(labels.get('x'), str) or not labels['x'].strip() or len(labels['x']) > 160:
                raise ValueError('Timed plot requires plot_labels.x with time unit')
            count_labels = []
            for group in dict.fromkeys(g['group'] for g in gs):
                points = sorted((g for g in gs if g['group'] == group), key=lambda g:g['time'])
                coordinates_text = '/'.join(f"{point['time']:.10g}" for point in points)
                counts_text = '/'.join(str(point['n']) for point in points)
                count_labels.append(f'{group} n (x={coordinates_text}): {counts_text}')
                container = ax.errorbar([g['time'] for g in points], [g['mean'] for g in points],
                                       yerr=[g['sample_sd'] for g in points], marker='o', capsize=3, label=group)
                actual_x, actual_y = container.lines[0].get_data()
                if list(actual_x) != [g['time'] for g in points] or list(actual_y) != [g['mean'] for g in points]:
                    raise ValueError('Rendered line coordinates differ from statistical results')
                segments = checked_sd_segments(container, [g['time'] for g in points],
                                               [g['mean'] for g in points], [g['sample_sd'] for g in points])
                rendered.append({'panel': panel, 'group': group, 'type': 'mean_line',
                                 'coordinates': [[float(x), float(y)] for x, y in zip(actual_x, actual_y)],
                                 'sd_bar_endpoints': segments})
                if result.get('plot_raw_points'):
                    observations = [row for row in tidy if row['panel'] == panel and row['group'] == group]
                    collection = ax.scatter([row['time'] for row in observations], [row['value'] for row in observations],
                                            s=15, alpha=.7, color=container.lines[0].get_color())
                    rendered.append({'panel': panel, 'group': group, 'type': 'raw_scatter',
                                     'source_cells': [row['source_cell'] for row in observations],
                                     'source_values': [row['value'] for row in observations],
                                     'coordinates': collection.get_offsets().tolist()})
            if len({g['n'] for g in gs}) == 1:
                position = 'x value' if result.get('coordinate_kind') == 'numeric' else 'time point'
                count_labels = [f"All groups: n={gs[0]['n']} at each {position}"]
            annotations = [comparison_label(row) + ': ' + row['display']
                           for row in result.get('reported_comparisons', []) if row['panel'] == panel]
            ax.set_xlabel('\n'.join([labels['x'], *count_labels, *annotations]), fontsize=8)
            ax.legend(fontsize=8)
        else:
            blocks = result.get('plot_blocks', {}).get(panel, [])
            names = [g['group'] for g in gs]
            positions = list(range(len(gs)))
            if blocks:
                if not isinstance(blocks, list) or any(
                        not isinstance(b, dict) or set(b) != {'label', 'groups'} or
                        not isinstance(b['label'], str) or not b['label'].strip() or
                        not isinstance(b['groups'], list) or not b['groups'] for b in blocks):
                    raise ValueError('Plot blocks require labels and nonempty ordered groups')
                if [name for b in blocks for name in b['groups']] != names:
                    raise ValueError('Plot blocks must cover each panel group once in source order')
                positions = []
                block_layout[panel] = []
                offset = 0
                for index, block in enumerate(blocks):
                    xs = [offset + j + index * .8 for j in range(len(block['groups']))]
                    positions.extend(xs)
                    center = (xs[0] + xs[-1]) / 2
                    ax.text(center, 1.03, block['label'], ha='center', transform=ax.get_xaxis_transform())
                    block_layout[panel].append({'label': block['label'], 'groups': block['groups'], 'positions': xs})
                    offset += len(block['groups'])
            for i, group in zip(positions, gs):
                observations = [x for x in tidy if x['panel'] == panel and x['group'] == group['group']]
                values = [x['value'] for x in observations]
                offsets = [(k-(len(values)-1)/2)*.035 for k in range(len(values))]
                collection = ax.scatter([i+x for x in offsets], values, s=20)
                coordinates = collection.get_offsets().tolist()
                if len(coordinates) != len(values) or any(
                        not math.isclose(point[1], value, rel_tol=0, abs_tol=1e-12) or
                        not math.isclose(point[0], i + offset, rel_tol=0, abs_tol=1e-12)
                        for point, value, offset in zip(coordinates, values, offsets)):
                    raise ValueError('Rendered scatter coordinates differ from source values')
                container = ax.errorbar(i, group['mean'], yerr=group['sample_sd'], fmt='_', color='black', capsize=5)
                mean_x, mean_y = container.lines[0].get_data()
                if list(mean_x) != [i] or list(mean_y) != [group['mean']]:
                    raise ValueError('Rendered mean marker differs from statistical results')
                segments = checked_sd_segments(container, [i], [group['mean']], [group['sample_sd']])
                rendered.append({'panel': panel, 'group': group['group'], 'type': 'raw_scatter',
                                 'source_cells': [o['source_cell'] for o in observations],
                                 'source_values': values, 'coordinates': coordinates,
                                 'mean_marker': [float(mean_x[0]), float(mean_y[0])],
                                 'sd_bar_endpoints': segments})
            tick_labels = [f"{i + 1}. {g['group']}\n(n={g['n']})" if panel in matrix_axes
                           else f"{g['group']}\n(n={g['n']})" for i, g in enumerate(gs)]
            ax.set_xticks(positions, tick_labels, rotation=25, ha='right')
            style = result.get('significance_style', 'p')
            annotations = [comparison_label(c) + '\n' + significance_label(c, style)
                           for c in result['comparisons'] if c['panel'] == panel]
            annotations.extend(comparison_label(c) + '\n' + significance_label(c, style)
                               for c in result.get('reported_comparisons', []) if c['panel'] == panel)
            if panel in matrix_axes:
                cells = [['—' if a == b else '' for b in names] for a in names]
                for comparison in result['comparisons']:
                    if comparison['panel'] == panel:
                        cells[names.index(comparison['a'])][names.index(comparison['b'])] = significance_label(comparison, style)
                matrix_ax = matrix_axes[panel]
                matrix_ax.set_axis_off()
                correction = next(c['correction'] for c in result['comparisons'] if c['panel'] == panel)
                matrix_ax.set_title(('P' if correction == 'none' else 'Adjusted P')
                                    + ' (upper triangle; groups numbered as in plot)', fontsize=8)
                numbers = [str(i + 1) for i in range(len(names))]
                table = matrix_ax.table(cellText=cells, rowLabels=numbers, colLabels=numbers,
                                        cellLoc='center', bbox=[0, 0, 1, 1])
                table.auto_set_font_size(False)
                table.set_fontsize(7)
                for row in range(1, len(names) + 1):
                    table[(row, -1)].PAD = .4
                comparison_artists.append(table)
                comparison_artists.extend(cell.get_text() for cell in table.get_celld().values())
                comparison_tables[panel] = {'groups': names, 'cells': cells}
                annotations = []
            if annotations or panel in matrix_axes:
                if result['comparisons']:
                    method_name = {
                        'student_t': 'Student t-test (two-sided)',
                        'welch_t': 'Welch t-test (two-sided)',
                        'paired_t': 'Paired t-test (two-sided)',
                        'pairwise_t_holm': ('Paired t-test (two-sided)' if result['design'] == 'paired'
                                            else 'Welch t-test (two-sided)'),
                        'tukey_hsd': 'Tukey HSD',
                        'one_way_anova': ('One-way ANOVA + Tukey' if omnibus_by_panel.get(panel, {}).get('variance_model') == 'equal'
                                          else 'Welch ANOVA + Games-Howell'),
                    }[result['method']]
                    correction = next(c['correction'] for c in result['comparisons'] if c['panel'] == panel)
                    p_kind = 'unadjusted P' if correction == 'none' else 'adjusted P'
                    correction_label = 'no multiplicity adjustment' if correction == 'none' else correction
                    annotations.extend(['Mean ± sample SD', method_name + '; ' + correction_label + '; ' + p_kind])
                    overall = omnibus_by_panel.get(panel)
                    if overall:
                        annotations.append('Overall F=' + format(overall['statistic'], '.4g')
                                           + ', P=' + format(overall['p_value'], '.4g'))
                ax.set_xlabel('\n'.join(annotations), fontsize=8)
        ax.set_title(textwrap.fill(str(panel), width=28, break_on_hyphens=False),
                     fontsize=10, pad=32 if panel in block_layout else 6)
        ax.set_ylabel(textwrap.fill(y_by_panel.get(panel, common_y), width=28), fontsize=9)
        ax.grid(alpha=.15)
    for ax in axes[len(panels):]:
        ax.set_visible(False)
    fig.tight_layout()
    plt.rcParams['svg.fonttype'] = 'path'
    extra_labels = [label for ax in axes for label in (ax.xaxis.label, ax.yaxis.label)]
    extra_labels.extend(text for ax in axes for text in ax.texts)
    extra_labels.extend(comparison_artists)
    fig.savefig(stage/'figure.svg', bbox_inches='tight', bbox_extra_artists=extra_labels)
    fig.savefig(stage/'figure.tiff', dpi=300, bbox_inches='tight',
                bbox_extra_artists=extra_labels, pil_kwargs={'compression': 'tiff_lzw'})
    plt.close(fig)
    with Image.open(stage/'figure.tiff') as im:
        im.convert('RGB').save(stage/'figure.tiff', dpi=(300, 300), compression='tiff_lzw')
    with Image.open(stage/'figure.tiff') as im:
        if not all(abs(float(x)-300) < .01 for x in im.info['dpi']):
            raise ValueError('TIFF DPI validation failed')
        result['figure'] = {'pixels': list(im.size), 'dpi': 300, 'mode': im.mode}
        write_preview(im, stage/'figure-preview.png')
    proof = {'schema': 1, 'status': 'verified',
             'scope': 'Matplotlib coordinates recorded; mean coordinates and SD bars checked before export. Source semantics and medical approval require separate review.',
             'figure_svg_sha256': digest(stage/'figure.svg'), 'figure_tiff_sha256': digest(stage/'figure.tiff'),
             'series': rendered, 'block_layout': block_layout, 'comparison_tables': comparison_tables,
             'axis_labels': [{'panel': panel, 'x': ax.get_xlabel(), 'y': ax.get_ylabel(),
                              'tick_labels': [label.get_text() for label in ax.get_xticklabels()]}
                             for panel, ax in zip(panels, axes)]}
    (stage/'rendered-points.json').write_text(json.dumps(proof, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    result['rendering_check'] = {'status': 'verified', 'evidence_file': 'rendered-points.json',
                                 'figure_svg_sha256': proof['figure_svg_sha256'],
                                 'figure_tiff_sha256': proof['figure_tiff_sha256']}



def write_preview(image, path):
    """Save a PNG copy small enough for an image-reading tool; the TIFF and SVG stay the deliverables."""
    preview = image.convert('RGB')
    if preview.width > PREVIEW_WIDTH:
        preview = preview.resize((PREVIEW_WIDTH, round(preview.height * PREVIEW_WIDTH / preview.width)))
    preview.save(path, format='PNG')


def check_reproduction(stage):
    """Run the packaged reproduce.py from a fresh extraction and require identical tables and report.

    Returns the compared file names. Raises ValueError when the replay fails or any compared file differs.
    """
    compared = [path.name for path in sorted(stage.iterdir())
                if path.suffix == '.csv' or path.name in ('report.md', 'delivery.md')]
    with tempfile.TemporaryDirectory(prefix='medical-statistics-replay-') as tmp:
        package = Path(tmp) / 'package'
        with zipfile.ZipFile(stage / 'reproduction.zip') as archive:
            archive.extractall(package)
        replay = subprocess.run([sys.executable, str(package / 'reproduce.py'), '--out-root', str(Path(tmp) / 'out')],
                                capture_output=True, text=True, timeout=600,
                                env={**os.environ, REPLAY_ENVIRONMENT: '1'})
        if replay.returncode != 0:
            detail = (replay.stderr.strip().splitlines() or ['exit status ' + str(replay.returncode)])[-1]
            raise ValueError('Reproduction check failed: ' + detail)
        output = Path(replay.stdout.splitlines()[0])
        different = [name for name in compared if (output / name).read_bytes() != (stage / name).read_bytes()]
    if different:
        raise ValueError('Reproduction check failed: ' + ', '.join(different) + ' differ from this run')
    return compared


def run_summary(destination, result, checked):
    """Text printed after the run directory: deliverables, automatic checks, and the report the answer uses."""
    lines = [str(destination)]
    delivery = destination / 'delivery.md'
    if delivery.is_file():
        lines += ['', '【交付】', delivery.read_text(encoding='utf-8').strip()]
    checks = []
    for name, key in (('figure.tiff', 'figure'), ('heatmap.tiff', 'heatmap')):
        info = result.get(key)
        if info:
            checks.append(f"- {name}：{info['pixels'][0]}×{info['pixels'][1]} 像素，{info['dpi']} dpi，{info['mode']}。")
    rendering = (result.get('rendering_check') or {}).get('status')
    if rendering == 'verified':
        checks.append('- 图中均值、SD 端点和散点坐标已在导出前与结果核对（rendered-points.json）。')
    elif rendering == 'custom_renderer_requires_review':
        checks.append('- figure.svg、figure.tiff 为自定义重绘结果（render-variant.json）。rendered-points.json 只对应原始分析图 '
                      'analysis-figure.*，重绘图的标签、几何和数值需在预览中读回核对。')
    if checked:
        checks.append('- 复现自检：已在临时目录解压 reproduction.zip 并运行 reproduce.py，'
                      + '、'.join(checked) + ' 与本次逐字节一致（reproduction-check.json）。')
    if checks:
        lines += ['', '【自动核对】', *checks]
    report = destination / 'report.md'
    if report.is_file():
        previews = [name for name in ('figure-preview.png', 'heatmap-preview.png') if (destination / name).is_file()]
        lines += ['', '【report.md 全文】', report.read_text(encoding='utf-8').strip(), '', '【下一步】',
                  '用 ViewImage 查看 ' + '、'.join(previews) + ' 一次，检查标注、遮挡和裁切。'
                  '需要改图注、标签或方案时修改方案后重跑，不手改报告。'
                  '无需修改时 present_files 本目录的图、report.md 和 reproduction.zip；最终回复沿用【交付】正文并附文件链接，'
                  '数值和图注以 report.md 为准。以上核对已完成，不再另读报告、重跑复现包或编写校验脚本。']
    else:
        for name in ('summary.csv', 'omnibus.csv', 'comparisons.csv'):
            if (destination / name).is_file():
                lines += ['', f'【{name}】', (destination / name).read_text(encoding='utf-8-sig').strip()]
    return '\n'.join(lines)


def plot_row_z(result, stage):
    import numpy as np
    import matplotlib.pyplot as plt
    from PIL import Image
    data = result['row_z']
    values = np.array(data['values'], dtype=float)
    bound = float(np.nanmax(np.abs(values)))
    cmap = plt.get_cmap('coolwarm').copy()
    cmap.set_bad('#66ff66')
    fig, ax = plt.subplots(figsize=(11, max(5, len(data['panels']) * .4)))
    image = ax.pcolormesh(np.arange(values.shape[1] + 1) - .5,
                         np.arange(values.shape[0] + 1) - .5,
                         np.ma.masked_invalid(values), cmap=cmap,
                         vmin=-bound, vmax=bound, rasterized=False)
    ax.set_ylim(values.shape[0] - .5, -.5)
    for row, column in np.argwhere(np.isnan(values)):
        ax.text(column, row, 'x', ha='center', va='center', color='black', fontsize=8)
    ax.set_yticks(range(len(data['panels'])), data['panels'], fontsize=9)
    ax.set_xticks(range(len(data['columns'])),
                  [c['group'] + ' [' + c['source_column'] + ']' for c in data['columns']], rotation=90, fontsize=8)
    ax.set_title('Row-wise Z score; original row/column order')
    ax.set_xlabel('Group [source column]; green cell with black x = blank (not zero)')
    colorbar = fig.colorbar(image, ax=ax, label='Z score (ddof=' + str(data['ddof']) + ')')
    if colorbar.solids is not None:
        colorbar.solids.set_rasterized(False)
    fig.tight_layout()
    fig.savefig(stage / 'heatmap.svg')
    fig.savefig(stage / 'heatmap.tiff', dpi=300, pil_kwargs={'compression': 'tiff_lzw'})
    plt.close(fig)
    with Image.open(stage / 'heatmap.tiff') as im:
        im.convert('RGB').save(stage / 'heatmap.tiff', dpi=(300, 300), compression='tiff_lzw')
    with Image.open(stage / 'heatmap.tiff') as im:
        if not all(abs(float(x) - 300) < .01 for x in im.info['dpi']):
            raise ValueError('Heatmap TIFF DPI validation failed')
        result['heatmap'] = {'pixels': list(im.size), 'dpi': 300, 'mode': im.mode}
        write_preview(im, stage / 'heatmap-preview.png')


def main():
    parser = argparse.ArgumentParser(description='根据明确方案执行统计计算与图形导出')
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--csv', type=Path)
    inputs.add_argument('--xlsx', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out-root', type=Path, required=True)
    parser.add_argument('--plot', action='store_true')
    parser.add_argument('--renderer', type=Path, help='可选单文件重绘入口，接受 --run-dir/--out-dir；随同次产物打包复现')
    args = parser.parse_args()
    renderer_bytes = None
    if args.renderer:
        if not args.plot:
            raise ValueError('--renderer requires --plot')
        renderer_bytes = args.renderer.read_bytes()
    if args.plan.stat().st_size > 65536:
        raise ValueError('Plan size limit exceeded')
    plan_raw = args.plan.read_bytes()
    plan = json.loads(plan_raw)
    if args.plot and plan.get('source_origin') not in ('attached_data', 'published_data'):
        raise ValueError('A plot plan requires source_origin=published_data when this dataset is linked to an original article, '
                         'or attached_data for data without an identified publication. This describes provenance, not the analysis design. '
                         'Published data require the already-read source definitions in caption_notes; a data-only caption is insufficient.')
    source = args.csv or args.xlsx
    if source and source.stat().st_size > MAX_BYTES:
        raise ValueError('Input size limit exceeded')
    if not source and plan.get('method') != 'proportion_precision':
        raise ValueError('Explicit CSV or XLSX required')
    raw = source.read_bytes() if source else b''
    plan, note_receipts, note_sources = verify_notes(raw, source.suffix.lower() if source else '.csv', plan, args.plan.resolve().parent)
    if args.plot:
        verify_figure_definitions(plan)
    result, tidy = calculate(raw, plan, source.suffix.lower() if source else '.csv')
    result['source_note_receipts'] = note_receipts
    result['source_note_validation_scope'] = 'Exact source quotations and locations only; interpretation and clinical meaning require separate review.'
    requirements = Path(__file__).with_name('requirements.txt')
    if not requirements.is_file():
        requirements = Path(__file__).resolve().parents[1]/'requirements.txt'
    implementation = {'analyze.py': Path(__file__), '_artifact_store.py': Path(__file__).with_name('_artifact_store.py'),
                      '_anova.py': Path(__file__).with_name('_anova.py'),
                      '_delivery.py': Path(__file__).with_name('_delivery.py'),
                      '_source_notes.py': Path(__file__).with_name('_source_notes.py'),
                      'requirements.txt': requirements}
    result['implementation'] = {name: digest(path) for name, path in implementation.items()}
    with tempfile.TemporaryDirectory(prefix='medical-statistics-') as tmp:
        stage = Path(tmp)
        environment = {'python': result['runtime']['python'], 'packages': result['runtime']['packages'],
                       'scope': 'Package versions observed during this invocation; requirements.txt is a separate dependency contract.'}
        (stage/'runtime-environment.json').write_text(json.dumps(environment, ensure_ascii=False, indent=2)+'\n')
        result['runtime_environment_file'] = 'runtime-environment.json'
        input_paths = [stage/'plan.json']
        (stage/'original-plan.json').write_bytes(plan_raw)
        (stage/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
        for name, data in note_sources.items():
            (stage/name).write_bytes(data)
            input_paths.append(stage/name)
        if source:
            snapshot = stage/('input' + source.suffix.lower())
            snapshot.write_bytes(raw)
            input_paths.append(snapshot)
        for name, records in (('summary.csv', result.get('groups', [])), ('omnibus.csv', result.get('omnibus', [])), ('comparisons.csv', result.get('comparisons', [])), ('reported-comparisons.csv', result.get('reported_comparisons', [])), ('input-tidy.csv', tidy), ('row-z.csv', row_z_records(result, tidy))):
            if records:
                with (stage/name).open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(records[0]))
                    writer.writeheader()
                    writer.writerows(records)
        if args.plot and tidy:
            bind_source_axis_labels(raw, plan, source.suffix.lower(), result)
            plot(result, tidy, stage)
            if result.get('row_z'):
                plot_row_z(result, stage)
            (stage/'report.md').write_text(render_plot_report(result, tidy), encoding='utf-8')
        if (stage/'report.md').is_file():
            (stage/'delivery.md').write_text(render_delivery(stage), encoding='utf-8')
        (stage/'result.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
        for name, path in implementation.items():
            (stage/name).write_bytes(path.read_bytes())
        if renderer_bytes is not None:
            if not tidy or not (stage/'figure.svg').is_file():
                raise ValueError('--renderer requires a supported plotted dataset')
            variant = render_variant(stage, renderer_bytes)
            result['figure'] = variant['figure']
            from PIL import Image
            with Image.open(stage/'figure.tiff') as im:
                write_preview(im, stage/'figure-preview.png')
            result['rendering_check'] = {
                'status': 'custom_renderer_requires_review', 'evidence_file': 'render-variant.json',
                'figure_svg_sha256': variant['figure_svg_sha256'],
                'figure_tiff_sha256': variant['figure_tiff_sha256'],
                'source_result': variant['source_result'],
            }
            (stage/'result.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
        replay = ["import argparse, subprocess, sys", "from pathlib import Path", "p=argparse.ArgumentParser()",
                  "p.add_argument('--out-root',required=True)", "a=p.parse_args()", "d=Path(__file__).resolve().parent",
                  "cmd=[sys.executable,str(d/'analyze.py'),'--plan',str(d/'plan.json'),'--out-root',a.out_root]"]
        if source:
            replay.append(f"cmd.extend([{('--xlsx' if args.xlsx else '--csv')!r},str(d/{('input'+source.suffix.lower())!r})])")
        if args.plot:
            replay.append("cmd.append('--plot')")
        if renderer_bytes is not None:
            replay.append("cmd.extend(['--renderer',str(d/'render_variant.py')])")
        replay.append('subprocess.run(cmd,check=True)')
        (stage/'reproduce.py').write_text('\n'.join(replay)+'\n')
        published_files = package_snapshot(stage)
        checked = []
        if os.environ.get(REPLAY_ENVIRONMENT) != '1':
            checked = check_reproduction(stage)
            (stage/'reproduction-check.json').write_text(json.dumps(
                {'status': 'identical', 'compared': checked,
                 'scope': 'Byte comparison of tables and report from reproduce.py run in a fresh extraction of reproduction.zip.'},
                ensure_ascii=False, indent=2)+'\n')
            published_files.append(stage/'reproduction-check.json')
        destination = publish(args.out_root, input_paths, published_files)
        manifest = json.loads((destination/'manifest.json').read_text())
        for entry in manifest['inputs']:
            entry['file'] = Path(entry['file']).name
        (destination/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
        print(run_summary(destination, result, checked))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, OverflowError, ImportError, KeyError, TypeError, zipfile.BadZipFile,
            subprocess.TimeoutExpired) as exc:
        print('Analysis stopped: ' + str(exc), file=sys.stderr)
        sys.exit(2)
