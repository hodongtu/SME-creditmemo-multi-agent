"""Detail-ledger spreadsheets (sổ chi tiết) into one JSON keyed by account.

Customers submit these as one workbook of many sheets, or as many files of one
sheet each — the same six accounts either way. The output is the same shape in
both cases: one record for the whole folder, keyed by account code, so a report
asking for "phải thu khách hàng" looks in one place regardless of how the files
arrived. A key gains its period — "131@2024" — only when the folder holds more
than one period for that account, because balances from different periods are
two facts and adding them produces a third that is not one.

Unlike the other five passes, the input is not a scan. ``openpyxl`` hands back
exact typed cells, so there is nothing for a model to read off a page — asking
one to retype a hundred billion-đồng figures would only add a way to be wrong.
And the account a sheet holds is usually stated too, in the sheet name or the
file name. So the split is:

* **code** opens the workbook, flattens multi-tier headers, finds the totals
  row, sums each column, and reads every cell;
* **the LLM** is asked, in ONE call for the whole folder, what each sheet *is*:
  the account, the reporting period, and which column header is which field.
  It used to be asked only about sheets the keyword rules could not place, which
  on the sample workbook is none of them — but those rules place that workbook
  and stop short of the general case, so every sheet goes now.

``merge_accounts`` reads only label fields out of the model's answer. Any figure
it emits is discarded, so a hallucinated number cannot reach the report.
"""

import json
import re
from typing import Any

import openpyxl

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
)
from src.utils.common import normalize_text

REQUIRED_TOP_LEVEL_KEYS = {"sheets"}

# Characters the rendered block may occupy, across every ledger file together.
# Rows are never dropped from the stored record — this only bounds what reaches
# the prompt, against the agent's 120k total. Measured: the sample workbook's 48
# rows render to ~14k, so a real ledger fits whole and nothing is aggregated.
LEDGER_BLOCK_CHAR_BUDGET = 40_000
# Label of the row standing in for everything the budget left out. It carries
# the summed figures of those rows, so visible rows still add up to the total.
RESIDUAL_LABEL = "Các đối tác còn lại"
# Which canonical fields hold money and which hold a count. Read off the field
# name rather than the value: a magnitude threshold is a guess, and the names
# are the thing the column mapping already established. One "units" value for a
# whole account said "vnd" over an inventory sheet whose vehicle counts —
# 705, 3.059, 2.206, 1.558 — are not đồng at all.
QUANTITY_SUFFIX = "_quantity"
MONEY_SUFFIXES = ("_debit", "_credit", "_movement", "_value")
# A header row needs this many distinct text cells.
MIN_HEADER_CELLS = 4
# Sums are compared against the printed total at this tolerance, in đồng.
TOTAL_TOLERANCE = 1.0

# Account code -> category. The codes are the Vietnamese chart of accounts, and
# the same ones already listed as keywords for so_chi_tiet_khoan_muc_khac in
# src/matrix/document_matrix.yaml.
ACCOUNT_CODES = {
    # Assets
    "111": "cash", "112": "cash", "113": "cash",
    "121": "cash", "128": "cash",
    "131": "receivable",
    "133": "other_receivable", "136": "other_receivable",
    "138": "other_receivable", "141": "other_receivable",
    "242": "other_receivable",
    # 154 sits INSIDE "Hàng tồn kho" on a TT200 balance sheet, alongside
    # 151-158 — it was filed under its own "wip" category, which no section of
    # the report asks for.
    "151": "inventory", "152": "inventory", "153": "inventory",
    "154": "inventory", "155": "inventory", "156": "inventory",
    "157": "inventory", "158": "inventory",
    "211": "fixed_asset", "212": "fixed_asset", "213": "fixed_asset",
    "214": "fixed_asset", "217": "fixed_asset",
    # Construction in progress: the report's asset section (d) is titled "Tài
    # sản cố định, tài sản dở dang dài hạn", so it belongs with the fixed assets.
    "241": "fixed_asset",
    # Liabilities and equity
    "331": "payable",
    "333": "other_payable", "334": "other_payable", "335": "other_payable",
    "336": "other_payable", "337": "other_payable", "338": "other_payable",
    "344": "other_payable", "352": "other_payable",
    # 311 is NOT a TT200 account — it was "Vay ngắn hạn" under the older QĐ15 /
    # QĐ48 charts, and older files still use it. Kept on purpose; deleting it
    # because it is "not in the standard" would silently stop recognising them.
    "311": "borrowing", "341": "borrowing", "343": "borrowing",
    "411": "equity", "421": "equity",
}
# Phrases to fall back on when no code is printed. Matched against
# normalize_text output, which turns "P.TRA KHAC" into "p tra khac".
#
# IN DECLARED ORDER, most specific first — the first match wins. Length was the
# old tiebreak and it ranked the wrong things: "khach hang" (10) beat "phai tra"
# (8), so a payables sheet naming its counterparty was filed as receivables. A
# word naming the ACCOUNT has to outrank a word naming the party, and length
# does not know the difference. Counterparty words sit last for that reason.
CATEGORY_PHRASES: tuple[tuple[str, str], ...] = (
    ("xuat nhap ton", "inventory"),
    ("phai thu khac", "other_receivable"), ("pthu khac", "other_receivable"),
    ("phai tra khac", "other_payable"), ("p tra khac", "other_payable"),
    ("ptra khac", "other_payable"),
    ("nguoi ban", "payable"),
    ("phai tra", "payable"), ("phai thu", "receivable"),
    ("ton kho", "inventory"), ("nxt", "inventory"), ("vay", "borrowing"),
    # Names the counterparty, not the account. Only reached when nothing above
    # matched, so "phai tra khach hang" resolves as a payable rather than here.
    ("khach hang", "receivable"),
)
# The code to file a category under when the file prints none. Conventional, not
# read — every use is stamped code_source="convention" so the two never look
# alike in the output.
CONVENTIONAL_CODE = {
    "cash": "112", "receivable": "131", "other_receivable": "138",
    "inventory": "156", "fixed_asset": "211",
    "payable": "331", "other_payable": "338", "borrowing": "341",
    "equity": "411",
}
# Vietnamese column headers -> canonical field names, so every key in the output
# is English. Looked up through normalize_text, so case and spacing do not
# matter. A header not listed here keeps its original name and is reported in
# "unmapped_columns": a Vietnamese key leaking out is better than inventing an
# English field name for a column nobody has understood yet.
COLUMN_FIELDS = {
    "du no dau ky": "opening_debit", "du co dau ky": "opening_credit",
    "phat sinh no": "debit_movement", "phat sinh co": "credit_movement",
    "du no cuoi ky": "closing_debit", "du co cuoi ky": "closing_credit",
    "du dau so luong": "opening_quantity", "du dau gia tri": "opening_value",
    "nhap vao so luong": "inflow_quantity", "nhap vao gia tri": "inflow_value",
    "xuat ra so luong": "outflow_quantity", "xuat ra gia tri": "outflow_value",
    "du cuoi so luong": "closing_quantity", "du cuoi gia tri": "closing_value",
    "ma khach hang": "counterparty_code", "ten khach hang": "counterparty_name",
    "ten don vi": "booking_unit", "loai xe": "item_name",
}

LEDGER_EXTRACTION_SYSTEM_PROMPT = """
You name the account behind a sheet of a Vietnamese accounting detail ledger
(sổ chi tiết / bảng cân đối phát sinh công nợ), for SME credit underwriting.

You are asked about EVERY sheet. A keyword pass has its own answer for each of
them and will be used wherever yours does not check out, so answer only what you
can read — "unknown" and "" are proper answers.

You are NOT the source of any figure. The program reads every cell of the
workbook itself, exactly. Producing a number here changes nothing; it is
discarded. You get a few sample rows so you can tell a balance column from a
movement column, not so you can read them back.

For each sheet you get the FILE NAME it came from, the sheet name, the report
title, the BANNER lines printed above the table, the column headers, the row
count, and up to three sample rows.

THE FILE NAME IS OFTEN THE STRONGEST SIGNAL. Customers export one account per
file, leaving the sheet called "Sheet1" while the file is called
"SO CHI TIET TK 131.xlsx". Read the file name first.

"category" must be exactly one of:
  cash             - cash and equivalents (TK 111, 112, 113, 121, 128)
  receivable       - trade receivables (TK 131, "phải thu khách hàng")
  other_receivable - other receivables and prepayments (TK 133, 136, 138, 141, 242)
  inventory        - inventory, including work in progress (TK 151-158)
  fixed_asset      - fixed assets and construction in progress (TK 211-217, 241)
  payable          - trade payables (TK 331, "phải trả người bán")
  other_payable    - other payables, tax, payroll, accruals (TK 333-338, 344, 352)
  borrowing        - borrowings and bonds (TK 311, 341, 343, "vay")
  equity           - owners' equity (TK 411, 421)
  unknown          - anything you cannot place with confidence

Prefer "unknown" over a guess. A wrongly named account is worse than an unnamed
one: the analyst can read an unnamed sheet, but will trust a wrong name.

"account_code" is the account number when the sheet or file states one, else "".
Never infer a number that is not written down — the program fills those in
itself and marks them as conventional.

"period" is the reporting period, read from the banner. Give ISO dates in "from"
and "to", and the banner line you read them from in "as_printed". Vietnamese
ledgers write this many ways — "Từ ngày 01/01/2025 đến ngày 31/12/2025",
"Kỳ báo cáo: 01/01/2025 - 31/12/2025", "Năm 2024", "Quý 4/2024", "Tháng 12 năm
2024". Expand a bare year or quarter to its first and last day. When no banner
line states a period, use "" for all three rather than guessing from a file name
that merely contains a year.

"columns" maps each column header EXACTLY AS GIVEN to one of these field names:
  counterparty_code counterparty_name item_name booking_unit
  opening_debit opening_credit debit_movement credit_movement
  closing_debit closing_credit
  opening_quantity opening_value inflow_quantity inflow_value
  outflow_quantity outflow_value closing_quantity closing_value
Use the *_quantity / *_value pair for stock sheets (nhập xuất tồn) and the
debit/credit set for payable and receivable sheets. Omit a header you cannot
place — do not invent a field name, and do not map two headers to the same
field. A row counter ("Stt", "TT") is not a field; omit it.

Return EXACTLY this JSON schema and no other text:
{{
  "sheets": [
    {{"file": "the file name exactly as given",
      "sheet": "the sheet name exactly as given",
      "category": "one of the values above",
      "account_code": "the printed code, or ''",
      "period": {{"from": "YYYY-MM-DD or ''", "to": "YYYY-MM-DD or ''",
                 "as_printed": "the banner line, or ''"}},
      "columns": {{"header exactly as given": "field name"}},
      "description": "one line in Vietnamese saying what the sheet holds"}}
  ],
  "extraction_notes": ["sheets you could not place, and why"]
}}
"""

# ── Reading the workbook — openpyxl only, no model ────────────────────────

def _value_grid(worksheet: Any) -> list[list[Any]]:
    """The sheet's cells, with merged ranges filled across every cell they span.

    A merged range reports its value only in the top-left cell and ``None``
    everywhere else, which is what collapses a two-tier header ("Dư đầu" over
    "Số lượng"/"Giá trị") into one column instead of two.
    """

    grid = [list(row) for row in worksheet.iter_rows(values_only=True)]
    for merged in worksheet.merged_cells.ranges:
        top, left = merged.min_row - 1, merged.min_col - 1
        if top >= len(grid) or left >= len(grid[top]):
            continue
        value = grid[top][left]
        for r in range(merged.min_row - 1, min(merged.max_row, len(grid))):
            for c in range(merged.min_col - 1, min(merged.max_col, len(grid[r]))):
                grid[r][c] = value
    return grid


def _text_cells(row: list[Any]) -> int:
    """Count of DISTINCT text cells.

    Distinct because _value_grid fills merged ranges: the company-name banner
    these exports put in A1:I1 becomes nine identical text cells and would
    otherwise out-score the real header row.
    """

    return len({cell.strip() for cell in row if isinstance(cell, str) and cell.strip()})


def _numeric_cells(row: list[Any]) -> int:
    return sum(
        1 for cell in row
        if isinstance(cell, (int, float)) and not isinstance(cell, bool)
    )


def _find_header(grid: list[list[Any]]) -> tuple[int, int] | None:
    """(first, last) row index of the header, or None when there is no table.

    Deliberately not tied to a column set or a row number: these exports put a
    different number of banner rows above the table for every customer, and the
    columns are whatever their accounting software prints.
    """

    for index, row in enumerate(grid):
        if _text_cells(row) < MIN_HEADER_CELLS or _numeric_cells(row):
            continue
        rest = grid[index + 1:]
        # A second header tier is text-only too; the data starts at the first
        # row carrying numbers.
        for offset, below in enumerate(rest[:2]):
            if _numeric_cells(below):
                return index, index + offset
        if any(_numeric_cells(row) for row in rest[:6]):
            return index, index
    return None


def _headers(grid: list[list[Any]], first: int, last: int) -> list[str]:
    """Column names, joining a two-tier header into one name per column."""

    def text(row: list[Any], column: int) -> str:
        cell = row[column] if column < len(row) else None
        return str(cell).strip() if cell is not None else ""

    width = max(len(row) for row in grid[first:last + 1])
    names = []
    for column in range(width):
        parts = [text(grid[row], column) for row in range(first, last + 1)]
        unique = list(dict.fromkeys(part for part in parts if part))
        names.append(" - ".join(unique))
    return names


def _is_total(row: dict[str, Any]) -> bool:
    return any(
        isinstance(value, str) and value.strip().lower().startswith(("tổng", "tong"))
        for value in row.values()
    )


def _profile_sheet(worksheet: Any) -> dict[str, Any]:
    """One sheet as headers, printed total, computed totals and its top rows."""

    grid = _value_grid(worksheet)
    span = _find_header(grid)
    # Above the table only. Reading a fixed twelve rows swept the column headers
    # into the banner, where they said nothing the "column_headers" list does not already
    # say and cost more of the labelling payload than the banner itself.
    banner = [
        str(cell).strip()
        for row in grid[: span[0] if span else 12] for cell in row
        if isinstance(cell, str) and cell.strip()
    ]
    profile: dict[str, Any] = {
        "sheet": worksheet.title,
        "title": next((t for t in banner if "BÁO CÁO" in t.upper()), ""),
        "period_line": next((t for t in banner if t.lower().startswith("từ ngày")), ""),
        # The banner lines the two fields above were derived from, kept rather
        # than discarded. "period_line" only recognises one phrasing — measured, 2 of 10
        # common ones — and parsing that line is exactly what the labelling call
        # is asked to do. It cannot do it from a field that came back empty.
        # De-duplicated in order: a merged banner cell repeats its text across
        # every column it spans, so the raw list was the company name six times
        # before anything else. Bounded because a banner is a heading, not a
        # page.
        "banner": list(dict.fromkeys(banner))[:12],
    }

    if span is None:
        # Kept rather than dropped: the LLM may still recognise the sheet, and a
        # sheet we cannot parse is information the reader should have.
        profile["error"] = "Không tìm được dòng tiêu đề bảng"
        profile["first_rows"] = [
            [str(c) for c in row if c is not None][:6] for row in grid[:5]
        ]
        return profile

    first, last = span
    headers = _headers(grid, first, last)
    records = []
    for row in grid[last + 1:]:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        records.append({
            name: (row[i] if i < len(row) else None)
            for i, name in enumerate(headers) if name
        })

    printed_total = next((r for r in records if _is_total(r)), None)
    details = [r for r in records if r is not printed_total]

    numeric = [
        name for name in headers
        if name and any(
            isinstance(r.get(name), (int, float)) and not isinstance(r.get(name), bool)
            for r in details
        )
    ]
    computed = {
        name: round(sum(
            r[name] for r in details
            if isinstance(r.get(name), (int, float)) and not isinstance(r[name], bool)
        ), 2)
        for name in numeric
    }

    profile.update({
        "column_headers": [name for name in headers if name],
        "row_count": len(details),
        "computed_total": computed,
        "printed_total": (
            {k: v for k, v in printed_total.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if printed_total else None
        ),
        "_rows": details,
    })
    return profile


def _reconcile(profile: dict[str, Any]) -> list[str]:
    """Disagreements between what we summed and what the file printed."""

    printed = profile.get("printed_total")
    if not printed:
        return []
    notes = []
    for column, value in printed.items():
        ours = profile.get("computed_total", {}).get(column)
        if ours is None or abs(ours - value) <= TOTAL_TOLERANCE:
            continue
        notes.append(
            f"{profile['sheet']} / {column}: cộng các dòng ra {ours:,.0f} nhưng "
            f"dòng tổng in {value:,.0f} (lệch {ours - value:,.0f})"
        )
    return notes


def profile_workbook(path: str) -> dict[str, Any]:
    """Every sheet's profile, plus any total that does not reconcile."""

    workbook = openpyxl.load_workbook(path, data_only=True)
    try:
        sheets = [_profile_sheet(sheet) for sheet in workbook.worksheets]
    finally:
        workbook.close()
    return {
        "sheets": sheets,
        "reconcile_notes": [note for sheet in sheets for note in _reconcile(sheet)],
    }

# ── Naming a sheet by rule — the floor the model answer falls back to ─────

def _counter_columns(rows: list[dict], columns: list[str]) -> set[str]:
    """Columns that are just a row counter (1, 2, 3, …).

    Matched by the sequence rather than by the header, because "Stt" is only
    this exporter's name for it — the next customer's file may say "STT", "TT"
    or "No.". A counter adds a column of noise to every table it reaches.
    """

    counters = set()
    for column in columns:
        values = [r.get(column) for r in rows]
        if all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            and v == index
            for index, v in enumerate(values, 1)
        ) and len(values) > 1:
            counters.add(column)
    return counters


def _label_column(
    rows: list[dict[str, Any]],
    columns: list[str],
    counters: set[str],
) -> str:
    """The column naming what each row is about.

    Structural rather than lexical: a ledger puts its key columns first and its
    bookkeeping metadata last, so the subject is the LAST text column before the
    figures begin. Two simpler rules were tried against the real workbook and
    both picked the wrong column — first text column gave the customer CODE
    ("NH14"), and longest text gave "Tên đơn vị", the branch that booked the
    entry, because one branch name repeated outweighs three counterparty names.

    Row counters are excluded first or "Stt" would be the first figure and leave
    no text column before it. A value of one character is not a name — these
    exports write "-" for an empty cell, which would otherwise make a money
    column look like text.
    """

    ordered = [c for c in columns if c not in counters]
    has_number = {
        c for c in ordered
        if any(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
               for r in rows)
    }
    first_figure = next(
        (i for i, c in enumerate(ordered) if c in has_number), len(ordered)
    )
    named = [
        c for c in ordered[:first_figure]
        if any(isinstance(r.get(c), str) and len(r[c].strip()) > 1 for r in rows)
    ]
    return named[-1] if named else ""


def label_sheet(
    filename: str,
    sheet_name: str,
    title: str,
) -> tuple[str, str, str, str]:
    """Name the account a sheet holds: (category, code, code_source, evidence).

    Reuses ``normalize_text`` — the same normaliser document classification uses
    — over the file name, the sheet name and the report title. Codes are checked
    before phrases because a printed number settles the question and a phrase
    only suggests it.

    ``code_source`` separates a code read off the file from one filled in by
    convention. The sample workbook prints a code on two of its six sheets, so
    four of six accounts are keyed by a number that is nowhere in the customer's
    file; that has to be visible rather than implied.
    """

    for source, text in (
        ("file name", filename), ("sheet name", sheet_name), ("report title", title),
    ):
        for run in re.findall(r"\d+", normalize_text(text)):
            # Three to five digits: a level-1 account plus up to two sub-levels.
            # Real ledgers name the sub-account ("TK 1311", "so chi tiet 1561"),
            # and matching only exact three-digit runs missed all of them. The
            # length ceiling is what keeps it from firing on a tax code, and the
            # floor plus the lookup keeps it off a year — 2025 gives 202, which
            # is not an account. A run that matches nothing is skipped, so
            # "BCTC 2025 - TK 131" still resolves to 131.
            if 3 <= len(run) <= 5 and run[:3] in ACCOUNT_CODES:
                return (ACCOUNT_CODES[run[:3]], run[:3], "printed",
                        f"{source} {text!r}")

    joined = normalize_text(" ".join([filename, sheet_name, title]))
    # On word boundaries, not as a bare substring: "phai thu khac" is a prefix
    # of "phai thu khach hang", so plain containment filed a customer-receivable
    # sheet under other-receivables. normalize_text leaves only [a-z0-9 ], so
    # the two lookarounds are the whole of word boundary here.
    for phrase, category in CATEGORY_PHRASES:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", joined):
            return (category, CONVENTIONAL_CODE[category], "convention",
                    f"phrase {phrase!r}, no code printed")
    return "unknown", "", "none", "no account code or keyword found"


def canonical_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """(original header -> field name, headers with no field name).

    Unmapped headers map to themselves so no column is ever lost; they are
    returned separately so the gap shows up in the record instead of hiding as a
    stray Vietnamese key.
    """

    mapping, unmapped = {}, []
    for header in headers:
        field = COLUMN_FIELDS.get(normalize_text(header))
        if field is None:
            unmapped.append(header)
        mapping[header] = field or header
    return mapping, unmapped

# ── Turning a sheet into rows and units ───────────────────────────────────

def _sheet_rows(sheet: dict[str, Any], fields: dict[str, str]) -> list[dict[str, Any]]:
    """Every detail row of a sheet, under canonical field names.

    Nothing is ranked or dropped here. Selecting the "top" rows by closing
    balance — which this used to do — threw away live credit relationships: a
    bank that turned over 23 tỷ in both directions and settled to a zero closing
    balance scored zero and was cut, while that turnover is exactly what an
    underwriter is looking for. What has to be left out for space is decided in
    ``fit_to_budget``, by magnitude across all columns, and aggregated.
    """

    rows = sheet.get("_rows") or []
    columns = sheet.get("column_headers", [])
    counters = _counter_columns(rows, columns)
    label_column = _label_column(rows, columns, counters)
    # A column counts as numeric when any row puts a number in it; the rest of
    # that column's cells are dashes or blanks, i.e. zeros.
    numeric_columns = [
        c for c in columns
        if c not in counters and c != label_column
        and any(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
                for r in rows)
    ]
    out = []
    for row in rows:
        entry = {"name": row.get(label_column) if label_column else None}
        # Every non-zero figure, not just the closing balance. Carrying one
        # figure per row was measurably worse than carrying none: the report
        # template asks for opening, movements and closing, and given only the
        # closing figure the model put it in the opening column and INVENTED
        # the rest — inventory read 640,02 / 3.500 / 2.500 against a real
        # 205,67 / 1.590,66 / 1.156,31.
        # Zero is a figure, not an absence: a counterparty that opened at zero
        # and closed at zero still says something, and a key that vanishes when
        # the value is 0 makes the reader guess whether the column was empty or
        # the number really was nothing. Every numeric column of the sheet gets
        # a key on every row.
        #
        # These exports write an empty cell as "-", so a row can carry a dash
        # where its neighbours carry figures. That is the sheet saying zero, and
        # it is recorded as 0 rather than dropped.
        # Rounded to whole units: these sheets keep đồng and item counts, both
        # integers, and a trailing ".0" on every figure is noise the reader has
        # to look past.
        entry.update({
            fields.get(k, k): (
                round(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
            )
            for k in numeric_columns
            for v in [row.get(k)]
        })
        out.append(entry)
    return out


def _field_units(rows: list[dict[str, Any]]) -> dict[str, str]:
    """The unit of every numeric field present, by its canonical name.

    Fields outside the column map keep their Vietnamese header and get no unit:
    an unrecognised column could be money or a count, and saying the wrong one
    is worse by a factor of a billion than saying nothing.
    """

    units = {}
    for field in {k for row in rows for k in row if k != "name"}:
        if field.endswith(QUANTITY_SUFFIX):
            units[field] = "quantity"
        elif field.endswith(MONEY_SUFFIXES):
            units[field] = "vnd"
        else:
            units[field] = "unknown"
    return dict(sorted(units.items()))


# Every canonical field name a column may be mapped to. Built from the rule
# table so the two can never list different things.
CANONICAL_FIELDS = frozenset(COLUMN_FIELDS.values())
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ── Checking the model's answer before any of it is used ──────────────────

def _checked_account(
    said: dict[str, Any], sheet_text: str = ""
) -> tuple[str, str, str] | None:
    """(category, code, evidence) from a model answer, or None to use the rule.

    A wrongly named account is worse than an unnamed one — the analyst can read
    an unnamed sheet but will trust a wrong name — so both halves have to hold
    up: the category must be one this system defines, and a code, if given, must
    be a real account number rather than a year or a customer id.

    A valid code is not enough: it also has to be IN the sheet. The prompt lists
    example codes per category ("borrowing - ... TK 311, 341, 343"), and on a
    sheet called "TK VAY" that prints no number at all the model answered 311 —
    a real account code, copied out of the prompt rather than read off the file.
    It passed every check there was. "Never infer a number that is not written
    down" is in the prompt too, and this is the third time in this project that
    a rule living only there was not followed.

    A code the model produced but the sheet does not contain is dropped, and the
    category falls through to its conventional code, which is exactly what
    code_source="convention" was built to say.
    """

    category = str(said.get("category") or "")
    if category not in CONVENTIONAL_CODE:
        return None
    printed = re.sub(r"\D", "", str(said.get("account_code") or ""))
    if printed and (len(printed) < 3 or len(printed) > 5
                    or printed[:3] not in ACCOUNT_CODES
                    or (sheet_text and printed not in re.sub(r"\D", " ", sheet_text))):
        printed = ""
    code = printed or CONVENTIONAL_CODE[category]
    said_what = str(said.get("description") or category)
    return category, code, f"LLM: {said_what}"


def _checked_period(said: dict[str, Any], fallback: str) -> dict[str, Any]:
    """The period as ISO dates when the model gave usable ones, else the banner.

    Kept as a dict rather than a bare string because the string is what the code
    already had and could do nothing with: two files cannot be compared, so a
    2024 ledger and a 2025 one merge into one account and their balances are
    summed. Dates make that answerable. Nothing here acts on it yet.
    """

    period = said.get("period")
    raw = fallback
    if isinstance(period, dict):
        raw = str(period.get("as_printed") or "") or fallback
        tu, den = str(period.get("from") or ""), str(period.get("to") or "")
        if _ISO_DATE.match(tu) and _ISO_DATE.match(den) and tu <= den:
            return {"from": tu, "to": den, "as_printed": raw, "source": "llm"}
    return {"from": "", "to": "", "as_printed": raw,
            "source": "banner" if raw else "none"}


def _checked_columns(
    said: dict[str, Any], headers: list[str]
) -> tuple[dict[str, str], list[str]]:
    """(header -> field, headers the model did not place) for one sheet.

    Per header, not per sheet: a model that names three columns correctly and
    invents a fourth keeps the three. The invented one goes to the rule table,
    and if that has no name for it either the header keeps its own — the
    behaviour canonical_columns already had.

    The check that matters is that a field name exists. Everything downstream
    reads "opening_debit", "closing_credit" and the rest BY NAME, so a plausible
    invention like "so_du_dau" would not raise anywhere; it would quietly empty
    a column of the report.
    """

    mapping = said.get("columns")
    if not isinstance(mapping, dict):
        return {}, list(headers)
    taken: set[str] = set()
    placed: dict[str, str] = {}
    for header in headers:
        field = mapping.get(header)
        # Two headers on one field would silently drop one of them.
        if isinstance(field, str) and field in CANONICAL_FIELDS and field not in taken:
            placed[header] = field
            taken.add(field)
    return placed, [h for h in headers if h not in placed]

# ── Merging into one entry per account and period ─────────────────────────

def _period_key(period: dict[str, Any]) -> str:
    """What makes two entries the same reporting period, or "" when unknown."""

    frm, to = period.get("from") or "", period.get("to") or ""
    if frm and to:
        return f"{frm}..{to}"
    return normalize_text(period.get("as_printed") or "")


def _period_label(period_key: str) -> str:
    """The period as it goes into a display key.

    A whole calendar year gets written as the year, because "131@2024" is what
    an analyst would write and "131@2024-01-01..2024-12-31" is the same fact
    spelled at four times the length. Anything else keeps its span — a ledger
    covering July to June is not a year and should not read as one.
    """

    if not period_key:
        return "unknown-period"
    frm, _, to = period_key.partition("..")
    if _ISO_DATE.match(frm) and _ISO_DATE.match(to):
        year = frm[:4]
        if to[:4] == year and frm[5:] == "01-01" and to[5:] == "12-31":
            return year
        return period_key
    return period_key


def _display_keys(grouped: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    """Public account keys: bare code when unambiguous, code@period when not.

    A dossier holding one period per account — the ordinary case — keeps the
    keys it has always had. The period only appears where the alternative is two
    entries fighting over one name, which is the situation this whole split
    exists to stop: summing a 2024 closing balance with a 2025 one produced a
    figure that never existed in either file.
    """

    periods_per_code: dict[str, set[str]] = {}
    for code, period_key in grouped:
        periods_per_code.setdefault(code, set()).add(period_key)
    out: dict[str, Any] = {}
    for (code, period_key), entry in grouped.items():
        key = code if len(periods_per_code[code]) == 1 else (
            f"{code}@{_period_label(period_key)}"
        )
        out[key] = entry
    return out


def merge_accounts(
    profiles: list[tuple[str, dict[str, Any]]],
    labels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold every sheet of every file into one record keyed by account code.

    Figures are converted to tỷ VNĐ here rather than left in đồng: the agent's
    tables are headed "(Đơn vị: tỷ VNĐ)", and asking it to divide by a billion is
    asking it to do arithmetic on evidence. It got that wrong by a factor of a
    thousand on a real run — 225,51 tỷ of receivables reported as 225.510,14 —
    so the conversion happens in code, as the metrics block already does.

    Only ``category``, ``account_code`` and ``description`` are read out of
    ``labels``. Everything numeric comes from the workbook.
    """

    from_model = {
        (str(item.get("file", "")), str(item.get("sheet", ""))): item
        for item in ((labels or {}).get("sheets") or [])
        if isinstance(item, dict)
    }
    # Keyed by (account code, period) while merging. Two files covering the same
    # account but different years are two different things: their movements
    # could arguably be added, their balances cannot — a closing balance plus
    # another closing balance is a number neither file contains.
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    unmapped: list[str] = []

    for filename, profile in profiles:
        for sheet in profile.get("sheets", []):
            name = sheet.get("sheet", "")
            said = from_model.get((filename, name), {})
            # The model answers first and the rules catch what it drops, which
            # is the reverse of how this used to run. The rules place the sample
            # workbook and stop short of the general case; the model is asked
            # about every sheet precisely because those rules do not carry.
            # Everything the sheet says about itself, for the code check above.
            sheet_text = " ".join([
                filename, name, sheet.get("title", ""),
                *(sheet.get("banner") or []),
            ])
            checked = _checked_account(said, sheet_text)
            if checked is not None:
                category, code, evidence = checked
                code_source = "llm"
            else:
                category, code, code_source, evidence = label_sheet(
                    filename, name, sheet.get("title", "")
                )

            key = code or f"unknown_{name}"
            # Row counters are dropped before mapping: "Stt" is not a field the
            # report ever wants, and listing it as unmapped would send someone
            # looking for an English name it should never have.
            counters = _counter_columns(sheet.get("_rows") or [], sheet.get("column_headers", []))
            headers = [c for c in sheet.get("column_headers", []) if c not in counters]
            # Same shape for columns: take what the model placed, send the rest
            # to the rule table. Per header, so one invented field name costs
            # one column rather than the sheet.
            fields, left = _checked_columns(said, headers)
            by_rule, missing = canonical_columns(left)
            fields.update(by_rule)
            unmapped += [c for c in missing if c not in unmapped]
            rows = _sheet_rows(sheet, fields)
            totals_raw = {
                fields.get(k, k): round(v) if isinstance(v, (int, float))
                and not isinstance(v, bool) else v
                for k, v in (sheet.get("printed_total")
                             or sheet.get("computed_total") or {}).items()
                # Counters dropped here as well as from the rows. Summing "Stt"
                # gives 153 for a 17-row sheet, which reads as a figure and is
                # not one; it only stayed hidden while the printed total row
                # happened to leave that cell empty. Zeros are kept, as in the rows.
                if k not in counters
            }
            # Values stay exactly as the sheet holds them. The conversion to
            # tỷ VNĐ that used to happen here is left to convert_amounts_in_text
            # in _finalize, which does it on the finished report rather than on
            # the evidence. Which file a row came from is on the account as
            # "source_files"; repeating it per row said nothing extra.
            entry = {
                "category": category,
                "code_source": code_source,
                "code_evidence": evidence,
                "source_files": [filename],
                "period": _checked_period(said, sheet.get("period_line", "")),
                "source_columns": {v: k for k, v in fields.items()},
                "units": _field_units(rows),
                "totals": totals_raw,
                "item_count": len(rows),
                "items": rows,
            }
            if "error" in sheet:
                entry["error"] = sheet["error"]

            slot = (key, _period_key(entry["period"]))
            if slot not in grouped:
                grouped[slot] = entry
                continue
            # Same account, same period, another sheet or another file: join
            # rather than overwrite. Sheets of one workbook split an account by
            # month or product group and belong together; two files on one
            # period might instead be duplicates, which the warning below says.
            existing = grouped[slot]
            existing["items"] += entry["items"]
            existing["item_count"] = len(existing["items"])
            if filename not in existing["source_files"]:
                existing["source_files"].append(filename)
            existing["source_columns"].update(entry["source_columns"])
            existing["units"].update(entry["units"])
            for field, value in entry["totals"].items():
                existing["totals"][field] = round(
                    existing["totals"].get(field, 0) + value, 2
                )

    accounts = _display_keys(grouped)

    # Two notes, about two different things.
    #
    # A split is information: the dossier covers more than one period for this
    # account, so it is two entries rather than one. It used to be a warning
    # saying the balances had been added together, which is what happened before
    # the split and would be a lie now.
    #
    # Two files on ONE period is the case still worth flagging: they may be
    # continuing each other, or they may be the same data twice, and only the
    # reader can tell. Emitted once per account after the merge — it used to
    # fire inside the loop, so six sheets of one workbook produced five warnings
    # saying "merged from 2, 3, 4, 5, 6 files" about a single file.
    by_code: dict[str, list[str]] = {}
    for key in accounts:
        by_code.setdefault(key.split("@")[0], []).append(key)
    warnings += [
        f"Tài khoản {code} được TÁCH thành {len(keys)} mục theo kỳ báo cáo "
        f"({', '.join(sorted(keys))}): số dư của các kỳ khác nhau không được "
        f"cộng vào nhau."
        for code, keys in by_code.items() if len(keys) > 1
    ]
    warnings += [
        f"Tài khoản {key} gộp từ {len(account['source_files'])} file "
        f"({', '.join(account['source_files'])}) trong CÙNG một kỳ: tổng đã "
        f"được cộng lại. Nếu các file trùng dữ liệu thay vì bổ sung nhau thì "
        f"tổng bị cộng đôi."
        for key, account in accounts.items()
        if len(account["source_files"]) > 1
    ]
    warnings += [note for _, p in profiles for note in (p.get("reconcile_notes") or [])]
    return {
        "accounts": accounts,
        "unmapped_columns": unmapped,
        "warnings": warnings,
        "extraction_notes": [str(n) for n in ((labels or {}).get("extraction_notes") or [])],
    }

# ── Fitting the record to the prompt's character budget ───────────────────

def _magnitude(row: dict[str, Any]) -> float:
    """How big this row is, across every column rather than one of them."""

    return max(
        (abs(v) for k, v in row.items()
         if k != "name"
         and isinstance(v, (int, float)) and not isinstance(v, bool)),
        default=0.0,
    )


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One row summing every field of the rows it stands in for."""

    totals: dict[str, float] = {}
    for row in rows:
        for key, value in row.items():
            if (key != "name"
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                totals[key] = totals.get(key, 0) + value
    return {"name": f"{RESIDUAL_LABEL} ({len(rows)})",
            **{k: round(v) for k, v in totals.items()}}


def fit_to_budget(
    record: dict[str, Any],
    budget: int = LEDGER_BLOCK_CHAR_BUDGET,
) -> dict[str, Any]:
    """The record trimmed to ``budget`` characters, losing no figures.

    Rows that do not fit are replaced by one aggregate row carrying their summed
    columns, so visible rows still reconcile to ``totals`` — the same principle
    as naming skipped files in ``discover_documents`` rather than dropping them.

    Budgets the whole record at once. Doing it per file gave every ledger file
    the full allowance, so six files could claim 240k against a 120k prompt and
    push the document content off the end.
    """

    def shrink(keep: int) -> dict[str, Any]:
        accounts = {}
        for key, account in (record.get("accounts") or {}).items():
            rows = account.get("items") or []
            if len(rows) <= keep:
                accounts[key] = account
                continue
            ordered = sorted(rows, key=_magnitude, reverse=True)
            accounts[key] = {**account,
                             "items": ordered[:keep] + [_aggregate(ordered[keep:])]}
        return {**record, "accounts": accounts}

    def size(candidate: dict[str, Any]) -> int:
        return len(json.dumps(candidate, ensure_ascii=False, indent=2))

    if size(record) <= budget:
        return record
    widest = max((len(a.get("items") or [])
                  for a in (record.get("accounts") or {}).values()), default=0)
    # Monotone in `keep`, so a binary search costs ~log2(rows) renders.
    low, high = 0, widest
    while low < high:
        mid = (low + high + 1) // 2
        if size(shrink(mid)) <= budget:
            low = mid
        else:
            high = mid - 1
    return shrink(low)

# ── The pass itself: profile every file, one labelling call, merge ────────

def build_ledger_extraction_chain(llm: Any):
    """Build the JSON-output labelling chain for detail-ledger workbooks."""

    return build_extraction_chain(LEDGER_EXTRACTION_SYSTEM_PROMPT, llm)


# How many detail rows go with each sheet in the labelling payload. Enough to
# tell a balance column from a movement column when the header alone is
# ambiguous — a "Số dư" that never changes across rows reads differently from a
# "Phát sinh" that does. Not enough to be worth transcribing: the figures in the
# record come from openpyxl, and these rows are there so the model can NAME the
# columns, not so it can read them back.
SAMPLE_ROWS_FOR_LABELLING = 3


def _labelling_payload(
    profiles: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Every sheet, as the labelling call sees it.

    Every sheet, not only the ones the keyword pass could not place. The rules
    handle the sample workbook and stop short of the general case: the account
    falls back to convention on 4 of 6 sheets, the period line is recognised in
    2 of 10 common phrasings, and the column names are 18 hard-coded strings
    that another accounting package will not match. One call per run buys all
    three, and the figures never leave openpyxl.

    ``banner`` goes in raw. The parsed ``ky`` beside it is exactly the field
    that comes back empty when the phrasing is unfamiliar, so sending only that
    would ask the model to fix a problem with the evidence removed.
    """

    return [
        {
            "file": filename,
            "sheet": sheet.get("sheet", ""),
            "title": sheet.get("title", ""),
            "banner": sheet.get("banner", []),
            "column_headers": sheet.get("column_headers", []),
            "row_count": sheet.get("row_count"),
            "sample_rows": (sheet.get("_rows") or [])[:SAMPLE_ROWS_FOR_LABELLING],
        }
        for filename, profile in profiles
        for sheet in profile.get("sheets", [])
    ]


def extract_ledger_batch(
    chain: Any,
    documents: list[tuple[str, str, str]],
) -> list[tuple[dict[str, Any] | None, str]]:
    """Read every ledger file, then return one shared record for all of them.

    Batch rather than per-document because the record spans files: the same
    account can arrive split across two of them, and one labelling call over the
    whole set also keeps the naming consistent. Each document still receives the
    record, so the pointer that replaces its raw grid stays correct.
    """

    profiles: list[tuple[str, dict[str, Any]]] = []
    failures: list[str] = []
    for filename, _content, path in documents:
        try:
            profiles.append((filename, profile_workbook(path or filename)))
        except Exception as exc:
            # Anything not a readable .xlsx — a .csv, a legacy .xls, a corrupt
            # file. The other files still go through, and the runner leaves this
            # one holding its raw text so no evidence is dropped.
            failures.append(f"{filename}: {type(exc).__name__}: {exc}"[:300])

    if not profiles:
        reason = "; ".join(failures) or "Không có file nào đọc được"
        return [(None, f"Không đọc được workbook: {reason}"[:500]) for _ in documents]

    # One call for every sheet of every ledger file. It used to fire only for
    # sheets the keyword pass could not place, which on the sample workbook is
    # never — and the rules that placed them are the ones that do not carry to
    # other accounting packages.
    labels, error = run_extraction(
        chain,
        ", ".join(name for name, _ in profiles),
        # Compact: indentation is 21% of this payload and the model reads it the
        # same either way — measured on the sample workbook, same six answers.
        json.dumps({"sheets": _labelling_payload(profiles)},
                   ensure_ascii=False, separators=(",", ":")),
        REQUIRED_TOP_LEVEL_KEYS,
        "No ledger extraction LLM configured.",
    )
    if labels is None:
        # The figures are already in hand; losing the labels is not worth losing
        # them over. Everything falls back to the keyword rules, and the record
        # says so through code_source — this is the degraded path, not the
        # normal one, and it has to be legible as such.
        labels = {"sheets": [],
                  "extraction_notes": [f"Không gán nhãn được: {error}"]}

    record = merge_accounts(profiles, labels)
    record["extraction_notes"] += failures
    return [(record, "") for _ in documents]
