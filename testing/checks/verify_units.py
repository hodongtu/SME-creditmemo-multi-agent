"""Chứng minh hết nhân đôi đơn vị, và làm tròn 2 số thập phân."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import sys; from src.agents.sitevisit_extraction import (
    normalize_amounts, SITEVISIT_EXTRACTION_SYSTEM_PROMPT)
from src.agents import proposal_extraction as prop

EXPECT = 240_800_000_000
fails = []

def block(unit, rev):
    return {"business_plan_next_year": {
        "source_unit": unit, "net_revenue": rev,
        "cogs": 220_050, "gross_profit": 20_750, "profit_before_tax": 11_300}}

print("=" * 68)
print("1. Model tuân prompt MỚI: trả số như in + đơn vị của trang")
got = normalize_amounts(block("trieu dong", 240_800))["business_plan_next_year"]["net_revenue"]
ok = got == EXPECT
print(f"   240.800 + 'trieu dong' -> {got:,.0f}   (mong đợi {EXPECT:,})  {'✅' if ok else '❌'}")
fails += [] if ok else ["nhân đôi vẫn còn"]

print("\n2. source_unit mặc định đồng (rỗng / thiếu hẳn)")
for unit, label in [("", "rỗng"), ("dong", "'dong'"), (None, "null")]:
    got = normalize_amounts(block(unit, EXPECT))["business_plan_next_year"]["net_revenue"]
    ok = got == EXPECT
    print(f"   {label:8} -> {got:,.0f}  {'✅' if ok else '❌'}")
    fails += [] if ok else [f"mặc định {label} sai"]
b = block("dong", EXPECT)["business_plan_next_year"]; del b["source_unit"]
got = normalize_amounts({"business_plan_next_year": b})["business_plan_next_year"]["net_revenue"]
ok = got == EXPECT
print(f"   {'không có khoá':8} -> {got:,.0f}  {'✅' if ok else '❌'}")
fails += [] if ok else ["thiếu khoá source_unit sai"]

print("\n3. Prompt không còn bảo model tự quy đổi")
p = SITEVISIT_EXTRACTION_SYSTEM_PROMPT
for bad in ["quy về ĐỒNG", "nhân lên"]:
    ok = bad not in p
    print(f"   sitevisit không chứa {bad!r}  {'✅' if ok else '❌'}")
    fails += [] if ok else [f"sitevisit còn {bad!r}"]
pp = prop.PROPOSAL_EXTRACTION_SYSTEM_PROMPT
for bad in ["phải quy về ĐỒNG", "-> 240800000000"]:
    ok = bad not in pp
    print(f"   proposal  không chứa {bad!r}  {'✅' if ok else '❌'}")
    fails += [] if ok else [f"proposal còn {bad!r}"]
for p_, name in [(p, "sitevisit"), (pp, "proposal")]:
    ok = 'thì để "dong"' in p_
    print(f"   {name:9} nêu mặc định \"dong\"  {'✅' if ok else '❌'}")
    fails += [] if ok else [f"{name} thiếu mặc định"]

print("\n4. Làm tròn 2 số thập phân")
got = normalize_amounts(block("trieu dong", 240_800.123456))["business_plan_next_year"]["net_revenue"]
ok = got == round(got, 2) and len(str(got).split(".")[-1]) <= 2
print(f"   240800,123456 x10^6 -> {got}  {'✅' if ok else '❌'}")
fails += [] if ok else ["sitevisit không làm tròn"]

print("\n5. lc_terms giữ nguyên độ chính xác (là đầu vào, không phải số hiển thị)")
from src.agents.sitevisit_extraction import normalize_lc_ratios
r = normalize_lc_ratios({"lc_terms": {"import_ratio": 0.355}})
ok = r["lc_terms"]["import_ratio"] == 0.355
print(f"   import_ratio 0.355 -> {r['lc_terms']['import_ratio']}  {'✅' if ok else '❌'}")
fails += [] if ok else ["lc_terms bị làm tròn"]

print("\n" + "=" * 68)
if fails:
    print("❌ HỎNG:", *fails, sep="\n   - "); sys.exit(1)
print("✅ Tất cả kiểm chứng đơn vị + làm tròn đều đạt")
