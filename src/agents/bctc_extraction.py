"""LLM extraction of full BCTC (báo cáo tài chính) bundles into structured JSON.

Runs once per document whose matrix type is flagged ``bctc_extraction``
(see src/matrix/document_matrix.yaml and ``is_bctc_type``),
turning noisy raw OCR text into a compact structured record — report type, period,
audit opinion, the 3 core statements, and a bounded summary of the notes section —
so FINANCIAL_ANALYSIS_AGENT can consume clean data instead of a multi-page raw dump.

Money is read as printed and scaled here, the same arrangement the other four
extraction passes use. This pass was the last one asking the model to convert
units itself, which put the arithmetic in the one place nothing could check it:
a statement printed in triệu đồng came through a thousandfold short whenever the
model missed the unit line, and the ``unit`` field still said "VNĐ" because the
schema said so. ``normalize_amounts`` now does the conversion from each block's
own ``source_unit``, and ``FinancialRatioCalculator.detect_unit_anomalies``
catches what slips past by comparing year to year.
"""

from __future__ import annotations

import re
from typing import Any

from src.utils.tax_xml import parse_tax_xml
from src.agents.structured_extraction import (
    build_extraction_chain,
    resolve_money_multiplier,
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

BCTC_EXTRACTION_SYSTEM_PROMPT = """
Bạn trích xuất dữ liệu có cấu trúc từ văn bản OCR thô của một bộ Báo cáo tài
chính (BCTC) doanh nghiệp Việt Nam, phục vụ thẩm định tín dụng SME.

YÊU CẦU QUAN TRỌNG NHẤT — TRÍCH XUẤT ĐẦY ĐỦ:
Với mỗi bảng (cân đối kế toán, kết quả kinh doanh, lưu chuyển tiền tệ), liệt kê
ĐẦY ĐỦ TỪNG DÒNG chỉ tiêu xuất hiện trong bảng, theo đúng thứ tự trong tài liệu
— cả dòng tổng hợp lẫn dòng chi tiết. Một bảng cân đối kế toán đầy đủ thường có
40-60 dòng; nếu bạn chỉ trả về vài dòng tổng hợp là SAI. Tuyệt đối KHÔNG tóm
lược, KHÔNG chọn lọc "các chỉ tiêu quan trọng", KHÔNG bỏ dòng nào có số liệu.

Chỉ dùng dữ kiện có trong văn bản nguồn. Không tự suy diễn hay bịa số liệu.
Nếu một trường không xác định được, để giá trị null (hoặc mảng/chuỗi rỗng),
KHÔNG bỏ qua trường đó.

QUY TẮC ĐƠN VỊ TIỀN:
- GHI ĐÚNG CON SỐ IN TRÊN GIẤY, chỉ bỏ dấu phân cách hàng nghìn. TUYỆT ĐỐI
  KHÔNG tự nhân lên triệu/tỷ, KHÔNG tự quy đổi đơn vị — việc quy đổi do chương
  trình làm. Bảng đề "Đơn vị tính: triệu đồng" và in 240.800 thì trả 240800,
  KHÔNG phải 240800000000.
- Giữ đúng dấu: âm cho khoản mục ghi âm hoặc đặt trong ngoặc.
- "source_unit" của MỖI bảng ghi đơn vị in ở đầu chính bảng đó:
  "dong" | "trieu dong" | "ty dong". Bảng không chú thích đơn vị nào thì để
  "dong". Thuyết minh có đơn vị riêng thì ghi vào source_unit của
  "notes_summary" — đừng chép đơn vị của bảng chính sang.

Văn bản OCR có các mốc "--- Page N ---" đánh dấu ranh giới trang, dùng để trích
dẫn nguồn cho người đọc. Với MỖI BẢNG, bắt buộc điền "page" của bảng đó (số
trang nơi bảng bắt đầu). Với từng dòng chỉ tiêu, điền "page" nếu xác định được,
không xác định được thì để null — nhưng việc này TUYỆT ĐỐI KHÔNG được làm giảm
số dòng bạn trích xuất; đầy đủ dòng quan trọng hơn đầy đủ số trang. Không bịa
số trang.

Cấu trúc "notes_summary" tóm tắt phần Thuyết minh báo cáo tài chính — đây
thường là phần dài nhất, chỉ giữ lại nội dung có ý nghĩa cho thẩm định tín
dụng (không chép lại nguyên văn): chính sách kế toán trọng yếu, giao dịch bên
liên quan, nợ tiềm tàng, sự kiện sau ngày kết thúc kỳ, và chi tiết các khoản
mục lớn (vay nợ, phải thu/phải trả lớn, hàng tồn kho...). Dùng
"other_material_disclosures" cho bất kỳ điểm quan trọng nào khác không thuộc
các mục trên — không được bỏ sót thông tin trọng yếu chỉ vì nó không khớp một
mục có sẵn.

QUY TẮC GHI KỲ BÁO CÁO — áp dụng cho MỌI nhãn kỳ trong toàn bộ JSON:
ghi đúng dạng "Năm YYYY", không dùng dạng nào khác.
- "31/12/2024", "01/01/2024 - 31/12/2024", "Quý 4/2024", "122024" -> "Năm 2024"
  (khoảng thời gian thì lấy năm KẾT THÚC).
- Cột đặt tên theo vị trí ("Số cuối kỳ", "Số đầu kỳ", "Kỳ này", "Kỳ trước",
  "Cuối năm", "Đầu năm") thì phải suy ra năm thật từ kỳ báo cáo của chính tài
  liệu: cột cuối kỳ là năm báo cáo, cột đầu kỳ là năm liền trước. BCTC kỳ
  31/12/2024 thì "Số cuối kỳ" -> "Năm 2024", "Số đầu kỳ" -> "Năm 2023".
Quy tắc này áp cho period_label, comparative_period_label, mảng "years" của
từng bảng, và ĐẶC BIỆT là khoá của "values" trong mỗi dòng chỉ tiêu. Hai bảng
ghi cùng một năm theo hai cách khác nhau sẽ bị tính thành hai cột riêng và làm
sai tăng trưởng.

Trả về CHÍNH XÁC JSON theo schema sau, không thêm text nào khác:
{{
  "document_type": "BCTC hợp nhất | BCTC riêng lẻ | không xác định",
  "reporting_period": {{
    "period_label": "Năm YYYY",
    "start_date": "YYYY-MM-DD hoặc null",
    "end_date": "YYYY-MM-DD hoặc null",
    "comparative_period_label": "Năm YYYY, hoặc null"
  }},
  "audit_opinion": {{
    "is_audited": true hoặc false,
    "opinion_type": "chấp nhận toàn phần | ngoại trừ | không đủ cơ sở | trái ngược | không xác định",
    "auditor_name": "tên công ty kiểm toán hoặc null",
    "notes": "ghi chú ngắn nếu có, hoặc chuỗi rỗng",
    "page": <số trang nguyên hoặc null>
  }},
  "balance_sheet": {{
    "unit": "VNĐ",
    "source_unit": "dong | trieu dong | ty dong — đơn vị IN ở đầu bảng, mặc định dong",
    "page": <số trang nơi bảng bắt đầu, bắt buộc nếu xác định được>,
    "years": ["các kỳ xuất hiện trong bảng, mỗi kỳ ghi dạng 'Năm YYYY'"],
    "line_items": [
      {{"label": "tên chỉ tiêu", "code": "mã số nếu có hoặc null",
        "values": {{"Năm YYYY": <số như in trên giấy>}}, "page": <số trang nguyên hoặc null>}}
      // LIỆT KÊ HẾT MỌI DÒNG CỦA BẢNG, không rút gọn
    ]
  }},
  "income_statement": {{"unit": "VNĐ", "source_unit": "dong | trieu dong | ty dong",
                       "page": <số trang>, "years": [], "line_items": []}},
  "cash_flow_statement": {{"unit": "VNĐ", "source_unit": "dong | trieu dong | ty dong",
                          "page": <số trang>, "years": [], "line_items": []}},
  "notes_summary": {{
    "source_unit": "dong | trieu dong | ty dong — đơn vị IN trong thuyết minh, mặc định dong",
    "accounting_policies": "tóm tắt ngắn gọn hoặc chuỗi rỗng (kèm '(trang N)' ở cuối nếu xác định được)",
    "related_party_transactions": [
      {{"counterparty": "...", "nature": "...", "amount": <số như in trên giấy, hoặc null>, "year": "...",
        "page": <số trang nguyên hoặc null>}}
    ],
    "contingent_liabilities": ["... (trang N)"],
    "subsequent_events": ["... (trang N)"],
    "key_item_breakdowns": [
      {{"item": "vd: Vay và nợ thuê tài chính", "breakdown": "...", "amount": <số như in trên giấy, hoặc null>,
        "page": <số trang nguyên hoặc null>}}
    ],
    "other_material_disclosures": ["... (trang N)"]
  }},
  "extraction_notes": ["ghi chú về dữ liệu thiếu, không chắc chắn, hoặc OCR kém"]
}}

Với các mục dạng chuỗi tự do (contingent_liabilities, subsequent_events,
other_material_disclosures, accounting_policies): nếu xác định được số trang,
thêm "(trang N)" vào cuối chuỗi đó; nếu không, để nguyên chuỗi không có phần
này — không ghi "(trang null)" hay tương tự.
"""


def build_bctc_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain, mirroring the document classifier chain."""

    return build_extraction_chain(BCTC_EXTRACTION_SYSTEM_PROMPT, llm)


# Guard on digits rather than word boundaries: \b fails between a letter and a
# digit, so "FY2024" would keep its raw form. The lookarounds still refuse to
# pick a year out of the middle of a longer number.
_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# Whole digit runs only. Matching a fixed width instead would carve a plausible
# date out of the middle of a longer number — an eight-digit window slides
# straight across a ten-digit tax code.
_DIGIT_RUN_PATTERN = re.compile(r"(?<!\d)\d+(?!\d)")
# Lengths a date can be written in without separators: MMYYYY/YYYYMM and
# DDMMYYYY/YYYYMMDD.
_SQUASHED_DATE_LENGTHS = (6, 8)

PERIOD_LABEL_PREFIX = "Năm "

# Columns named by position rather than by year. Vietnamese balance sheets label
# them "Số cuối kỳ"/"Số đầu kỳ": the closing column is the reporting year, the
# opening column is the year before it.
_PREVIOUS_PERIOD_MARKERS = (
    "ky truoc", "nam truoc", "nam ngoai", "dau ky", "dau nam", "so dau",
    "cung ky", "ky lien truoc",
)
_CURRENT_PERIOD_MARKERS = (
    "ky nay", "nam nay", "ky bao cao", "nam bao cao", "cuoi ky", "cuoi nam",
    "so cuoi", "nam hien tai", "ky hien tai", "ky hien hanh",
)


def _year_from_digit_run(run: str) -> str | None:
    """Read a year out of a date written without separators.

    "122024" is December 2024 and "31122024" is 31 December 2024, but
    "0104498100" is a tax code and "1234567" is an amount. Only 6- and 8-digit
    runs are considered, and only when the leftover digits form a plausible
    day/month — otherwise the run is left alone.
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
    """Read (current_year, previous_year) out of an extraction's own period block.

    This is the anchor positional labels resolve against. The comparative label
    is preferred for the prior year; failing that the year before the reporting
    year is a safe inference, since a comparative column in a Vietnamese annual
    report is the preceding financial year.
    """

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
        # Two spellings of one year collapsing together is the whole point;
        # keep the first non-null figure so a null column cannot erase a real
        # number that arrived under the other spelling.
        if key in normalized and normalized[key] is not None:
            continue
        normalized[key] = value
    return normalized


def normalize_extraction_periods(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize every period label in an extraction result, in place.

    Applied at the source so the stored JSON, the ratio calculator and the
    prompts all see one spelling, instead of each consumer having to defend
    itself.
    """

    # Resolved before anything is rewritten, since positional column labels
    # elsewhere in the document are interpreted against it.
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


# The three statements plus the notes each carry their own printed unit: a
# balance sheet in đồng sitting next to a note in triệu is ordinary in these
# bundles, so one unit for the whole document would be wrong for part of it.
_STATEMENT_KEYS = ("balance_sheet", "income_statement", "cash_flow_statement")


def _scale(value: Any, multiplier: int) -> Any:
    """Scale one figure to đồng, leaving anything that is not a number alone."""

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return round(value * multiplier, 2)


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using each block's own source_unit, in place.

    BCTC was the last pass that asked the model to do this arithmetic itself.
    That is a bad place for it: a statement printed in triệu đồng comes through
    a thousand-fold short whenever the model misses the unit line at the top of
    the table, and nothing downstream could tell that figure from a real one —
    the ``unit`` field said "VNĐ" because the schema said so, not because
    anybody checked. The four other passes already read the printed unit and
    scale here; this one now matches them.

    Both consumers of the result are protected by this: FINANCIAL_ANALYSIS_AGENT
    reads these statements directly, and ``financial_metrics`` turns them into
    the revenue figure the credit-need table divides by.
    """

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
                    period: _scale(value, multiplier)
                    for period, value in values.items()
                }

    notes = result.get("notes_summary")
    if isinstance(notes, dict):
        multiplier = resolve_money_multiplier(notes.get("source_unit"))
        for field in ("related_party_transactions", "key_item_breakdowns"):
            for entry in notes.get(field) or []:
                if isinstance(entry, dict):
                    entry["amount"] = _scale(entry.get("amount"), multiplier)
    return result


def extract_bctc_from_xml(path: str) -> tuple[dict[str, Any] | None, str]:
    """Read an e-tax XML filing, or say why it could not be read.

    Thin on purpose: the parsing lives in utils.tax_xml, and this is only the
    seam that lets bctc_extraction own all three input kinds. Returning the note
    rather than swallowing it keeps the fallback explainable.
    """

    result = parse_tax_xml(path)
    if result.error:
        return None, result.error
    if result.kind != "bctc":
        return None, f"XML là {result.kind or 'biểu mẫu khác'}, không phải báo cáo tài chính."
    return result.bctc_extraction, ""


def extract_bctc_structured_data(
    chain: Any,
    filename: str,
    content: str,
    path: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Turn one financial-statement document into the structured record.

    Three kinds of file arrive here and all three leave in the same shape. A PDF
    or a spreadsheet is text by the time it reaches this point and goes to the
    model, exactly as before. An e-tax XML does not: it states the figures
    against the form's own indicator codes, so those are read directly and no
    model sees the document at all.

    That is worth the branch because of what the model gets wrong on this
    document in particular. 08e4008 fixed the metrics block reading giá vốn hàng
    bán as 0.82 tỷ, because the extraction had copied each label's ordinal into
    the code field and "11. Thu nhập khác" then carried giá vốn's TT200 code. A
    filing cannot produce that: nothing is being recognised, only read.

    Never raises — returns (None, error_message) on any failure so the caller
    always has a clean signal to fall back to raw text for this document.
    """

    if (path or filename).lower().endswith(".xml"):
        record, note = extract_bctc_from_xml(path or filename)
        if record is not None:
            return record, ""
        # Unrecognised form, or one whose own arithmetic did not hold. Fall
        # through to the model on the flattened text rather than lose the
        # document — the reason is carried out so a run can be traced.
        if note:
            content = f"[XML không đọc trực tiếp được: {note}]\n\n{content}"

    result, error = run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No BCTC extraction LLM configured.",
    )
    if result is None:
        return None, error
    return normalize_amounts(normalize_extraction_periods(result)), ""
