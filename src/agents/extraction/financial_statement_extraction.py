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


REQUIRED_TOP_LEVEL_KEYS = {
    "document_type",
    "reporting_period",
    "audit_opinion",
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "notes_summary",
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
  gets "dong". If the notes carry their own unit, record it in the source_unit
  of "notes_summary" — do not copy the main statement's unit across.

The OCR text carries "--- Page N ---" markers at page boundaries, used to cite
sources for the reader. For EVERY STATEMENT you must fill in its "page" (the
page the statement starts on). For an individual line, fill in "page" when you
can determine it and null when you cannot — but this must NEVER reduce the
number of lines you extract. Complete lines matter more than complete page
numbers. Do not invent a page number.

"notes_summary" condenses the notes to the financial statements — usually the
longest section. Keep only what bears on credit underwriting, without copying it
out verbatim: material accounting policies, related-party transactions,
contingent liabilities, events after the reporting date, and the breakdown of
large items (borrowings, large receivables/payables, inventory, and so on). Use
"other_material_disclosures" for any other significant point that fits none of
those buckets — never drop material information just because there is no
ready-made slot for it.

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
    "line_items": [
      {{"label": "line name", "code": "code if any, otherwise null",
        "values": {{"Năm YYYY": <number exactly as printed>}}, "page": <integer page number or null>}}
      // LIST EVERY LINE OF THE STATEMENT, no shortening
    ]
  }},
  "income_statement": {{"unit": "VNĐ", "source_unit": "dong | trieu dong | ty dong",
                       "page": <page number>, "years": [], "line_items": []}},
  "cash_flow_statement": {{"unit": "VNĐ", "source_unit": "dong | trieu dong | ty dong",
                          "page": <page number>, "years": [], "line_items": []}},
  "notes_summary": {{
    "source_unit": "dong | trieu dong | ty dong — the unit PRINTED in the notes, default dong",
    "accounting_policies": "brief summary or an empty string (ending with '(trang N)' when determinable)",
    "related_party_transactions": [
      {{"counterparty": "...", "nature": "...", "amount": <number exactly as printed, or null>, "year": "...",
        "page": <integer page number or null>}}
    ],
    "contingent_liabilities": ["... (trang N)"],
    "subsequent_events": ["... (trang N)"],
    "key_item_breakdowns": [
      {{"item": "e.g. Vay và nợ thuê tài chính", "breakdown": "...", "amount": <number exactly as printed, or null>,
        "page": <integer page number or null>}}
    ],
    "other_material_disclosures": ["... (trang N)"]
  }},
  "extraction_notes": ["notes on missing data, uncertainty, or poor OCR"]
}}

For the free-text entries (contingent_liabilities, subsequent_events,
other_material_disclosures, accounting_policies): append "(trang N)" to the
string when you can determine the page; when you cannot, leave the string
without it — never write "(trang null)" or anything like it. The suffix stays
Vietnamese because it is printed in the report.
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
        for line_item in statement.get("line_items") or []:
            if isinstance(line_item, dict):
                line_item["values"] = _normalize_values_by_period(
                    line_item.get("values"), current_year, previous_year
                )

    notes = result.get("notes_summary")
    if isinstance(notes, dict):
        for entry in notes.get("related_party_transactions") or []:
            if isinstance(entry, dict) and entry.get("year"):
                entry["year"] = normalize_period_label(
                    entry["year"], current_year, previous_year
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
        for line_item in statement.get("line_items") or []:
            if not isinstance(line_item, dict):
                continue
            values = line_item.get("values")
            if isinstance(values, dict):
                line_item["values"] = {
                    period: scale_amount(value, multiplier)
                    for period, value in values.items()
                }

    notes = result.get("notes_summary")
    if isinstance(notes, dict):
        multiplier = resolve_money_multiplier(notes.get("source_unit"))
        for field in ("related_party_transactions", "key_item_breakdowns"):
            for entry in notes.get(field) or []:
                if isinstance(entry, dict):
                    entry["amount"] = scale_amount(entry.get("amount"), multiplier)
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
        lambda result: normalize_amounts(normalize_extraction_periods(result)),
    )
