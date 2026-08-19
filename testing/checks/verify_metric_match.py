"""The metrics block: pick the right statement line, and flag figures that contradict."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import json; from src.agents.financial_ratio_calculator import FinancialRatioCalculator as C

fails = []
def check(name, ok, detail=""):
    print(f"   {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok: fails.append(name)

# Synthetic fixture, no customer data. It reproduces the exact failure
# conditions: the extraction wrote each label's ordinal into the "code" field, so
# "11. Thu nhập khác" carries code 11 — giá vốn hàng bán's TT200 code — and
# "6. Doanh thu hoạt động tài chính" carries code 22, chi phí tài chính's. Checked:
# run through the old score comparison this fixture yields 0.80 and 179.00 tỷ, so
# it really does guard this.
raw = json.load(open(REPO / "testing/checks/fixtures/bctc_code_collision.json",
                     encoding="utf-8"))
c = C()
ym = c.extract_yearly_metrics(raw)
B = 1e9

print("1. Lỗi 1 — dòng khớp TÊN thắng dòng chỉ khớp MÃ SỐ")
check("cogs 2025 = 3.478 tỷ (Giá vốn hàng bán, không phải Thu nhập khác 0,80)",
      abs(ym["Năm 2025"]["cogs"]/B - 3478.00) < 0.01, f"{ym['Năm 2025']['cogs']/B:,.2f}")
check("financial_expense 2025 = 116 tỷ (Chi phí tài chính, không phải Doanh thu HĐTC 179)",
      abs(ym["Năm 2025"]["financial_expense"]/B - 116.00) < 0.01,
      f"{ym['Năm 2025']['financial_expense']/B:,.2f}")
check("financial_expense 2024 = 125 tỷ (cùng lỗi ở cột 2024)",
      abs(ym["Năm 2024"]["financial_expense"]/B - 125.00) < 0.01,
      f"{ym['Năm 2024']['financial_expense']/B:,.2f}")

print("\n2. Fixture thật sự canh được — cách so CŨ cho kết quả khác")
# The old ranking (bare score, no tier) is rebuilt here rather than compared
# against a snapshot stored outside the repo. If someone removes the tier, the two
# assertions in section 1 go red, and this section says why they once were.
old = {}
for li in raw[0]["bctc_extraction"]["income_statement"]["line_items"]:
    m = c.match_metric(li["label"], li["code"], "income_statement")
    if not m:
        continue
    metric, _tier, score = m
    if metric.key not in old or score > old[metric.key][0]:
        old[metric.key] = (score, li["values"]["Năm 2025"])
check("cách cũ lấy nhầm Thu nhập khác cho giá vốn",
      abs(old["cogs"][1]/B - 0.80) < 0.01, f"{old['cogs'][1]/B:,.2f} tỷ")
check("cách cũ lấy nhầm Doanh thu HĐTC cho chi phí tài chính",
      abs(old["financial_expense"][1]/B - 179.00) < 0.01, f"{old['financial_expense'][1]/B:,.2f} tỷ")
check("chỉ tiêu vốn đã đúng thì không đổi",
      abs(old["net_revenue"][1] - ym["Năm 2025"]["net_revenue"]) < 1
      and abs(old["gross_profit"][1] - ym["Năm 2025"]["gross_profit"]) < 1)

print("\n3. Bậc — code-only vẫn dùng được khi nhãn hỏng")
m = c.match_metric("", "10", "income_statement")          # label destroyed, code survives
check("nhãn hỏng + mã đúng -> vẫn map được", m is not None and m[1] == C.CODE_ONLY_TIER,
      str(m[:2]) if m else "None")
m2 = c.match_metric("4. Giá vốn hàng bán", "20", "income_statement")
check("nhãn rõ -> bậc TÊN", m2 is not None and m2[1] == C.LABEL_TIER)
check("bậc TÊN > bậc MÃ SỐ", C.LABEL_TIER > C.CODE_ONLY_TIER)

print("\n4. Lỗi 2 — kiểm đẳng thức kế toán")
check("fixture nhất quán -> im", c.detect_identity_breaks(ym) == [],
      str(c.detect_identity_breaks(ym))[:80])
# The figures actually seen in the wild: gross profit exceeding net revenue.
broken = {"Năm 2025": {"net_revenue": 376.64e9, "cogs": 3478.04e9, "gross_profit": 572.0e9}}
w = c.detect_identity_breaks(broken)
check("lợi nhuận gộp không khớp doanh thu - giá vốn -> báo",
      any("lợi nhuận gộp" in x for x in w), f"{len(w)} cảnh báo")
clean = {"Năm 2025": {"net_revenue": 1000.0, "cogs": 700.0, "gross_profit": 300.0,
                      "gross_revenue": 1020.0, "total_assets": 500.0,
                      "total_liabilities": 200.0, "equity": 300.0}}
check("số liệu sạch -> im", c.detect_identity_breaks(clean) == [], str(c.detect_identity_breaks(clean)))
bad_gr = {"Năm 2025": {"net_revenue": 1100.0, "gross_revenue": 1000.0}}
check("doanh thu thuần > doanh thu bán hàng -> báo",
      any("lớn hơn doanh thu bán hàng" in x for x in c.detect_identity_breaks(bad_gr)))
bad_bs = {"Năm 2025": {"total_assets": 500.0, "total_liabilities": 200.0, "equity": 250.0}}
check("bảng cân đối không cân -> báo",
      any("không cân" in x for x in c.detect_identity_breaks(bad_bs)))
check("lệch 0,5% -> bỏ qua (dung sai 1%)",
      c.detect_identity_breaks({"Năm 2025": {"net_revenue": 1000.0, "cogs": 700.0,
                                             "gross_profit": 301.5}}) == [])
check("thiếu chỉ tiêu -> không báo bừa",
      c.detect_identity_breaks({"Năm 2025": {"net_revenue": 1000.0}}) == [])

print("\n5. Cảnh báo nằm TRÊN bảng số")
# Uses the contradictory figures: the main fixture is consistent, so it warns of
# nothing.
blk = c.format_markdown(broken, c.compute_ratios(broken))
iw, inum = blk.find("SỐ LIỆU KHÔNG KHỚP"), blk.find("| Doanh thu thuần")
check("có trong khối metrics", iw >= 0)
check("nằm trên bảng", 0 <= iw < inum, f"{iw} < {inum}")

print("\n" + "=" * 66)
if fails: print("❌ HỎNG:", *fails, sep="\n   - "); sys.exit(1)
print("✅ Chọn đúng dòng chỉ tiêu, và báo khi số liệu tự mâu thuẫn")
