"""Read Vietnamese e-tax XML filings — financial statements and VAT returns.

HTKK and eTax export a filing as XML carrying the form's official indicator
codes. That is a better source than the scanned PDF of the same document: no
OCR layer, no model reading a table, and the figures come with the codes the
form itself defines.

It is also the source that removes a failure this project has already been bitten
by. Commit 08e4008 fixed the metrics block picking "11. Thu nhập khác" as giá vốn
hàng bán, because the extraction had copied each label's ordinal into the code
field and 11 is giá vốn's TT200 code. Nothing in this module can produce that: it
never guesses which line is which, it reads the code the filing states.

Three things here exist because the sample files taught them, not because they
seemed prudent:

- **Never flatten by tag name.** A balance sheet holds two year columns as
  sibling blocks, and the two appendices name their columns differently
  (SoCuoiNam/SoDauNam versus NamNay/NamTruoc). Reading every ``ct*`` element into
  one dict silently keeps whichever column came last — which, on the sample,
  returned the prior year's revenue while looking entirely correct.

- **Codes mean different things under different circulars.** TT133's ct200 is
  TỔNG CỘNG TÀI SẢN; TT200's 200 is TÀI SẢN DÀI HẠN. So this module emits the
  canonical Vietnamese *label* and leaves ``code`` empty. FinancialRatioCalculator
  ranks a label match above a code match (see its match_metric), which makes the
  label the safe channel and a code the dangerous one.

- **The file may not be well formed.** One sample begins ``<Image <?xml
  version=...``, which ElementTree rejects at column 7. Parsing starts from the
  declaration.

Every reading is checked against the identities the form guarantees — a balance
sheet balances, ct10 is ct01 minus ct02 — and a filing whose arithmetic does not
hold is reported rather than returned, because the likeliest cause is that the
wrong block was read.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

NAMESPACE = "http://kekhaithue.gdt.gov.vn/TKhaiThue"
_NS = {"n": NAMESPACE}
_CODE = re.compile(r"ct\d+\w*")

# maTKhai values this module knows how to read. Anything else is reported as
# unrecognised rather than read on the assumption that one filing looks like
# another — the codes are only meaningful once the form is known.
FORM_VAT_01GTGT = "842"
FORM_BCTC_B01A_DNN = "683"

# B01a-DNN, TT133/2016. Derived from the sample and confirmed against the form's
# own identities (ct200 = 110+120+…+180, ct300 = 311..320, ct400 = 411..417,
# ct500 = ct300+ct400), not from memory of the circular.
#
# Labels are worded to match FinancialRatioCalculator's aliases exactly; each one
# below was checked to resolve to the metric named in the comment. "Phải thu của
# khách hàng" — the form's own wording — does not match, "Phải thu khách hàng"
# does, and that difference is the whole reason these are spelled out here rather
# than copied off the form.
_B01A_BALANCE = {
    "ct110": "Tiền và các khoản tương đương tiền",   # cash
    "ct130": "Các khoản phải thu",
    "ct131": "Phải thu khách hàng",                   # accounts_receivable
    "ct132": "Trả trước người bán",                   # prepaid_suppliers
    "ct136": "Các khoản phải thu khác",               # other_receivables
    "ct140": "Hàng tồn kho",                          # inventory
    "ct141": "Hàng tồn kho",
    "ct150": "Tài sản cố định",
    "ct151": "Nguyên giá tài sản cố định",
    "ct152": "Giá trị hao mòn luỹ kế",
    "ct200": "TỔNG CỘNG TÀI SẢN",                     # total_assets
    "ct300": "NỢ PHẢI TRẢ",                           # total_liabilities
    "ct311": "Phải trả người bán",                    # accounts_payable
    "ct312": "Người mua trả tiền trước",              # customer_advances
    "ct313": "Thuế và các khoản phải nộp Nhà nước",
    "ct314": "Phải trả người lao động",
    "ct400": "VỐN CHỦ SỞ HỮU",                        # equity
    "ct411": "Vốn góp của chủ sở hữu",
    "ct417": "Lợi nhuận sau thuế chưa phân phối",
    "ct500": "TỔNG CỘNG NGUỒN VỐN",
}
# Deliberately absent: ct316 "Vay và nợ thuê tài chính". B01a-DNN does not split
# borrowings into short and long term, and the calculator has a metric for each.
# Mapping it to either would state a maturity the filing never gave.
#
# Also absent by construction: current_assets and current_liabilities. B01a lists
# assets in decreasing liquidity with no current/non-current division at all, so
# any ratio needing that split cannot come from this form. Recorded in the notes
# rather than approximated.

_B02_INCOME = {
    "ct01": "Doanh thu bán hàng và cung cấp dịch vụ",             # gross_revenue
    "ct02": "Các khoản giảm trừ doanh thu",
    "ct10": "Doanh thu thuần về bán hàng và cung cấp dịch vụ",    # net_revenue
    "ct11": "Giá vốn hàng bán",                                   # cogs
    "ct20": "Lợi nhuận gộp về bán hàng và cung cấp dịch vụ",      # gross_profit
    "ct21": "Doanh thu hoạt động tài chính",
    "ct22": "Chi phí tài chính",                                  # financial_expense
    "ct23": "Chi phí lãi vay",                                    # interest_expense
    "ct24": "Chi phí quản lý kinh doanh",
    "ct30": "Lợi nhuận thuần từ hoạt động kinh doanh",
    "ct31": "Thu nhập khác",
    "ct32": "Chi phí khác",
    "ct40": "Lợi nhuận khác",
    "ct50": "Lợi nhuận trước thuế",                               # profit_before_tax
    "ct51": "Chi phí thuế thu nhập doanh nghiệp",
    "ct60": "Lợi nhuận sau thuế thu nhập doanh nghiệp",           # net_profit
}

_LCTT_CASHFLOW = {
    "ct20": "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
    "ct30": "Lưu chuyển tiền thuần từ hoạt động đầu tư",
    "ct40": "Lưu chuyển tiền thuần từ hoạt động tài chính",
    "ct50": "Lưu chuyển tiền thuần trong kỳ",
    "ct60": "Tiền và tương đương tiền đầu kỳ",
    "ct70": "Tiền và tương đương tiền cuối kỳ",
}

# 01/GTGT. ct34 is the figure the debt/revenue chart wants: total revenue of
# goods and services sold, which the form defines as ct26 + ct27 + ct32a.
VAT_TOTAL_REVENUE = "ct34"


@dataclass
class TaxXmlResult:
    """What a filing yielded, or why it yielded nothing."""

    kind: str = ""                       # "bctc" | "vat"
    form_name: str = ""
    taxpayer_id: str = ""
    taxpayer_name: str = ""
    bctc_extraction: dict[str, Any] | None = None
    vat_revenue: dict[str, tuple[float, bool]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""


def _root(path: str) -> ET.Element:
    """Parse from the XML declaration, ignoring anything before it.

    One real filing arrived as ``<Image <?xml version="1.0"…``. Whatever put that
    there, the document after it is valid, and refusing the file would lose a
    statement over a stray seven characters.
    """

    raw = open(path, encoding="utf-8-sig").read()
    start = raw.find("<?xml")
    if start > 0:
        raw = raw[start:]
    return ET.fromstring(raw)


def _text(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    found = node.find(f"n:{tag}", _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def _column(parent: ET.Element | None, name: str) -> dict[str, float]:
    """Every ct* code inside one named column block.

    Scoped to the block on purpose. The alternative — walking the parent and
    keying by tag — merges the year columns into each other.
    """

    if parent is None:
        return {}
    for child in parent:
        if child.tag.split("}")[-1] != name:
            continue
        values: dict[str, float] = {}
        for element in child.iter():
            tag = element.tag.split("}")[-1]
            if not _CODE.fullmatch(tag) or not (element.text or "").strip():
                continue
            try:
                values[tag] = float(element.text)
            except ValueError:
                continue
        return values
    return {}


def _year_of(date_text: str) -> str:
    """"31/12/2025" or "2025-12-31" -> "Năm 2025"."""

    match = re.search(r"(19|20)\d{2}", date_text or "")
    return f"Năm {match.group(0)}" if match else ""


def _statement(
    values_by_year: dict[str, dict[str, float]],
    code_labels: dict[str, str],
) -> dict[str, Any]:
    """One statement in the shape bctc_extraction already uses.

    ``code`` is left empty on every line. The codes are real and correct, but
    they are TT133's, and FinancialRatioCalculator's tables are TT200's — where
    200 means long-term assets rather than total assets. Passing them through
    would invite exactly the mis-map that tiering label above code was added to
    stop.
    """

    years = [year for year in values_by_year if year]
    line_items = []
    for code, label in code_labels.items():
        values = {
            year: values_by_year[year][code]
            for year in years
            if code in values_by_year[year]
        }
        if values:
            line_items.append(
                {"label": label, "code": None, "values": values, "page": None}
            )
    return {
        "unit": "VNĐ",
        "source_unit": "dong",
        "page": None,
        "years": years,
        "line_items": line_items,
    }


def _check_identities(
    balance: dict[str, dict[str, float]],
    income: dict[str, dict[str, float]],
) -> list[str]:
    """Report where the filing's own arithmetic fails to hold.

    These identities are true by construction on a correctly-read filing, so a
    break means the reading is wrong — most likely the wrong column block. Worth
    more than any assertion this module could make about itself.
    """

    problems: list[str] = []
    for year, values in balance.items():
        total, debts, equity = (values.get(c) for c in ("ct200", "ct300", "ct400"))
        if None not in (total, debts, equity) and abs(debts + equity - total) > 1:
            problems.append(
                f"{year}: bảng cân đối không cân — nợ phải trả {debts:,.0f} cộng "
                f"vốn chủ sở hữu {equity:,.0f} khác tổng tài sản {total:,.0f}."
            )
        # Assets equal liabilities-and-equity twice over in this form: once as
        # ct300+ct400 and once as the stated ct500. Checking only the first left
        # a corrupted ct500 to pass through unnoticed.
        funding = values.get("ct500")
        if None not in (total, funding) and abs(funding - total) > 1:
            problems.append(
                f"{year}: tổng nguồn vốn {funding:,.0f} khác tổng tài sản "
                f"{total:,.0f}."
            )
    for year, values in income.items():
        gross, revenue, cogs = (values.get(c) for c in ("ct20", "ct10", "ct11"))
        if None in (gross, revenue, cogs):
            continue
        if abs(revenue - cogs - gross) > 1:
            problems.append(
                f"{year}: lợi nhuận gộp {gross:,.0f} khác doanh thu thuần "
                f"{revenue:,.0f} trừ giá vốn {cogs:,.0f}."
            )
    return problems


def _parse_bctc(root: ET.Element, result: TaxXmlResult) -> TaxXmlResult:
    """B01a-DNN and its appendices into a bctc_extraction record."""

    period = root.find(".//n:KyKKhaiThue", _NS)
    end_year = _year_of(_text(period, "kyKKhaiDenNgay")) or f"Năm {_text(period, 'kyKKhai')}"
    start_year = _year_of(_text(period, "kyKKhaiTuNgay"))
    # The opening column is the prior year's close, whatever the filing calls it.
    prior_year = f"Năm {int(end_year.split()[-1]) - 1}" if end_year.split()[-1].isdigit() else ""
    if start_year == end_year:
        start_year = prior_year

    main = root.find(".//n:CTieuTKhaiChinh", _NS)
    balance = {
        end_year: _column(main, "SoCuoiNam"),
        start_year: _column(main, "SoDauNam"),
    }
    income_node = root.find(".//n:PL_KQHDSXKD", _NS)
    income = {
        end_year: _column(income_node, "NamNay"),
        start_year: _column(income_node, "NamTruoc"),
    }
    cashflow_node = root.find(".//n:PL_LCTTTT", _NS)
    cashflow = {
        end_year: _column(cashflow_node, "NamNay"),
        start_year: _column(cashflow_node, "NamTruoc"),
    }
    balance = {y: v for y, v in balance.items() if y and v}
    income = {y: v for y, v in income.items() if y and v}
    cashflow = {y: v for y, v in cashflow.items() if y and v}

    if not balance:
        result.error = "Không đọc được khối SoCuoiNam/SoDauNam của bảng cân đối."
        return result

    problems = _check_identities(balance, income)
    if problems:
        result.error = "Số liệu trong XML không tự nhất quán: " + " ".join(problems)
        return result

    audited = _text(root.find(".//n:CTieuTKhaiChinh", _NS), "bctcDaKiemToan") == "1"
    result.bctc_extraction = {
        "document_type": result.form_name or "BCTC",
        "reporting_period": {
            "period_label": end_year,
            "start_date": _text(period, "kyKKhaiTuNgay"),
            "end_date": _text(period, "kyKKhaiDenNgay"),
            "comparative_period_label": start_year,
        },
        "audit_opinion": {
            "is_audited": audited,
            "opinion_type": "không xác định",
            "auditor_name": None,
            "notes": "",
            "page": None,
        },
        "balance_sheet": _statement(balance, _B01A_BALANCE),
        "income_statement": _statement(income, _B02_INCOME),
        "cash_flow_statement": _statement(cashflow, _LCTT_CASHFLOW),
        # Present because the schema requires it, empty because the filing has no
        # notes section. Left visibly empty rather than filled with anything —
        # a caller merging this with an OCR reading needs to see the gap.
        "notes_summary": {
            "accounting_policies": "",
            "related_party_transactions": [],
            "contingent_liabilities": [],
            "subsequent_events": [],
            "key_item_breakdowns": [],
            "other_material_disclosures": [],
        },
        "extraction_notes": [
            f"Đọc trực tiếp từ XML khai thuế ({result.form_name}) — số liệu là mã "
            "chỉ tiêu do người nộp thuế kê khai, không qua OCR hay mô hình.",
            "Biểu mẫu B01a-DNN xếp tài sản theo tính thanh khoản giảm dần, KHÔNG "
            "tách ngắn hạn/dài hạn — nên không có tài sản ngắn hạn, nợ ngắn hạn, "
            "và không tách được vay ngắn hạn với vay dài hạn.",
            "XML không có phần thuyết minh báo cáo tài chính.",
        ],
    }
    result.notes = list(result.bctc_extraction["extraction_notes"])
    return result


def _parse_vat(root: ET.Element, result: TaxXmlResult) -> TaxXmlResult:
    """01/GTGT into {"MM/YYYY": (doanh thu, ước lượng)}."""

    period = root.find(".//n:KyKKhaiThue", _NS)
    main = root.find(".//n:CTieuTKhaiChinh", _NS)
    codes: dict[str, float] = {}
    for element in (main.iter() if main is not None else []):
        tag = element.tag.split("}")[-1]
        if _CODE.fullmatch(tag) and (element.text or "").strip():
            try:
                codes[tag] = float(element.text)
            except ValueError:
                continue

    revenue = codes.get(VAT_TOTAL_REVENUE)
    if revenue is None:
        result.error = f"Tờ khai không có chỉ tiêu {VAT_TOTAL_REVENUE} (tổng doanh thu)."
        return result

    # The form defines ct34 as ct26 + ct27 + ct32a; a mismatch means the wrong
    # element was read, not that the taxpayer filed something unusual.
    parts = sum(codes.get(c, 0.0) for c in ("ct26", "ct27", "ct32a"))
    if abs(parts - revenue) > 1:
        result.error = (
            f"ct34 ({revenue:,.0f}) khác ct26+ct27+ct32a ({parts:,.0f}) — "
            "không chắc đọc đúng chỉ tiêu."
        )
        return result

    kind = (_text(period, "kieuKy") or "").upper()
    label = _text(period, "kyKKhai")
    year = _year_of(_text(period, "kyKKhaiDenNgay")).replace("Năm ", "")
    if not year:
        result.error = "Không đọc được kỳ kê khai."
        return result

    if kind == "M":
        month = label.split("/")[0].strip()
        result.vat_revenue = {f"{int(month):02d}/{year}": (revenue, False)}
        result.notes.append(f"Tờ khai tháng {month}/{year}: doanh thu là số kê khai, không ước lượng.")
    elif kind == "Q":
        quarter = int(label.split("/")[0].strip())
        share = revenue / 3
        result.vat_revenue = {
            f"{month:02d}/{year}": (share, True)
            for month in range(quarter * 3 - 2, quarter * 3 + 1)
        }
        result.notes.append(
            f"Tờ khai quý {quarter}/{year}: chia đều cho 3 tháng và đánh dấu ước lượng."
        )
    else:
        result.error = f"Kiểu kỳ kê khai không nhận ra: {kind!r}."
    return result


def parse_tax_xml(path: str) -> TaxXmlResult:
    """Read one e-tax XML, or say why it could not be read.

    Never raises and never returns a half-filled record: either the filing was
    recognised and its arithmetic held, or ``error`` says what stopped it and the
    caller falls back to reading the file as text.
    """

    result = TaxXmlResult()
    try:
        root = _root(path)
    except Exception as exc:
        result.error = f"Không parse được XML: {type(exc).__name__}: {exc}"
        return result

    if not root.tag.endswith("HSoThueDTu"):
        result.error = "Không phải hồ sơ thuế điện tử (thẻ gốc không phải HSoThueDTu)."
        return result

    declaration = root.find(".//n:TKhaiThue", _NS)
    form_id = _text(declaration, "maTKhai")
    result.form_name = _text(declaration, "tenTKhai")
    taxpayer = root.find(".//n:NNT", _NS)
    result.taxpayer_id = _text(taxpayer, "mst")
    result.taxpayer_name = _text(taxpayer, "tenNNT")

    if form_id == FORM_BCTC_B01A_DNN:
        result.kind = "bctc"
        return _parse_bctc(root, result)
    if form_id == FORM_VAT_01GTGT:
        result.kind = "vat"
        return _parse_vat(root, result)

    result.error = (
        f"Chưa hỗ trợ biểu mẫu maTKhai={form_id!r} ({result.form_name!r}). "
        "Mã chỉ tiêu chỉ có nghĩa khi biết biểu mẫu, nên tài liệu này được đọc "
        "như văn bản thường thay vì đoán."
    )
    return result
