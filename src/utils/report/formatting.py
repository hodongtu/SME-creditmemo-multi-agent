"""Formatting helpers for underwriting report output."""

import re

from src.utils.common import CODE_FENCE as _FENCE

VND_PER_BILLION = 1_000_000_000

_AMOUNT_TOKEN = re.compile(r"[+-]?\d{1,3}(?:[.,]\d{3})+(?![\d.,])")
_TRAILING_CURRENCY = re.compile(r"\s*(?:VN[ĐD]|đồng|VND)\b", re.IGNORECASE)
_ALREADY_SCALED = re.compile(r"^\s*(?:tỷ|triệu|nghìn\s+tỷ|ngàn\s+tỷ)\b", re.IGNORECASE)
_ZERO_DECIMALS = re.compile(r"(?<=\d)[.,]0+(?=\s*%)")
_ZERO_CELL = re.compile(r"^\*{0,2}0(?:[.,]0+)?\s*%?\*{0,2}$")
_TABLE_RULE = re.compile(r"^[\s|:-]+$")
_TABLE_ROW = re.compile(r"^\s*\|")
_SCALED_IN_CELL = re.compile(r"(?<=\d)\s*(?:tỷ|triệu|nghìn\s+tỷ|ngàn\s+tỷ)\s*VN[ĐD]\b",
                             re.IGNORECASE)


def format_vn_number(value: float, decimals: int = 2) -> str:
    """Format a number Vietnamese-style: '.' thousands, ',' decimal."""
    formatted = f"{value:,.{decimals}f}"  # US style: 3,991.12
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def render_money(value: float | None) -> str:
    """A đồng amount the way every prompt block writes it."""

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
    """Convert every raw đồng amount in a markdown/text block to tỷ VNĐ."""
    if not text:
        return text

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

        if token.lstrip()[:1] == "+" and value >= 0:
            replacement = "+" + replacement
        result.append(replacement)
        cursor = match.end() + (currency.end() if currency else 0)

    result.append(text[cursor:])
    return "".join(result)


def tidy_numbers(text: str) -> str:
    """Drop all-zero decimals from percentages, and write a zero cell as "-"."""

    if not text:
        return text

    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
            out.append(line)
            continue
        line = _ZERO_DECIMALS.sub("", line)
        if not in_code and line.lstrip().startswith("|") and not _TABLE_RULE.match(line):
            cells = line.split("|")
            for index, cell in enumerate(cells):
                if _ZERO_CELL.match(cell.strip()):
                    body = " - " if cell.startswith(" ") or cell.endswith(" ") else "-"
                    cells[index] = body
            line = "|".join(cells)
        out.append(line)
    return "\n".join(out)
