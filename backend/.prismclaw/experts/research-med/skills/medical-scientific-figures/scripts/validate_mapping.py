import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string, get_column_letter, range_boundaries


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(root, plan, observations_path):
    path = root / plan['workbook']
    if sha(path) != plan['sha256']:
        raise ValueError('Source workbook hash differs from the mapping')
    workbook = load_workbook(path, data_only=True)
    sheet = workbook[plan['sheet']]
    expected = {}
    errors = []
    counts = {}
    header_conflicts = []
    for group in plan['groups']:
        first = column_index_from_string(group['first_column'])
        last = column_index_from_string(group['last_column'])
        header = sheet[group['header_cell']]
        merged = [item for item in sheet.merged_cells.ranges if header.coordinate in item]
        anchor = sheet.cell(merged[0].min_row, merged[0].min_col) if merged else header
        if anchor.value != group['header_text']:
            raise ValueError(f'Header text mismatch at {header.coordinate}')
        if merged and not (merged[0].min_col <= first <= last <= merged[0].max_col):
            resolution = group.get('merged_header_resolution')
            if not resolution or not all(resolution.get(key) for key in ['source', 'location', 'reason']):
                raise ValueError(f'Mapping conflicts with merged header {merged[0]}; source-backed resolution required')
            resolution_path = root / resolution['source']
            header_conflicts.append({'group': group['name'], 'merged_range': str(merged[0]),
                                     'mapping_columns': [group['first_column'], group['last_column']],
                                     'resolution': resolution, 'resolution_source_sha256': sha(resolution_path),
                                     'semantic_review': 'Required; program records source and enforces explicit resolution'})
        counts[group['name']] = []
        for row_spec, wanted_n in zip(plan['rows'], group['expected_n'], strict=True):
            count = 0
            for column in range(first, last + 1):
                cell = sheet.cell(row_spec['row'], column)
                if cell.value is None:
                    continue
                if isinstance(cell.value, bool) or not isinstance(cell.value, (int, float)) or not math.isfinite(cell.value):
                    raise ValueError(f'Invalid observation at {cell.coordinate}')
                if cell.coordinate in expected:
                    raise ValueError(f'Overlapping mapping at {cell.coordinate}')
                expected[cell.coordinate] = {'source_cell': cell.coordinate, 'group': group['name'],
                                            'position': row_spec['position'], 'source_value': cell.value,
                                            'plot_value': cell.value * plan['scale']}
                count += 1
            counts[group['name']].append(count)
            if count != wanted_n:
                errors.append({'kind': 'source_n_mismatch', 'group': group['name'],
                               'row': row_spec['row'], 'actual': count, 'expected': wanted_n})
    region = {cell.coordinate for row in sheet[plan['region']] for cell in row if cell.value is not None}
    if region != set(expected):
        errors.append({'kind': 'region_coverage', 'unmapped': sorted(region - expected.keys()),
                       'outside': sorted(expected.keys() - region)})
    if observations_path:
        with observations_path.open(newline='') as handle:
            observations = list(csv.DictReader(handle))
        seen = Counter(item['source_cell'] for item in observations)
        if set(seen) != set(expected) or any(count != 1 for count in seen.values()):
            errors.append({'kind': 'observation_coverage', 'missing': sorted(expected.keys() - seen.keys()),
                           'extra': sorted(seen.keys() - expected.keys()),
                           'duplicates': {cell: count for cell, count in seen.items() if count > 1}})
        for item in observations:
            cell = item['source_cell']
            if cell not in expected:
                continue
            for field, wanted in expected[cell].items():
                actual = item[field]
                equal = actual == wanted if isinstance(wanted, str) else math.isclose(
                    float(actual), wanted, rel_tol=1e-12, abs_tol=1e-12)
                if not equal:
                    errors.append({'kind': 'observation_mismatch', 'cell': cell, 'field': field,
                                   'actual': actual, 'expected': wanted})
    left, top, right, bottom = range_boundaries(plan['region'])
    profile = [{'column': get_column_letter(column),
                'nonempty': sum(sheet.cell(row, column).value is not None for row in range(top, bottom + 1))}
               for column in range(left, right + 1)]
    source = root / plan['semantic_evidence']['local_source']
    report = {'status': 'failed' if errors else 'passed', 'errors': errors,
              'workbook_sha256': sha(path), 'source_evidence_sha256': sha(source),
              'semantic_evidence': plan['semantic_evidence'], 'counts': counts,
              'merged_ranges': [str(item) for item in sheet.merged_cells.ranges],
              'merged_header_conflicts': header_conflicts,
              'headers': {group['header_cell']: sheet[group['header_cell']].value for group in plan['groups']},
              'column_profile': profile, 'observations': list(expected.values()),
              'checked_observations_sha256': sha(observations_path) if observations_path else None}
    workbook.close()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--emit-observations', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.mapping.read_text())
    report = validate(args.root, plan, args.observations)
    report['mapping_sha256'] = sha(args.mapping)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if report['status'] == 'passed' and args.emit_observations:
        with args.emit_observations.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(report['observations'][0]))
            writer.writeheader()
            writer.writerows(report['observations'])
    print(json.dumps({'status': report['status'], 'errors': report['errors'], 'counts': report['counts']}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
