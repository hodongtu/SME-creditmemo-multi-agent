"""Đầu-cuối: cảnh báo im khi dữ liệu đúng, vẫn nổi khi dữ liệu chưa scale."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import sys; from src.agents.sitevisit_extraction import normalize_amounts
from src.agents.credit_need_calculator import build_credit_need_table

# BCTC: doanh thu năm gần nhất 200 tỷ đồng.
YEARLY = {"Năm 2025": {
    "net_revenue": 200_000_000_000, "cogs": 180_000_000_000,
    "total_assets": 150_000_000_000, "equity": 60_000_000_000,
    "inventory": 30_000_000_000, "receivables": 40_000_000_000,
    "payables": 25_000_000_000, "current_assets": 90_000_000_000,
    "current_liabilities": 50_000_000_000,
}}
RATIOS = {"Năm 2025": {"dso": 73.0, "dio": 60.0, "dpo": 50.0}}

def sitevisit(net_revenue, unit):
    """Báo cáo khảo sát in bằng triệu đồng, kế hoạch 240.800 triệu."""
    return normalize_amounts({
        "business_plan_next_year": {
            "year": "2026", "source_unit": unit,
            "net_revenue": net_revenue, "cogs": net_revenue * 0.914,
            "gross_profit": None, "profit_before_tax": None},
        "lc_terms": {}})

def unit_warnings(table):
    return [w for w in table.warnings if "Nghi sai đơn vị" in w]

print("=" * 70)
print("A. Model tuân prompt MỚI — trả 240.800 như in, source_unit 'trieu dong'")
t = build_credit_need_table(YEARLY, RATIOS, sitevisit_extraction=sitevisit(240_800, "trieu dong"))
w = unit_warnings(t)
rev = next(r for r in t.rows if "oanh thu" in r.label)
print(f"   doanh thu kế hoạch = {rev.plan:,.0f} đ  ({rev.plan/1e9:.2f} tỷ)")
print(f"   cảnh báo đơn vị: {len(w)}  {'✅ im' if not w else '❌ ' + w[0][:70]}")
ok_a = not w and abs(rev.plan - 240_800_000_000) < 1

print("\nB. Dữ liệu chưa scale lọt vào — cảnh báo PHẢI còn nổi")
t2 = build_credit_need_table(YEARLY, RATIOS, sitevisit_extraction=sitevisit(240_800, "dong"))
w2 = unit_warnings(t2)
print(f"   cảnh báo đơn vị: {len(w2)}  {'✅ nổi' if w2 else '❌ im lặng'}")
if w2: print(f"   {w2[0][:100]}")
ok_b = bool(w2)

print("\nC. Cách CŨ (model quy đổi sẵn rồi code nhân tiếp) — lỗi bạn gặp")
t3 = build_credit_need_table(YEARLY, RATIOS, sitevisit_extraction=sitevisit(240_800_000_000, "trieu dong"))
rev3 = next(r for r in t3.rows if "oanh thu" in r.label)
print(f"   doanh thu kế hoạch = {rev3.plan:,.0f} đ  <- thừa 6 số 0, cảnh báo nổi: {bool(unit_warnings(t3))}")

print("\nD. Mọi giá trị trong bảng tối đa 2 chữ số thập phân")
bad = [(r.label, v) for r in t.rows for v in (r.latest, r.plan)
       if isinstance(v, float) and round(v, 2) != v]
print(f"   {len(t.rows)} dòng, vi phạm: {len(bad)}  {'✅' if not bad else '❌ ' + str(bad[:3])}")
ok_d = not bad

print("\n" + "=" * 70)
if ok_a and ok_b and ok_d:
    print("✅ Cảnh báo im trên dữ liệu đúng, vẫn bắt được dữ liệu chưa scale")
else:
    print("❌ HỎNG"); sys.exit(1)
