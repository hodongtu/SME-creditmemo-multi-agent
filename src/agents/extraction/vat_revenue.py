"""Read the ```vat-doanh-thu``` block Credit Relationship Agent writes."""

import re

from src.agents.extraction.cic_s10a_extraction import normalize_month_label


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
