"""Deterministic analysis for Vietnamese ledger-style Excel tables.

The module reads workbook layout directly with openpyxl so merged headers,
total rows, and surrounding report text are handled before data is converted
to a table-like structure.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from src.utils.common import normalize_text


EMPTY_NUMERIC_VALUES = {"", "-", "–", "—"}
# A column needs this many figures before it counts as a data column at all.
MIN_NUMERIC_ROWS = 2
# A merged banner, spread across the row, repeats one value; a header row does
# not. That is what separates the two once merged cells are propagated.
MIN_DISTINCT_HEADERS = 2
MAX_HEADER_ROWS = 3
TOTAL_LABELS = {"tong", "tong cong", "cong", "total", "sum"}
LEDGER_TEXT_HEADERS = {"stt", "ma khach hang", "ten khach hang", "ten don vi"}
INVENTORY_TEXT_HEADERS = {"loai xe", "hang hoa", "mat hang", "ten hang"}


@dataclass(frozen=True)
class TableRow:
    """One detail row retained from the source sheet."""

    excel_row: int
    labels: dict[str, str]
    values: dict[str, Decimal]


@dataclass(frozen=True)
class TopRow:
    """A detail row ranked by one numeric column."""

    excel_row: int
    labels: dict[str, str]
    value: Decimal


@dataclass(frozen=True)
class ColumnSummary:
    """Totals and largest rows for a numeric column."""

    excel_column: int
    column_name: str
    calculated_total: Decimal
    reported_total: Decimal | None
    delta: Decimal | None
    top_rows: list[TopRow]


@dataclass(frozen=True)
class SheetAnalysis:
    """Analysis result for one detected table inside a sheet."""

    sheet_name: str
    table_type: str
    header_rows: list[int]
    total_row: int | None
    data_rows: int
    period: dict[str, str]
    columns: list[ColumnSummary]


def analyze_ledger_workbook(
    excel_path: str | Path,
    top_n: int = 5,
) -> list[SheetAnalysis]:
    """Analyze every ledger-like table in an XLSX workbook.

    Returns one analysis per sheet that contains a supported table pattern.
    Currently supported patterns:
    - one-row counterparty ledgers with columns such as "Dư nợ đầu kỳ";
    - two-row inventory movement tables with merged group headers.
    """

    workbook = load_workbook(excel_path, data_only=True)
    try:
        analyses: list[SheetAnalysis] = []
        for worksheet in workbook.worksheets:
            grid = _expanded_grid(worksheet)
            analysis = _analyze_sheet(worksheet, grid, top_n)
            if analysis is not None:
                analyses.append(analysis)
        return analyses
    finally:
        workbook.close()


def analyses_to_dict(analyses: list[SheetAnalysis]) -> list[dict[str, Any]]:
    """Convert analysis dataclasses into JSON-friendly dictionaries."""

    return [_json_ready(asdict(analysis)) for analysis in analyses]


def analyses_to_json(analyses: list[SheetAnalysis], indent: int = 2) -> str:
    """Serialize analyses with exact decimal values rendered as JSON numbers."""

    return json.dumps(
        analyses_to_dict(analyses),
        ensure_ascii=False,
        indent=indent,
    )


def _analyze_sheet(
    worksheet: Worksheet,
    grid: list[list[Any]],
    top_n: int,
) -> SheetAnalysis | None:
    detected = _detect_table(grid)
    if detected is None:
        return None
    header_rows, first_data_row = detected
    return _analyze_table(worksheet.title, grid, header_rows, first_data_row, top_n)


def _expanded_grid(worksheet: Worksheet) -> list[list[Any]]:
    """Return a 1-index-friendly sheet grid with merged cells propagated."""

    grid: list[list[Any]] = [
        [None] * (worksheet.max_column + 1)
        for _ in range(worksheet.max_row + 1)
    ]
    for row in range(1, worksheet.max_row + 1):
        for column in range(1, worksheet.max_column + 1):
            grid[row][column] = worksheet.cell(row, column).value

    for merged in worksheet.merged_cells.ranges:
        value = worksheet.cell(merged.min_row, merged.min_col).value
        for row in range(merged.min_row, merged.max_row + 1):
            for column in range(merged.min_col, merged.max_col + 1):
                grid[row][column] = value

    return grid


def _numeric_column_set(grid: list[list[Any]]) -> set[int]:
    """Columns that carry figures, counted across the whole sheet.

    This has to be settled BEFORE looking for the first data row. Deciding a row
    is data because it holds two or more figures fails twice: a table with only
    one numeric column has no such row, and a printed "Tổng cộng" row that stops
    after one column stops being recognisable as the start of the table.
    """

    width = max((len(row) for row in grid), default=0)
    counts = {column: 0 for column in range(1, width)}
    for row in grid[1:]:
        for column in range(1, min(width, len(row))):
            if _has_numeric_cell(row[column]):
                counts[column] += 1
    return {column for column, seen in counts.items() if seen >= MIN_NUMERIC_ROWS}


def _header_texts(row: list[Any]) -> list[str]:
    return [
        _cell_text(value) for value in row[1:]
        if _cell_text(value) and not _has_numeric_cell(value)
    ]


def _detect_table(grid: list[list[Any]]) -> tuple[list[int], int] | None:
    """The header rows and the first data row, found by shape rather than by name.

    The module used to require a fixed vocabulary — {stt, ma khach hang, ten
    khach hang} or {so luong, gia tri} — and a ledger the customer wrote
    themselves often uses none of it. A sheet that matched nothing was dropped
    in silence, which is the worst way to lose an account: the record stays
    valid JSON and simply has one fewer entry in it. Measured on four ordinary
    variants (no "Stt" column, "Khách hàng" instead of "Tên khách hàng", English
    headings, no banner) all four vanished.

    Three rules replace the vocabulary, and none of them needs to know what a
    column is called.
    """

    numeric = _numeric_column_set(grid)
    if not numeric:
        return None

    # A data row carries a figure in a data column AND a label outside of them.
    # The second half is what stops a banner that happens to contain a number.
    first_data_row = None
    for number, row in enumerate(grid):
        if number < 1:
            continue
        has_figure = any(
            column < len(row) and _has_numeric_cell(row[column])
            for column in numeric
        )
        has_label = any(
            column not in numeric and _cell_text(value)
            and not _has_numeric_cell(value)
            for column, value in enumerate(row) if column
        )
        if has_figure and has_label:
            first_data_row = number
            break
    if first_data_row is None or first_data_row == 1:
        return None

    header_rows: list[int] = []
    row_number = first_data_row - 1
    while row_number >= 1 and len(header_rows) < MAX_HEADER_ROWS:
        texts = _header_texts(grid[row_number])
        if (len(texts) < MIN_DISTINCT_HEADERS
                or len(set(texts)) < MIN_DISTINCT_HEADERS):
            break
        header_rows.insert(0, row_number)
        row_number -= 1
    return (header_rows, first_data_row) if header_rows else None


def _column_roles(
    grid: list[list[Any]],
    first_data_row: int,
) -> tuple[list[int], list[int]]:
    """Which columns hold labels and which hold money, judged by what is in them.

    A row counter is pulled out of the money side even though it is all digits:
    its values run exactly 1..n, and summing it produces a "total" of 153 that
    means nothing.
    """

    width = max((len(row) for row in grid), default=0)
    data = grid[first_data_row:]
    label_columns: list[int] = []
    numeric_columns: list[int] = []
    for column in range(1, width):
        cells = [row[column] for row in data if column < len(row)]
        figures = [
            _to_decimal(value) for value in cells
            if _cell_text(value) and _has_numeric_cell(value)
        ]
        texts = [
            value for value in cells
            if _cell_text(value) and not _has_numeric_cell(value)
        ]
        if len(figures) > len(texts):
            counter = (len(figures) >= MIN_NUMERIC_ROWS
                       and sorted(figures) == [Decimal(n)
                                               for n in range(1, len(figures) + 1)])
            (label_columns if counter else numeric_columns).append(column)
        elif texts:
            label_columns.append(column)
    return label_columns, numeric_columns


def _table_type(grid: list[list[Any]], header_rows: list[int]) -> str:
    """A name for the layout, for the reader's benefit only.

    The two vocabularies no longer decide whether a sheet is read — they only
    label what was read, and a sheet matching neither is analysed just the same.
    """

    names = {_norm_cell(value) for row in header_rows for value in grid[row]}
    if names & INVENTORY_TEXT_HEADERS and {"so luong", "gia tri"} & names:
        return "inventory_movement"
    if names & LEDGER_TEXT_HEADERS:
        return "counterparty_ledger"
    return "unknown"


def _analyze_table(
    sheet_name: str,
    grid: list[list[Any]],
    header_rows: list[int],
    first_data_row: int,
    top_n: int,
) -> SheetAnalysis:
    headers = _combined_headers(grid, header_rows)
    label_columns, numeric_columns = _column_roles(grid, first_data_row)
    rows = _detail_rows(grid, headers, first_data_row, label_columns, numeric_columns)
    total_row = _find_total_row(grid, first_data_row)

    return SheetAnalysis(
        sheet_name=sheet_name,
        table_type=_table_type(grid, header_rows),
        header_rows=list(header_rows),
        total_row=total_row,
        data_rows=len(rows),
        period=_extract_period(grid),
        columns=_column_summaries(
            grid=grid,
            headers=headers,
            rows=rows,
            numeric_columns=numeric_columns,
            total_row=total_row,
            top_n=top_n,
        ),
    )


def _combined_headers(grid: list[list[Any]], header_rows: list[int]) -> list[str]:
    width = max(len(grid[row]) for row in header_rows)
    headers: list[str] = [""] * width
    for column in range(1, width):
        parts: list[str] = []
        for row in header_rows:
            text = _cell_text(grid[row][column])
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        headers[column] = " / ".join(parts)
    return headers


def _find_total_row(grid: list[list[Any]], start_row: int) -> int | None:
    for row in range(start_row, len(grid)):
        if _is_total_row(grid[row]):
            return row
    return None


def _is_total_row(row: list[Any]) -> bool:
    return any(_norm_cell(value) in TOTAL_LABELS for value in row)


def _detail_rows(
    grid: list[list[Any]],
    headers: list[str],
    first_data_row: int,
    label_columns: list[int],
    numeric_columns: list[int],
) -> list[TableRow]:
    rows: list[TableRow] = []
    for row_number in range(first_data_row, len(grid)):
        row = grid[row_number]
        if _is_empty_row(row) or _is_total_row(row):
            continue
        labels = {
            headers[column] or f"Column {column}": _cell_text(row[column])
            for column in label_columns
            if column < len(row) and _cell_text(row[column])
        }
        if not labels:
            continue
        values: dict[str, Decimal] = {}
        has_numeric_evidence = False
        for column in numeric_columns:
            if column >= len(row):
                values[headers[column] or f"Column {column}"] = Decimal(0)
                continue
            has_numeric_evidence = has_numeric_evidence or _has_numeric_cell(row[column])
            values[headers[column] or f"Column {column}"] = (
                _to_decimal(row[column]) or Decimal(0)
            )
        if has_numeric_evidence:
            rows.append(TableRow(row_number, labels, values))
    return rows


def _column_summaries(
    grid: list[list[Any]],
    headers: list[str],
    rows: list[TableRow],
    numeric_columns: list[int],
    total_row: int | None,
    top_n: int,
) -> list[ColumnSummary]:
    summaries: list[ColumnSummary] = []
    for column in numeric_columns:
        column_name = headers[column] or f"Column {column}"
        values_by_row = [
            (row.values.get(column_name, Decimal(0)), row)
            for row in rows
        ]
        calculated_total = sum((value for value, _ in values_by_row), Decimal(0))
        reported_total = _reported_total(grid, total_row, column)
        delta = (
            calculated_total - reported_total
            if reported_total is not None
            else None
        )
        top_rows = [
            TopRow(row.excel_row, row.labels, value)
            for value, row in sorted(
                ((value, row) for value, row in values_by_row if value != 0),
                key=lambda item: item[0],
                reverse=True,
            )[:top_n]
        ]
        summaries.append(ColumnSummary(
            excel_column=column,
            column_name=column_name,
            calculated_total=calculated_total,
            reported_total=reported_total,
            delta=delta,
            top_rows=top_rows,
        ))
    return summaries


def _reported_total(
    grid: list[list[Any]],
    total_row: int | None,
    column: int,
) -> Decimal | None:
    if total_row is None or total_row >= len(grid) or column >= len(grid[total_row]):
        return None
    value = grid[total_row][column]
    if value is None or _cell_text(value) == "":
        return None
    return _to_decimal(value)


def _extract_period(grid: list[list[Any]]) -> dict[str, str]:
    for row in grid:
        text = " ".join(_cell_text(value) for value in row if value is not None)
        match = re.search(
            r"(\d{1,2})/(\d{1,2})/(\d{4}).*?(\d{1,2})/(\d{1,2})/(\d{4})",
            text,
        )
        if match:
            start_day, start_month, start_year, end_day, end_month, end_year = (
                int(part) for part in match.groups()
            )
            return {
                "from": f"{start_year:04d}-{start_month:02d}-{start_day:02d}",
                "to": f"{end_year:04d}-{end_month:02d}-{end_day:02d}",
            }
    return {"from": "", "to": ""}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return Decimal(0)
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if text in EMPTY_NUMERIC_VALUES:
            return Decimal(0)
        normalized = _normalize_number_text(text)
        if normalized is None:
            return None
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return None
    return None


def _has_numeric_cell(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return _to_decimal(value) is not None


def _normalize_number_text(text: str) -> str | None:
    """A printed figure as a plain decimal string, or None when it is not one.

    Cells that hold real numbers never come through here — openpyxl hands those
    back as int or float. This is the path for a workbook that stores its
    figures as text, which some accounting exports do, and there the same amount
    arrives in three spellings depending on the format it was saved under:

        1.210.969.205       dots group thousands
        1.210.969.204,91    dots group, comma is the decimal point
        1210969204.91       no grouping, dot is the decimal point

    Whichever mark comes LAST and is followed by one or two digits is the
    decimal point; every other mark groups thousands. The previous version
    stripped commas and left dots alone, so "6.000" read as six — a
    thousand-fold error that the module's own calculated-vs-reported check could
    not catch, because the printed total row was misread in exactly the same way
    and the two agreed.

    Grouping is then verified: every group after the first must be exactly three
    digits. Without that, the customer code "101.1.351.00011" parses as a number
    and a column of codes turns into a column of money.
    """

    text = re.sub(r"\s+", "", text).rstrip("đ₫")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if not re.fullmatch(r"[\d.,]+", text) or not any(c.isdigit() for c in text):
        return None

    whole, fraction = text, ""
    marks = [match.start() for match in re.finditer(r"[.,]", text)]
    if marks:
        tail = text[marks[-1] + 1:]
        if len(tail) in (1, 2) and tail.isdigit():
            whole, fraction = text[:marks[-1]], tail

    groups = re.split(r"[.,]", whole)
    if len(groups) > 1 and not all(len(group) == 3 for group in groups[1:]):
        return None
    digits = "".join(groups)
    if not digits:
        return None
    return ("-" if negative else "") + digits + (f".{fraction}" if fraction else "")


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _norm_cell(value: Any) -> str:
    return normalize_text(_cell_text(value))


def _is_empty_row(row: list[Any]) -> bool:
    return not any(_cell_text(value) for value in row)


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze totals and top rows in ledger-style XLSX workbooks.",
    )
    parser.add_argument("excel_path", help="Path to the .xlsx workbook")
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()

    analyses = analyze_ledger_workbook(args.excel_path, top_n=args.top_n)
    print(analyses_to_json(analyses))


if __name__ == "__main__":
    _main()
