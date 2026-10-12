"""Monthly VAT revenue, from the agent's own block or from an e-tax XML filing.

A VAT return 01/GTGT filed as XML is read here; the shared e-tax envelope that
says which form a file is lives in src/utils/reading/etax_xml.py.
"""

import re

from src.agents.extraction.cic_s10a_extraction import normalize_month_label
from src.utils.reading.etax_xml import (
    FORM_VAT_01GTGT,
    filing_period,
    indicator_codes,
    load_filing,
    main_block,
    year_of,
)


VAT_BLOCK_FENCE = "vat-doanh-thu"

_BLOCK = re.compile(
    r"```vat-doanh-thu[ \t]*\n(.*?)```",
    re.DOTALL,
)
_MONTH_LINE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*:\s*([\d.,]+)\s*$")
_QUARTER_LINE = re.compile(r"^\s*[Qq]\s*([1-4])\s*/\s*(\d{4})\s*:\s*([\d.,]+)\s*\(\s*qu[yý]\s*\)\s*$")

_QUARTER_MONTHS = {
    "1": (1, 2, 3),
    "2": (4, 5, 6),
    "3": (7, 8, 9),
    "4": (10, 11, 12),
}

def _parse_amount(text: str) -> float | None:
    """"31,400,000,000" or "31.400.000.000" -> 31400000000.0."""

    digits = re.sub(r"[.,]", "", text)
    if not digits.isdigit():
        return None
    return float(digits)


def parse_vat_revenue_block(text: str) -> dict[str, tuple[float, bool]]:
    """{"MM/YYYY": (doanh_thu, is_estimated)} from the agent's own block, or {}."""

    match = _BLOCK.search(text or "")
    if not match:
        return {}

    result: dict[str, tuple[float, bool]] = {}

    def _set(month: str, value: float, estimated: bool) -> None:
        month = normalize_month_label(month)
        existing = result.get(month)
        if existing is not None and not existing[1]:
            return  # a real monthly figure already claimed this month
        result[month] = (value, estimated)

    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        month_match = _MONTH_LINE.match(line)
        if month_match:
            mm, yyyy, raw_amount = month_match.groups()
            amount = _parse_amount(raw_amount)
            if amount is not None:
                _set(f"{mm}/{yyyy}", amount, False)
            continue
        quarter_match = _QUARTER_LINE.match(line)
        if quarter_match:
            q, yyyy, raw_amount = quarter_match.groups()
            amount = _parse_amount(raw_amount)
            if amount is None:
                continue
            share = amount / 3
            for mm in _QUARTER_MONTHS[q]:
                _set(f"{mm:02d}/{yyyy}", share, True)

    return result


def strip_vat_revenue_block(text: str) -> str:
    """Remove the ```vat-doanh-thu``` block — an internal channel, never shown."""

    if not text or "```vat-doanh-thu" not in text:
        return text
    return _BLOCK.sub("", text)


def merge_vat_series(
    *sources: dict[str, tuple[float, bool]],
) -> dict[str, tuple[float, bool]]:
    """Combine monthly VAT revenue from several readings, best evidence first."""

    merged: dict[str, tuple[float, bool]] = {}
    for source in sources:
        for month, (revenue, estimated) in source.items():
            previous = merged.get(month)
            if previous is not None and not previous[1] and estimated:
                continue
            merged[month] = (revenue, estimated)
    return merged


VAT_TOTAL_REVENUE = "ct34"
# The form defines ct34 as the sum of these three, so a mismatch means the wrong
# element was read rather than that the taxpayer filed something unusual.
VAT_REVENUE_PARTS = ("ct26", "ct27", "ct32a")


def _revenue_identity_error(codes: dict[str, float], revenue: float) -> str:
    parts = sum(codes.get(code, 0.0) for code in VAT_REVENUE_PARTS)
    if abs(parts - revenue) > 1:
        return (f"{VAT_TOTAL_REVENUE} ({revenue:,.0f}) does not equal "
                f"{'+'.join(VAT_REVENUE_PARTS)} ({parts:,.0f}), so the indicator "
                "codes may not have been read correctly.")
    return ""


def read_vat_revenue_xml(path: str) -> tuple[dict[str, tuple[float, bool]], list[str], str]:
    """One 01/GTGT filing as {"MM/YYYY": (revenue, is_estimated)}, notes and error.

    A quarterly return is split evenly across its three months and every month
    is flagged estimated, because the form declares the quarter as one figure.
    """

    root, header = load_filing(path)
    if root is None:
        return {}, [], header.error
    if header.form_id != FORM_VAT_01GTGT:
        return {}, [], header.error or "Not a VAT return (01/GTGT)."

    codes = indicator_codes(main_block(root))
    revenue = codes.get(VAT_TOTAL_REVENUE)
    if revenue is None:
        return {}, [], f"The return has no {VAT_TOTAL_REVENUE} indicator (total revenue)."
    identity = _revenue_identity_error(codes, revenue)
    if identity:
        return {}, [], identity

    period_type, label, end_date = filing_period(root)
    year = year_of(end_date).replace("Năm ", "")
    number = label.split("/")[0].strip()
    if not year or not number.isdigit():
        return {}, [], "Could not read the declaration period."

    if period_type == "M":
        series = {f"{int(number):02d}/{year}": (revenue, False)}
        note = (f"Monthly return {number}/{year}: revenue is the declared figure, "
                "not an estimate.")
    elif period_type == "Q":
        quarter = int(number)
        share = revenue / 3
        series = {
            f"{month:02d}/{year}": (share, True)
            for month in range(quarter * 3 - 2, quarter * 3 + 1)
        }
        note = (f"Quarterly return {quarter}/{year}: split evenly across 3 months "
                "and flagged as estimated.")
    else:
        return {}, [], f"Unrecognised declaration period type: {period_type!r}."
    return series, [note], ""
