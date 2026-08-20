"""Read the ```vat-doanh-thu``` block Credit Relationship Agent writes.

Deliberately not another LLM extraction pass: the debt/revenue chart needs a
real monthly VAT-revenue series, but the agent's own analysis call already
reads the tờ khai thuế GTGT as ordinary evidence (see document_matrix.yaml's
CREDIT_RELATIONSHIP_AGENT entry for to_khai_thue_gtgt and the block-emission
rule in credit-relationship-guidance.md) — so it is asked to also transcribe
just the revenue figures into a small fenced block, the same "agent writes a
fenced block, code reads it back" shape ```mermaid and ```linechart already
use elsewhere in this report. The debt series itself stays fully code-driven
from cic_s10a_extraction's structured JSON; only the VAT line's source changes.

Pure text processing here — no LLM call, no document classification. The block
is an internal data channel, never meant for the reader, so every entry point
into a final response strips it (see strip_vat_revenue_block).
"""

from __future__ import annotations

import re

from src.agents.cic_s10a_extraction import normalize_month_label

VAT_BLOCK_FENCE = "vat-doanh-thu"

_BLOCK = re.compile(
    r"```vat-doanh-thu[ \t]*\n(.*?)```",
    re.DOTALL,
)
# "MM/YYYY: <số>" or "QN/YYYY: <số> (quy)". The quarter marker is the literal
# "(quy)" suffix — required, not inferred from the "Q" prefix alone, so a
# stray "Q" typo in a monthly label can't silently get tripled into a quarter.
_MONTH_LINE = re.compile(
    r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*:\s*([\d.,]+)\s*$"
)
_QUARTER_LINE = re.compile(
    r"^\s*[Qq]\s*([1-4])\s*/\s*(\d{4})\s*:\s*([\d.,]+)\s*\(\s*qu[yý]\s*\)\s*$"
)

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
    """{"MM/YYYY": (doanh_thu, is_estimated)} from the agent's own block, or {}.

    A quarter line expands into its 3 months, each getting doanh_thu/3 flagged
    as estimated. Where a monthly and a quarter-derived figure collide for the
    same month, the real monthly figure wins — it is never an estimate, so it
    is always the more trustworthy one. Malformed lines are skipped rather
    than raising: one bad line should not lose every other figure the agent
    read correctly.
    """

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
    """Remove the ```vat-doanh-thu``` block — an internal channel, never shown.

    Applied to every response on the way out (see supervisor.py's _finalize),
    including a composed multi-agent memo: the composer is told to preserve
    fenced blocks verbatim, so a block meant only for this parser could just as
    easily survive composition and leak into the reader-facing PDF untouched.
    """

    if not text or "```vat-doanh-thu" not in text:
        return text
    return _BLOCK.sub("", text)


def merge_vat_series(
    *sources: dict[str, tuple[float, bool]],
) -> dict[str, tuple[float, bool]]:
    """Combine monthly VAT revenue from several readings, best evidence first.

    Sources are given in increasing order of trust, so the last one to claim a
    month keeps it. Two rules decide what "better" means, and they compose:

    - A figure the taxpayer filed for that month beats one divided out of a
      quarter, which is the rule ``parse_vat_revenue_block`` already applies
      within a single block.
    - A figure read from an e-tax XML beats one the credit-relationship agent
      transcribed out of the same return, because one is read from an indicator
      code and the other is retyped by a model.

    A real monthly figure is never displaced by an estimate, whatever its
    source: an exact number for the month is the better evidence even when the
    estimate came from a more trustworthy file.
    """

    merged: dict[str, tuple[float, bool]] = {}
    for source in sources:
        for month, (revenue, estimated) in source.items():
            previous = merged.get(month)
            if previous is not None and not previous[1] and estimated:
                continue
            merged[month] = (revenue, estimated)
    return merged
