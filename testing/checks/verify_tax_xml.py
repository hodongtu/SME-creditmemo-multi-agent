"""E-tax XML filings are read from their own indicator codes, not OCR'd.

An XML filing states the figures with the codes the form defines, so it removes
both the OCR layer and the model reading a table. The checks below insist on the
two things that make that safe: the year columns never merge, and a filing whose
own arithmetic fails is reported rather than returned.

Runs against the samples in docs/, which is gitignored — on a fresh clone this
reports missing data rather than failing.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.document_classification import discover_documents  # noqa: E402
from src.agents.financial_ratio_calculator import FinancialRatioCalculator  # noqa: E402
from src.utils.common import SUPPORTED_EXTENSIONS  # noqa: E402
from src.utils.extractors import extract_document_text  # noqa: E402
from src.utils.tax_xml import parse_tax_xml  # noqa: E402

BCTC = REPO / "docs" / "BCTC_sample.xml"
TKT = REPO / "docs" / "TKT_sample.xml"
for sample in (BCTC, TKT):
    if not sample.is_file():
        raise FileNotFoundError(sample)

fails = []


def check(name, ok, detail=""):
    print(f"   {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


print("1. .xml được nhận, và file lạ được BÁO chứ không bỏ im")
check(".xml trong SUPPORTED_EXTENSIONS", ".xml" in SUPPORTED_EXTENSIONS)
import tempfile, os  # noqa: E402
tmp = tempfile.mkdtemp()
for name in ("a.xml", "b.docx", ".DS_Store", "c.pdf"):
    Path(tmp, name).write_text("x")
found = {Path(f).name for f in discover_documents([tmp])}
check("nhận .xml và .pdf", found == {"a.xml", "c.pdf"}, str(sorted(found)))

print("\n2. BCTC — đọc đúng số, hai cột năm KHÔNG lẫn nhau")
bctc = parse_tax_xml(str(BCTC))
check("nhận ra biểu mẫu", bctc.kind == "bctc" and not bctc.error, bctc.error[:60])
ex = bctc.bctc_extraction
by_label = {li["label"]: li["values"] for li in ex["balance_sheet"]["line_items"]}
assets = by_label["TỔNG CỘNG TÀI SẢN"]
check("SoCuoiNam -> Năm 2025 = 50.226.331.878",
      assets.get("Năm 2025") == 50226331878.0, f"{assets.get('Năm 2025'):,.0f}")
check("SoDauNam -> Năm 2024 = 47.704.469.267",
      assets.get("Năm 2024") == 47704469267.0, f"{assets.get('Năm 2024'):,.0f}")
check("hai cột khác nhau (không bị gom phẳng)",
      assets.get("Năm 2025") != assets.get("Năm 2024"))
income = {li["label"]: li["values"] for li in ex["income_statement"]["line_items"]}
check("KQKD NamNay -> 2025, không lấy nhầm NamTruoc",
      income["Doanh thu thuần về bán hàng và cung cấp dịch vụ"]["Năm 2025"] == 62116063780.0)

print("\n3. Hình dạng JSON không đổi so với bản LLM")
REQUIRED = {"document_type", "reporting_period", "audit_opinion", "balance_sheet",
            "income_statement", "cash_flow_statement", "notes_summary"}
check("đủ khoá bắt buộc", REQUIRED <= set(ex), str(sorted(REQUIRED - set(ex))))
line = ex["balance_sheet"]["line_items"][0]
check("mỗi dòng có label/code/values/page",
      set(line) == {"label", "code", "values", "page"}, str(sorted(line)))

print("\n4. KHÔNG phát mã TT133 (tránh va chạm với bảng mã TT200)")
codes = {li["code"] for st in ("balance_sheet", "income_statement", "cash_flow_statement")
         for li in ex[st]["line_items"]}
check("mọi code đều None", codes == {None}, str(codes))

print("\n5. Nhãn khớp được vào metric — tổng tài sản KHÔNG thành tài sản dài hạn")
ym = FinancialRatioCalculator().extract_yearly_metrics([{"bctc_extraction": ex}])
check("total_assets 2025 = tổng tài sản thật",
      ym["Năm 2025"]["total_assets"] == 50226331878.0, f"{ym['Năm 2025']['total_assets']:,.0f}")
for key, want in (("net_revenue", 62116063780.0), ("cogs", 57336732353.0),
                  ("gross_profit", 4779331427.0), ("equity", 5434126183.0)):
    check(f"{key} 2025", ym["Năm 2025"].get(key) == want, f"{ym['Năm 2025'].get(key):,.0f}")
check("B01a không có tài sản ngắn hạn -> KHÔNG bịa ra",
      "current_assets" not in ym["Năm 2025"])
check("B01a không tách vay ngắn/dài hạn -> KHÔNG bịa ra",
      "short_term_debt" not in ym["Năm 2025"] and "long_term_debt" not in ym["Năm 2025"])

print("\n6. Tờ khai GTGT")
vat = parse_tax_xml(str(TKT))
check("nhận ra tờ khai", vat.kind == "vat" and not vat.error, vat.error[:60])
check("quý 1/2026 -> 3 tháng", sorted(vat.vat_revenue) == ["01/2026", "02/2026", "03/2026"])
total = sum(v for v, _ in vat.vat_revenue.values())
check("tổng 3 tháng = ct34 = 1.530.258.333", abs(total - 1530258333) < 1, f"{total:,.0f}")
check("tờ khai quý -> đánh dấu ước lượng", all(est for _, est in vat.vat_revenue.values()))

print("\n7. File không well-formed vẫn đọc được")
check("TKT_sample.xml có tiền tố '<Image '",
      Path(TKT).read_text(encoding="utf-8")[:7] == "<Image ")
check("vẫn parse được", vat.kind == "vat")

print("\n8. Số liệu mâu thuẫn -> BÁO, không trả về")
broken = Path(tempfile.mktemp(suffix=".xml"))
raw = Path(BCTC).read_text(encoding="utf-8")
broken.write_text(raw.replace("<ct500>50226331878</ct500>", "<ct500>99999999999</ct500>"), encoding="utf-8")
bad = parse_tax_xml(str(broken))
check("bảng cân đối không cân -> có error", bool(bad.error), bad.error[:70])
check("và KHÔNG trả về bản ghi", bad.bctc_extraction is None)
os.remove(broken)

print("\n9. Biểu mẫu lạ -> nói rõ lý do, không rỗng im lặng")
other = Path(tempfile.mktemp(suffix=".xml"))
other.write_text('<?xml version="1.0"?><root><a>1</a></root>', encoding="utf-8")
unknown = parse_tax_xml(str(other))
check("có error kèm lý do", bool(unknown.error), unknown.error[:60])
check("vẫn đọc được thành văn bản", "a: 1" in extract_document_text(str(other)))
os.remove(other)

print("\n10. XML thiếu thuyết minh — phải nhìn thấy được")
check("notes_summary rỗng nhưng CÓ mặt", ex["notes_summary"]["accounting_policies"] == "")
check("ghi rõ trong extraction_notes",
      any("thuyết minh" in n for n in ex["extraction_notes"]))
check("ghi rõ B01a không tách ngắn/dài hạn",
      any("ngắn hạn/dài hạn" in n for n in ex["extraction_notes"]))

print("\n11. bctc_extraction nhận cả ba loại, output cùng hình dạng")
from src.agents.bctc_extraction import extract_bctc_structured_data, REQUIRED_TOP_LEVEL_KEYS  # noqa: E402
rec, err = extract_bctc_structured_data(None, "BCTC_sample.xml", "", str(BCTC))
check("XML -> có bản ghi, không cần LLM", rec is not None and not err, err[:50])
check("đủ khoá bắt buộc", REQUIRED_TOP_LEVEL_KEYS <= set(rec or {}))
check("mỗi dòng vẫn {label, code, values, page}",
      set(rec["balance_sheet"]["line_items"][0]) == {"label", "code", "values", "page"})
# PDF/Excel không được đụng tới: không có chain thì phải báo đúng lỗi cũ
_, err_pdf = extract_bctc_structured_data(None, "BCTC.pdf", "text", "/x/BCTC.pdf")
_, err_xls = extract_bctc_structured_data(None, "BCTC.xlsx", "text", "/x/BCTC.xlsx")
check("PDF vẫn đi đường LLM y như cũ", err_pdf == "No BCTC extraction LLM configured.", err_pdf)
check("Excel vẫn đi đường LLM y như cũ", err_xls == "No BCTC extraction LLM configured.", err_xls)
_, err_bad = extract_bctc_structured_data(None, "la.xml", "text", str(BCTC).replace("BCTC_sample", "khong_co"))
check("XML không đọc được -> lùi về đường LLM, không mất tài liệu",
      err_bad == "No BCTC extraction LLM configured.", err_bad[:50])

print("\n12. Nguồn đáng tin hơn thắng, không trộn nguồn trong một năm")
import copy  # noqa: E402
from src.agents.financial_ratio_calculator import FinancialRatioCalculator as _C  # noqa: E402
_llm = copy.deepcopy(rec)
for _st in ("balance_sheet", "income_statement", "cash_flow_statement"):
    for _li in _llm[_st]["line_items"]:
        _li["values"] = {y: v * 3 for y, v in _li["values"].items()}
_c = _C()
_xo = _c.extract_yearly_metrics([{"bctc_extraction": rec, "bctc_extraction_source": "xml"}])
_both = _c.extract_yearly_metrics([
    {"bctc_extraction": _llm, "bctc_extraction_source": "llm"},
    {"bctc_extraction": rec, "bctc_extraction_source": "xml"}])
_shared = set(_xo["Năm 2025"]) & set(_both["Năm 2025"])
_bad = [k for k in _shared if _xo["Năm 2025"][k] != _both["Năm 2025"][k]]
check(f"{len(_shared)} chỉ tiêu chung đều lấy từ XML", not _bad, str(_bad))
check("thứ tự nguồn có ý nghĩa", _C.SOURCE_RANK["xml"] > _C.SOURCE_RANK["llm"])

print("\n13. VAT: gộp nguồn theo đúng thứ tự tin cậy")
from src.agents.vat_revenue import merge_vat_series  # noqa: E402
_agent = {"01/2026": (100.0, True), "02/2026": (200.0, False)}
_xml = {"01/2026": (150.0, True), "02/2026": (250.0, True), "03/2026": (300.0, False)}
_m = merge_vat_series(_agent, _xml)
check("cùng là ước lượng -> XML thắng", _m["01/2026"] == (150.0, True))
check("số tháng THẬT không bị ước lượng đè", _m["02/2026"] == (200.0, False))
check("tháng chỉ XML có -> giữ", _m["03/2026"] == (300.0, False))

print("\n" + "=" * 68)
if fails:
    print("❌ HỎNG:", *fails, sep="\n   - ")
    sys.exit(1)
print("✅ Đọc XML khai thuế từ mã chỉ tiêu, không lẫn cột, không bịa chỉ tiêu")
