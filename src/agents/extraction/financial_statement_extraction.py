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
from src.prompts import system_prompt


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


def build_financial_statement_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain, mirroring the document classifier chain."""
    
    return build_extraction_chain(system_prompt("financial_statement_extraction"), llm)


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
    """Read a year out of a date written without separators."""

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
    """Render any period label the model produced as a single "Năm YYYY" form."""

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


ROW_META_COLUMNS = ("label", "code", "page")


def iter_line_items(statement: Any):
    """Yield (label, code, values, page) for each row, in either shape."""

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
    """Rewrite period labels with ``rename(label)``, in either shape."""

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
    """Remove line items that carry no figure in any period, in place."""

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
