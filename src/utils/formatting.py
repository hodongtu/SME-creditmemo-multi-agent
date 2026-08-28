"""Formatting helpers for underwriting report output."""
import re

VND_PER_BILLION = 1_000_000_000

_AMOUNT_TOKEN = re.compile(r"[+-]?\d{1,3}(?:[.,]\d{3})+(?![\d.,])")
_TRAILING_CURRENCY = re.compile(r"\s*(?:VN[ĐD]|đồng|VND)\b", re.IGNORECASE)
_ALREADY_SCALED = re.compile(r"^\s*(?:tỷ|triệu|nghìn\s+tỷ|ngàn\s+tỷ)\b", re.IGNORECASE)


def format_vn_number(value: float, decimals: int = 2) -> str:
    """Format a number Vietnamese-style: '.' thousands, ',' decimal."""
    formatted = f"{value:,.{decimals}f}"  # US style: 3,991.12
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def to_billion_vnd(value: float, decimals: int = 2) -> str:
    """Render a raw đồng amount as a 'tỷ VNĐ' string."""
    return f"{format_vn_number(value / VND_PER_BILLION, decimals)} tỷ VNĐ"


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
    trailing "VNĐ"/"đồng" so the result is not double-labelled.
    """
    if not text:
        return text

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
        replacement = to_billion_vnd(value, decimals)
        # Preserve an explicit leading "+" (deltas / chênh lệch); the "-" sign is
        # already produced by number formatting for negative values.
        if token.lstrip()[:1] == "+" and value >= 0:
            replacement = "+" + replacement
        result.append(replacement)
        cursor = match.end() + (currency.end() if currency else 0)

    result.append(text[cursor:])
    return "".join(result)
