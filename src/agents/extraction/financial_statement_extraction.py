"""LLM extraction of full financial statement bundles into structured JSON"""

import re
from typing import Any

from src.utils.reading.tax_xml import parse_tax_xml
from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    resolve_money_multiplier,
    scale_amount,
    run_extraction,
)
from src.utils.common import normalize_text


# Keys the block prints, in the order the schema declares them. A record read
# back from logs/ or a cache can still carry keys the schema has since dropped —
# "notes_summary" was 2,182 tokens per file — and the block would print them
# without anything noticing. Filtering against this list means removing a key
# from the schema is enough; nothing has to be cleaned up downstream.
BLOCK_KEYS = (
    "customer",
    "document_type",
    "reporting_period",
    "audit_opinion",
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "extraction_notes",
)

REQUIRED_TOP_LEVEL_KEYS = {
    "document_type",
    "reporting_period",
    "audit_opinion",
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
}

FINANCIAL_STATEMENT_EXTRACTION_SYSTEM_PROMPT = """
You extract structured data from the raw OCR text of a Vietnamese company's
financial statements, for SME credit underwriting.

Written in English, but every literal below that ends up IN THE JSON stays
Vietnamese on purpose. "Năm YYYY", the source_unit tokens and the audit opinion
wordings are read back by code or printed straight into a Vietnamese report —
translating them would break the first and corrupt the second.

MOST IMPORTANT REQUIREMENT — EXTRACT EVERYTHING:
For each statement (balance sheet, income statement, cash flow), list EVERY
SINGLE LINE that appears in it, in the order the document has them — totals and
detail lines alike. A complete balance sheet usually runs to 40-60 lines; a
handful of summary lines is WRONG. Never summarise, never pick out "the
important indicators", never drop a line that carries a figure.

Use only what the source text says. Do not infer or invent figures. When a field
cannot be determined, set it to null (or an empty array/string) — do not omit
the field.

MONEY UNITS:
- WRITE THE NUMBER AS PRINTED ON THE PAGE, with only the thousands separators
  removed. Never multiply into millions or billions, never convert units — the
  program does the conversion. A statement headed "Đơn vị tính: triệu đồng"
  printing 240.800 returns 240800, NOT 240800000000.
- Keep the sign: negative for items shown negative or in parentheses.
- Each statement's "source_unit" records the unit printed at the head of THAT
  statement: "dong" | "trieu dong" | "ty dong". A statement with no unit note
  gets "dong".

The OCR text carries "--- Page N ---" markers at page boundaries, used to cite
sources for the reader. For EVERY STATEMENT you must fill in its "page" (the
page the statement starts on). For an individual line, fill in "page" when you
can determine it and null when you cannot — but this must NEVER reduce the
number of lines you extract. Complete lines matter more than complete page
numbers. Do not invent a page number.

REPORTING PERIOD LABELS — applies to EVERY period label anywhere in the JSON:
write exactly "Năm YYYY" and no other form.
- "31/12/2024", "01/01/2024 - 31/12/2024", "Quý 4/2024", "122024" -> "Năm 2024"
  (a date range takes its ENDING year).
- Columns named by position ("Số cuối kỳ", "Số đầu kỳ", "Kỳ này", "Kỳ trước",
  "Cuối năm", "Đầu năm") must be resolved to the real year from the document's
  own reporting period: the closing column is the reporting year, the opening
  column the year before. For statements as at 31/12/2024, "Số cuối kỳ" ->
  "Năm 2024" and "Số đầu kỳ" -> "Năm 2023".
This applies to period_label, comparative_period_label, each statement's "years"
array, and ESPECIALLY the keys of "values" on every line. Two statements writing
the same year two different ways become two separate columns and make the growth
figures wrong.

Return EXACTLY this JSON schema and no other text:
{{
  "customer": {{
    "ten": "tên doanh nghiệp như in trên tài liệu, hoặc ''",
    "ma_so_thue": "mã số thuế: ĐÚNG 10 chữ số, hoặc 13 với ba chữ số chi nhánh. Chép
      nguyên chữ số, bỏ dấu cách và gạch nối. Không thấy in trên tài liệu thì '' —
      KHÔNG suy ra từ mã nào khác, con số này dùng để tra cứu dữ liệu tín dụng và
      một chữ số sai sẽ kéo về hồ sơ của doanh nghiệp khác"
  }},
  "document_type": "BCTC hợp nhất | BCTC riêng lẻ | không xác định",
  "reporting_period": {{
    "period_label": "Năm YYYY",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "comparative_period_label": "Năm YYYY, or null"
  }},
  "audit_opinion": {{
    "is_audited": true or false,
    "opinion_type": "chấp nhận toàn phần | ngoại trừ | không đủ cơ sở | trái ngược | không xác định",
    "auditor_name": "audit firm name or null",
    "notes": "short note if any, otherwise an empty string",
    "page": <integer page number or null>
  }},
  "balance_sheet": {{
    "unit": "VNĐ",
    "source_unit": "dong | trieu dong | ty dong — the unit PRINTED at the head of the statement, default dong",
    "page": <page the statement starts on, required when determinable>,
    "years": ["every period in the statement, each written as 'Năm YYYY'"],
    "item_columns": ["label", "code", "Năm YYYY", "Năm YYYY", "page"],
    "line_items": [
      ["line name", "code if any else null", <number>, <number>, <page or null>]
      // LIST EVERY LINE OF THE STATEMENT, no shortening
    ]
  }},
  "income_statement": {{"unit": "VNĐ", "source_unit": "dong | trieu dong | ty dong",
                       "page": <page number>, "years": [], "item_columns": [], "line_items": []}},
  "cash_flow_statement": {{"unit": "VNĐ", "source_unit": "dong | trieu dong | ty dong",
                          "page": <page number>, "years": [], "item_columns": [], "line_items": []}},
  "extraction_notes": ["notes on missing data, uncertainty, or poor OCR"]
}}

════ "line_items" — POSITIONAL ARRAYS, NOT OBJECTS ════
Name the columns ONCE per statement in "item_columns", then give each line as an
ARRAY of values in that exact order. Never repeat "label"/"code"/"values"/"page"
on a row: the names were 40% of what the statement weighed.

"item_columns" is always ["label", "code", <one entry per period, newest first>,
"page"]. The periods are the same ones listed in "years", written the same way.

A line whose every period figure is empty is a GROUP HEADING, not data — leave it
out. "I. LƯU CHUYỂN TIỀN TỪ HOẠT ĐỘNG KINH DOANH" with no figures beside it names
the block underneath and carries nothing to underwrite on. A line with a figure
in one period and none in another STAYS, with null for the empty period.
"""


def build_financial_statement_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain, mirroring the document classifier chain."""
    
    return build_extraction_chain(FINANCIAL_STATEMENT_EXTRACTION_SYSTEM_PROMPT, llm)


_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_DIGIT_RUN_PATTERN = re.compile(r"(?<!\d)\d+(?!\d)")
_SQUASHED_DATE_LENGTHS = (6, 8)

PERIOD_LABEL_PREFIX = "Năm "

_PREVIOUS_PERIOD_MARKERS = (
    "ky truoc", "nam truoc", 
    "nam ngoai", "dau ky", 
    "dau nam", "so dau",
    "cung ky", "ky lien truoc",
)
_CURRENT_PERIOD_MARKERS = (
    "ky nay", "nam nay", 
    "ky bao cao", "nam bao cao", 
    "cuoi ky", "cuoi nam",
    "so cuoi", "nam hien tai",
    "ky hien tai", "ky hien hanh",
)


def _year_from_digit_run(run: str) -> str | None:
    """Read a year out of a date written without separators.

    "122024" is December 2024 and "31122024" is 31 December 2024.
    "0104498100" is a tax code and "1234567" is an amount.

    Only 6- and 8-digit runs are considered, and only when the leftover 
    digits form a plausible day/month — otherwise the run is left alone.
    """

    if len(run) == 6:
        # MMYYYY, then YYYYMM.
        if _YEAR_PATTERN.fullmatch(run[2:]) and 1 <= int(run[:2]) <= 12:
            return run[2:]
        if _YEAR_PATTERN.fullmatch(run[:4]) and 1 <= int(run[4:]) <= 12:
            return run[:4]
        return None
    if len(run) == 8:
        # DDMMYYYY, then YYYYMMDD.
        if (
            _YEAR_PATTERN.fullmatch(run[4:])
            and 1 <= int(run[:2]) <= 31
            and 1 <= int(run[2:4]) <= 12
        ):
            return run[4:]
        if (
            _YEAR_PATTERN.fullmatch(run[:4])
            and 1 <= int(run[4:6]) <= 12
            and 1 <= int(run[6:]) <= 31
        ):
            return run[:4]
    return None


def normalize_period_label(
    raw: Any,
    current_year: str | None = None,
    previous_year: str | None = None,
) -> str:
    """Render any period label the model produced as a single "Năm YYYY" form.

    Statements label the same year in whatever style the source document used:
    "2024", "31/12/2024", "122024", "01/01/2024 - 31/12/2024", or by position
    ("Số cuối kỳ"). Those strings are dict keys for the per-year figures, so
    mixed styles across two uploaded statements split one real year into several
    columns — and the growth ratio, which walks the columns in sorted order,
    then compares a year against itself.

    A date range resolves to its LAST year, because a column labelled with a
    range reports the period ending on that date. Positional labels resolve
    against ``current_year``/``previous_year`` taken from the report's own
    reporting period; without that context they are left alone rather than
    guessed at. A label that yields no year is returned unchanged: a slightly
    odd column beats a silently missing one. Idempotent.
    """

    text = str(raw or "").strip()
    if not text:
        return text

    years = _YEAR_PATTERN.findall(text)
    if years:
        return f"{PERIOD_LABEL_PREFIX}{years[-1]}"

    squashed = [
        year
        for run in _DIGIT_RUN_PATTERN.findall(text)
        if len(run) in _SQUASHED_DATE_LENGTHS
        and (year := _year_from_digit_run(run))
    ]
    if squashed:
        return f"{PERIOD_LABEL_PREFIX}{squashed[-1]}"

    # Checked before the current-period markers because "cuối kỳ trước" carries
    # both, and the trailing "trước" is the one that decides.
    normalized = normalize_text(text)
    if any(marker in normalized for marker in _PREVIOUS_PERIOD_MARKERS):
        if previous_year:
            return f"{PERIOD_LABEL_PREFIX}{previous_year}"
        return text
    if any(marker in normalized for marker in _CURRENT_PERIOD_MARKERS):
        if current_year:
            return f"{PERIOD_LABEL_PREFIX}{current_year}"
    return text


# Columns of a line-item row that are not a reporting period. Everything else in
# "item_columns" names a year, which is what lets one declaration at the top of a
# statement replace a "values" dict repeated on all 108 rows.
ROW_META_COLUMNS = ("label", "code", "page")


def iter_line_items(statement: Any):
    """Yield (label, code, values, page) for each row, in either shape.

    Two shapes exist on purpose. Rows are positional arrays under
    "item_columns" now; before that each row was an object repeating
    label/code/values/page, and every record already written to logs/ is in that
    older shape. Readers go through here so neither the ratio calculator nor the
    period normaliser has to know which one it is holding.
    """

    if not isinstance(statement, dict):
        return
    rows = statement.get("line_items") or []
    columns = statement.get("item_columns")

    if not isinstance(columns, list):
        for row in rows:
            if isinstance(row, dict):
                values = row.get("values")
                yield (
                    row.get("label"),
                    row.get("code"),
                    values if isinstance(values, dict) else {},
                    row.get("page"),
                )
        return

    at = {name: index for index, name in enumerate(columns)}
    years = [c for c in columns if c not in ROW_META_COLUMNS]

    def cell(row: list[Any], name: str) -> Any:
        index = at.get(name)
        return row[index] if index is not None and index < len(row) else None

    for row in rows:
        if isinstance(row, list):
            yield (
                cell(row, "label"),
                cell(row, "code"),
                {year: cell(row, year) for year in years},
                cell(row, "page"),
            )


def map_line_item_values(statement: Any, convert) -> None:
    """Apply ``convert(value)`` to every period figure, in either shape."""

    if not isinstance(statement, dict):
        return
    columns = statement.get("item_columns")
    rows = statement.get("line_items") or []

    if not isinstance(columns, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("values"), dict):
                row["values"] = {
                    period: convert(value)
                    for period, value in row["values"].items()
                }
        return

    numeric = [i for i, name in enumerate(columns)
               if name not in ROW_META_COLUMNS]
    for row in rows:
        if isinstance(row, list):
            for index in numeric:
                if index < len(row):
                    row[index] = convert(row[index])


def rename_line_item_periods(statement: Any, rename) -> None:
    """Rewrite period labels with ``rename(label)``, in either shape.

    Columnar records carry the period once in "item_columns", so the rename
    happens there; object rows carry it on every row's "values" key.
    """

    if not isinstance(statement, dict):
        return
    columns = statement.get("item_columns")
    if isinstance(columns, list):
        statement["item_columns"] = [
            c if c in ROW_META_COLUMNS else rename(c) for c in columns
        ]
        return
    for row in statement.get("line_items") or []:
        if isinstance(row, dict):
            row["values"] = _normalize_values_by_period_map(
                row.get("values"), rename
            )


def _normalize_values_by_period_map(values: Any, rename) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    return {rename(period): value for period, value in values.items()}


def resolve_report_years(result: Any) -> tuple[str | None, str | None]:
    """Read (current_year, previous_year) out of an extraction's own period block."""

    if not isinstance(result, dict):
        return None, None
    period = result.get("reporting_period")
    if not isinstance(period, dict):
        return None, None

    def _year_of(*candidates: Any) -> str | None:
        for candidate in candidates:
            found = _YEAR_PATTERN.findall(str(candidate or ""))
            if found:
                return found[-1]
        return None

    current = _year_of(period.get("period_label"), period.get("end_date"))
    previous = _year_of(period.get("comparative_period_label"))
    if not previous and current:
        previous = str(int(current) - 1)
    return current, previous


def _normalize_values_by_period(
    values: Any,
    current_year: str | None,
    previous_year: str | None,
) -> Any:
    """Re-key a line item's {period: number} map onto normalized labels."""

    if not isinstance(values, dict):
        return values
    normalized: dict[str, Any] = {}
    for period, value in values.items():
        key = normalize_period_label(period, current_year, previous_year)
        if key in normalized and normalized[key] is not None:
            continue
        normalized[key] = value
    return normalized


def normalize_extraction_periods(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize every period label in an extraction result, in place."""

    current_year, previous_year = resolve_report_years(result)

    period = result.get("reporting_period")
    if isinstance(period, dict):
        for key in ("period_label", "comparative_period_label"):
            if period.get(key):
                period[key] = normalize_period_label(
                    period[key], current_year, previous_year
                )

    for statement_key in ("balance_sheet", "income_statement", "cash_flow_statement"):
        statement = result.get(statement_key)
        if not isinstance(statement, dict):
            continue
        years = statement.get("years")
        if isinstance(years, list):
            seen: list[str] = []
            for year in years:
                label = normalize_period_label(year, current_year, previous_year)
                if label not in seen:
                    seen.append(label)
            statement["years"] = seen
        rename_line_item_periods(
            statement,
            lambda label: normalize_period_label(label, current_year, previous_year),
        )

    return result


_STATEMENT_KEYS = (
    "balance_sheet", 
    "income_statement", 
    "cash_flow_statement"
)


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using each block's own source_unit, in place."""

    for key in _STATEMENT_KEYS:
        statement = result.get(key)
        if not isinstance(statement, dict):
            continue
        multiplier = resolve_money_multiplier(statement.get("source_unit"))
        map_line_item_values(
            statement, lambda value: scale_amount(value, multiplier)
        )

    return result


def drop_heading_rows(result: dict[str, Any]) -> dict[str, Any]:
    """Remove line items that carry no figure in any period, in place.

    A cash-flow statement prints block headings as ordinary rows — "I. LƯU CHUYỂN
    TIỀN TỪ HOẠT ĐỘNG KINH DOANH" with every period empty. They name what follows
    and carry nothing to underwrite on, and there were 11 of them in one real
    statement. The prompt asks the model to leave them out; this is the guard,
    because a prompt rule is a request.

    A row with a figure in one period and none in another is real data and stays.
    """

    for key in _STATEMENT_KEYS:
        statement = result.get(key)
        if not isinstance(statement, dict):
            continue
        rows = statement.get("line_items") or []
        keep_at = [
            index for index, (_, _, values, _) in enumerate(iter_line_items(statement))
            if any(v is not None for v in values.values())
        ]
        if len(keep_at) != len(rows):
            statement["line_items"] = [rows[i] for i in keep_at]
    return result


def extract_financial_statement_from_xml(path: str) -> tuple[dict[str, Any] | None, str]:
    """Read an e-tax XML filing"""

    result = parse_tax_xml(path)
    if result.error:
        return None, result.error
    if result.kind != "bctc":
        return None, (
            f"The XML is {result.kind or 'another form type'}, "
            f"not a financial statement."
        )
    return result.financial_statement_extraction, ""


def extract_financial_statement_data(
    chain: Any,
    filename: str,
    content: str,
    path: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Turn one financial-statement document into the structured record."""

    if (path or filename).lower().endswith(".xml"):
        record, note = extract_financial_statement_from_xml(path or filename)
        if record is not None:
            return record, ""
        if note:
            content = f"[XML không đọc trực tiếp được: {note}]\n\n{content}"

    return run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No financial statement extraction LLM configured.",
        lambda result: drop_heading_rows(
            normalize_amounts(normalize_extraction_periods(result))
        ),
    )
