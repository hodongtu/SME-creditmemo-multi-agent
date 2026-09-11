"""LLM extraction of the CIC S10A credit-relationship report into structured JSON"""

import re
from typing import Any, Iterable

from src.agents.extraction.structured_extraction import (
    TRIEU_VND,
    build_extraction_chain,
    run_extraction,
    scale_amount,
)

REQUIRED_TOP_LEVEL_KEYS = {
    "bao_cao",
    "khach_hang",
    "du_no_hien_tai",
    "du_no_12_thang",
    "xep_hang_tin_dung",
}

VND_CURRENCY_CODES = frozenset({"VND", "VNĐ", "VN", ""})

CIC_S10A_EXTRACTION_SYSTEM_PROMPT = """
You turn a CREDIT RELATIONSHIP DETAIL REPORT (form S10A) from Vietnam's National
Credit Information Centre (CIC) into JSON, for SME credit underwriting.

The text is OCR of a scan, so lines may be skewed and columns run together.
Extract only what the page actually says. Do not infer, do not invent a figure.

READING NUMBERS — the easiest thing to get wrong, read carefully:

1. A "," here is a THOUSANDS SEPARATOR, not a decimal point: "12,345" -> 12345.
   Reading it as 12.345 is wrong by a factor of a thousand.

2. WRITE THE NUMBER AS PRINTED, with only the thousands separators removed.
   Never multiply into millions, never convert units — the program does that
   afterwards.

3. "(-)" means "Thiếu kỳ báo cáo số liệu" (no data for that period) -> null. An
   EMPTY cell -> null. Never substitute 0: "no data" and "a balance of zero" are
   opposite statements to anyone reading the debt chart.

4. The VND column and the foreign-currency (USD) column are separate. Do not add
   them together and do not convert between them — the report prints no exchange
   rate.

5. Table "2.6. Diễn biến dư nợ 12 tháng gần nhất" carries a "(*)" note: those
   balances ALREADY INCLUDE foreign-currency debt converted to VND. Copy the
   column as it stands; adding USD from another table would count it twice.

6. Keep the rows in the page's order (table 2.6 runs newest month first). Do not
   re-sort.

7. No section 2.6 at all — for instance the document is really a loan security
   report, form R20 — set "du_no_12_thang": [] and record why in
   "extraction_notes". Never invent a twelve-month series.

Write every month as "MM/YYYY", two digits for the month, e.g. "03/2026".

"page" is the page the table sits on, read from the "--- Page N ---" markers in
the OCR text; null when it cannot be determined. Do not invent a page number.

Return EXACTLY this JSON schema and no other text:
{{
  "bao_cao": {{
    "so_hieu": "e.g. 2026/S10A, or ''",
    "ngay_gui": "dd/mm/yyyy or ''",
    "don_vi_tra_cuu": "name of the enquiring institution, or ''",
    "page": <integer or null>
  }},
  "khach_hang": {{
    "ten": "...", "ma_cic": "...", "ma_so_thue": "...",
    "nguoi_dai_dien": "...", "dia_chi": "...",
    "page": <integer or null>
  }},
  "du_no_hien_tai": [
    {{"tctd": "lender name and branch",
      "ngay_bao_cao": "dd/mm/yyyy or ''",
      "khoan_muc": "e.g. Dư nợ cho vay ngắn hạn | Dư nợ cho vay trung hạn | Tổng cộng",
      "nhom_no": "e.g. Nợ đủ tiêu chuẩn, or ''",
      "vnd": <number exactly as printed, or null>,
      "ngoai_te": <number exactly as printed, or null>,
      "loai_ngoai_te": "e.g. USD, or ''",
      "page": <integer or null>}}
  ],
  "du_no_12_thang": [
    {{"thang": "MM/YYYY",
      "du_no_vay": <number exactly as printed, or null>,
      "du_no_the": <number exactly as printed, or null>,
      "tong_du_no": <number exactly as printed, or null>,
      "page": <integer or null>}}
  ],
  "cam_ket_ngoai_bang": [
    {{"tctd": "...", "gia_tri": <number exactly as printed, or null>,
      "loai_tien": "VND | USD | ...", "nhom_no": "...",
      "ngay_bao_cao": "dd/mm/yyyy or ''", "page": <integer or null>}}
  ],
  "xep_hang_tin_dung": [
    {{"nam": "YYYY", "hang": "e.g. T3, B2", "pd_phan_tram": <number or null>,
      "dien_giai": "...", "page": <integer or null>}}
  ],
  "canh_bao": {{
    "no_xau_5_nam": "the conclusion of section 2.7 verbatim, or ''",
    "no_can_chu_y_12_thang": "the conclusion of section 2.9 verbatim, or ''",
    "cham_thanh_toan_the": "the conclusion of section 2.8 verbatim, or ''",
    "du_no_the_tin_dung": "the conclusion of section 2.2 verbatim, or ''",
    "du_no_ban_vamc": "the conclusion of section 2.3 verbatim, or ''"
  }},
  "extraction_notes": ["notes on missing sections, uncertainty, or poor OCR"]
}}
"""


def build_cic_s10a_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the CIC S10A report."""

    return build_extraction_chain(CIC_S10A_EXTRACTION_SYSTEM_PROMPT, llm)


_YEAR = r"(?:19|20)\d{2}"
_SEP = r"\s*[/\-.]\s*"
_DATE_PATTERNS = (
    re.compile(rf"(?<!\d)(?:0?[1-9]|[12]\d|3[01]){_SEP}(0?[1-9]|1[0-2]){_SEP}({_YEAR})(?!\d)"),
    re.compile(rf"(?<!\d)(0?[1-9]|1[0-2]){_SEP}({_YEAR})(?!\d)"),
)
_YEAR_FIRST_PATTERNS = (
    re.compile(rf"(?<!\d)({_YEAR}){_SEP}(0?[1-9]|1[0-2]){_SEP}(?:0?[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(rf"(?<!\d)({_YEAR}){_SEP}(0?[1-9]|1[0-2])(?!\d)"),
)
_DIGIT_RUN = re.compile(r"(?<!\d)\d+(?!\d)")
_YEAR_ONLY = re.compile(rf"(?<!\d){_YEAR}(?!\d)")


def _month_year_from_digit_run(run: str) -> tuple[str, str] | None:
    """Read (month, year) out of a date written without separators."""

    if len(run) == 6:
        if _YEAR_ONLY.fullmatch(run[2:]) and 1 <= int(run[:2]) <= 12:
            return run[:2], run[2:]
        if _YEAR_ONLY.fullmatch(run[:4]) and 1 <= int(run[4:]) <= 12:
            return run[4:], run[:4]
        return None
    if len(run) == 8:
        if (
            _YEAR_ONLY.fullmatch(run[4:])
            and 1 <= int(run[:2]) <= 31
            and 1 <= int(run[2:4]) <= 12
        ):
            return run[2:4], run[4:]
        if (
            _YEAR_ONLY.fullmatch(run[:4])
            and 1 <= int(run[4:6]) <= 12
            and 1 <= int(run[6:]) <= 31
        ):
            return run[4:6], run[:4]
    return None


def normalize_month_label(raw: Any) -> str:
    """Render any month label the model produced as a single "MM/YYYY" form."""

    text = str(raw or "").strip()
    if not text:
        return text

    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"{int(match.group(1)):02d}/{match.group(2)}"
    for pattern in _YEAR_FIRST_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"{int(match.group(2)):02d}/{match.group(1)}"

    for run in _DIGIT_RUN.findall(text):
        if len(run) in (6, 8) and (found := _month_year_from_digit_run(run)):
            return f"{int(found[0]):02d}/{found[1]}"
    return text


def month_sort_key(label: str) -> tuple[int, int]:
    """Chronological sort key for a "MM/YYYY" label."""

    match = re.fullmatch(r"(\d{1,2})/(\d{4})", str(label or "").strip())
    if not match:
        return (9999, 99)
    return (int(match.group(2)), int(match.group(1)))


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Scale every VND amount from triệu đồng to đồng, in place."""

    for row in result.get("du_no_hien_tai") or []:
        if isinstance(row, dict):
            row["vnd"] = scale_amount(row.get("vnd"), TRIEU_VND)

    for row in result.get("du_no_12_thang") or []:
        if isinstance(row, dict):
            row["thang"] = normalize_month_label(row.get("thang"))
            for key in ("du_no_vay", "du_no_the", "tong_du_no"):
                row[key] = scale_amount(row.get(key), TRIEU_VND)

    for row in result.get("cam_ket_ngoai_bang") or []:
        if not isinstance(row, dict):
            continue
        currency = str(row.get("loai_tien") or "").strip().upper()
        if currency in VND_CURRENCY_CODES:
            row["gia_tri"] = scale_amount(row.get("gia_tri"), TRIEU_VND)

    return result


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    """First numeric value among ``keys``, or None when every one is missing."""

    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def merge_debt_series(
    extractions: Iterable[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge per-file S10A extractions into one ascending monthly debt series."""

    by_month: dict[str, dict[str, Any]] = {}
    for filename, extraction in extractions:
        if not isinstance(extraction, dict):
            continue
        for row in extraction.get("du_no_12_thang") or []:
            if not isinstance(row, dict):
                continue
            month = normalize_month_label(row.get("thang"))
            if not month:
                continue
            value = _first_number(row, "tong_du_no", "du_no_vay")
            entry = by_month.setdefault(
                month, {"thang": month, "du_no": None, "nguon": []}
            )
            if entry["du_no"] is None and value is not None:
                entry["du_no"] = value
            if filename and filename not in entry["nguon"]:
                entry["nguon"].append(filename)

    return [
        entry
        for entry in sorted(by_month.values(), key=lambda e: month_sort_key(e["thang"]))
        if entry["du_no"] is not None
    ]


def extract_cic_s10a_structured_data(
    chain: Any,
    filename: str,
    content: str,
    path: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Run the extraction chain and validate its shape. Never raises."""

    return run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No CIC S10A extraction LLM configured.",
        normalize_amounts,
    )