"""Formatting helpers for underwriting report output."""

import re

VND_PER_BILLION = 1_000_000_000

_AMOUNT_TOKEN = re.compile(r"[+-]?\d{1,3}(?:[.,]\d{3})+(?![\d.,])")
_TRAILING_CURRENCY = re.compile(r"\s*(?:VN[ĐD]|đồng|VND)\b", re.IGNORECASE)
_ALREADY_SCALED = re.compile(r"^\s*(?:tỷ|triệu|nghìn\s+tỷ|ngàn\s+tỷ)\b", re.IGNORECASE)
# A markdown table row. Cells in one drop the "tỷ VNĐ" suffix because the table
# already carries its unit on the line below it, and repeating it in all eighty
# cells of a statement is noise. A figure standing in a sentence has no such
# label, so it keeps the unit.
_TABLE_ROW = re.compile(r"^\s*\|")
# A "tỷ VNĐ" the model wrote itself. The converter only ever sees raw đồng, so a
# figure that arrived already scaled slipped past the table rule entirely — one
# report carried the unit in 99 table rows. Matched with the figure in front of
# it so the substitution cannot eat a bare unit standing on its own, such as the
# "(Đơn vị: tỷ VNĐ)" caption above the table.
_SCALED_IN_CELL = re.compile(r"(?<=\d)\s*(?:tỷ|triệu|nghìn\s+tỷ|ngàn\s+tỷ)\s*VN[ĐD]\b",
                             re.IGNORECASE)


def format_vn_number(value: float, decimals: int = 2) -> str:
    """Format a number Vietnamese-style: '.' thousands, ',' decimal."""
    formatted = f"{value:,.{decimals}f}"  # US style: 3,991.12
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def render_money(value: float | None) -> str:
    """A đồng amount the way every prompt block writes it.

    One function so the blocks cannot drift apart on formatting, and no division
    in it: đồng is what the extraction passes and the calculators produce, and
    đồng is what the blocks say. Turning it into tỷ VNĐ belongs to
    convert_amounts_in_text at the end of the run and to nobody else.

    Grouped rather than bare on purpose — that final converter only recognises a
    figure by its thousands separators, which is what keeps it off tax codes and
    account numbers. A block writing 225510140846 would reach the report as a
    number nothing downstream can read as money.
    """

    if value is None:
        return "N/A"
    return format_vn_number(value, 0)


def to_billion_vnd(value: float, decimals: int = 2, suffix: bool = True) -> str:
    """Render a raw đồng amount in tỷ VNĐ, with or without the unit written out."""
    figure = format_vn_number(value / VND_PER_BILLION, decimals)
    return f"{figure} tỷ VNĐ" if suffix else figure


def _parse_grouped_amount(token: str) -> float | None:
    """Parse a grouped integer amount in either VN or US locale."""
    text = token.strip()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")

    if "," in text and "." in text:
        # Decimal separator is whichever appears last.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(".", "").replace(",", "")

    try:
        return sign * float(text)
    except ValueError:
        return None


def convert_amounts_in_text(text: str, decimals: int = 2) -> str:
    """Convert every raw đồng amount in a markdown/text block to tỷ VNĐ.

    Skips percentages, values already expressed in tỷ/triệu/nghìn tỷ, and
    contiguous identifier digits (tax codes, registration numbers). Absorbs a
    trailing "VNĐ"/"đồng" so the result is not double-labelled. Writes the unit
    after figures in prose and leaves it off inside table cells — see _TABLE_ROW.

    A table cell is also stripped of a unit the MODEL wrote, not just one this
    function would have added. The rule is about the finished table, and until
    now it only governed the half of the figures that arrived as raw đồng.
    """
    if not text:
        return text

    # Line by line so each figure knows whether it is in a table. Safe because
    # _AMOUNT_TOKEN matches only digits and separators, so no match can span a
    # newline; split and join on the same character, so a trailing newline
    # survives.
    def one(line: str) -> str:
        in_table = bool(_TABLE_ROW.match(line))
        converted = _convert_line(line, decimals, suffix=not in_table)
        return _SCALED_IN_CELL.sub("", converted) if in_table else converted

    return "\n".join(one(line) for line in text.split("\n"))


def _convert_line(text: str, decimals: int, suffix: bool) -> str:
    """Convert every đồng amount on one line."""

    result = []
    cursor = 0
    for match in _AMOUNT_TOKEN.finditer(text):
        if match.start() < cursor:
            continue
        result.append(text[cursor:match.start()])
        token = match.group()
        tail = text[match.end():]

        if tail[:1] == "%" or _ALREADY_SCALED.match(tail):
            result.append(token)
            cursor = match.end()
            continue

        value = _parse_grouped_amount(token)
        if value is None:
            result.append(token)
            cursor = match.end()
            continue

        currency = _TRAILING_CURRENCY.match(tail)
        replacement = to_billion_vnd(value, decimals, suffix)
        # Preserve an explicit leading "+" (deltas / chênh lệch); the "-" sign is
        # already produced by number formatting for negative values.
        if token.lstrip()[:1] == "+" and value >= 0:
            replacement = "+" + replacement
        result.append(replacement)
        cursor = match.end() + (currency.end() if currency else 0)

    result.append(text[cursor:])
    return "".join(result)
