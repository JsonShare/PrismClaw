"""Read an explicit worksheet rectangle without shifting cell coordinates or imputing blanks."""
import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import sys


def inspect(source, sheet, region):
    from openpyxl import load_workbook
    from openpyxl.utils.cell import range_boundaries, get_column_letter
    source = Path(source)
    if source.stat().st_size > 50_000_000:
        raise ValueError('Workbook exceeds 50 MB')
    left, top, right, bottom = range_boundaries(region)
    if not all(type(value) is int and value > 0 for value in (left, top, right, bottom)):
        raise ValueError('An explicit finite rectangle is required')
    if right < left or bottom < top or (right - left + 1) * (bottom - top + 1) > 4096:
        raise ValueError('Rectangle must contain 1 through 4096 cells')
    raw = source.read_bytes()
    values_book = load_workbook(io.BytesIO(raw), data_only=True)
    formulas_book = load_workbook(io.BytesIO(raw), data_only=False)
    try:
        values = values_book[sheet]
        formulas = formulas_book[sheet]
        cells = []
        blanks = []
        zeros = []
        formulas_without_cache = []
        counts = {get_column_letter(column): {'number': 0, 'blank': 0, 'numeric_zero': 0,
                  'formula_without_cached_value': 0, 'other': 0} for column in range(left, right + 1)}
        for row in values.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
            for cell in row:
                original = formulas[cell.coordinate]
                value = cell.value
                formula = original.value if original.data_type == 'f' else None
                if formula and value is None:
                    kind = 'formula_without_cached_value'
                    formulas_without_cache.append(cell.coordinate)
                elif value is None:
                    kind = 'blank'
                    blanks.append(cell.coordinate)
                elif isinstance(value, bool):
                    kind = 'boolean'
                elif isinstance(value, (float, int)):
                    kind = 'number'
                    if value == 0:
                        zeros.append(cell.coordinate)
                elif isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
                    kind = 'date_or_time'
                    value = value.isoformat()
                else:
                    kind = 'text'
                    value = str(value)
                cells.append({'cell': cell.coordinate, 'value': value, 'kind': kind,
                              **({'formula': formula} if formula else {})})
                column = counts[get_column_letter(cell.column)]
                column[kind if kind in ('number', 'blank', 'formula_without_cached_value') else 'other'] += 1
                if kind == 'number' and value == 0:
                    column['numeric_zero'] += 1
        return {'schema': 1, 'sourceFile': source.name,
                'sourceSha256': hashlib.sha256(raw).hexdigest(), 'sheet': sheet, 'range': region,
                'mergedRanges': [str(value) for value in values.merged_cells.ranges],
                'cells': cells, 'blankCells': blanks, 'numericZeroCells': zeros,
                'columnCounts': counts,
                'countScope': '仅指定区域逐格计数，不代表独立生物学样本量；不补点或删点，不推断区域外观测',
                'formulasWithoutCachedValues': formulas_without_cache,
                'scope': '原单元格读取；不推断分组、单位、设计或科学映射，不填补空白'}
    finally:
        values_book.close()
        formulas_book.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--sheet', required=True)
    parser.add_argument('--range', required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.input, args.sheet, args.range), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print('Workbook inspection stopped: ' + str(error), file=sys.stderr)
        sys.exit(2)
