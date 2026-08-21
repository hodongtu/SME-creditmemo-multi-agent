"""Chứng minh hết nhân đôi đơn vị, và số trong báo cáo được trình bày đúng."""

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



# --- Trình bày số trong báo cáo giao cho người đọc ---------------------------
# Hai luật đặt ra cho mọi agent, chốt lại bằng mã ở đây vì luật trong prompt chỉ
# là lời nhờ. Đáng canh nhất là chỗ chúng đụng EVIDENCE RULE: dấu "-" nghĩa là số
# ĐỌC ĐƯỢC và bằng không, còn ô TRỐNG nghĩa là hồ sơ không nêu. Lẫn hai thứ này
# là báo cáo nói dối người đọc, nên có riêng một khẳng định cho nó.
from src.utils.markdown_fixups import tidy_numbers  # noqa: E402
from src.agents.specialist import SpecialistAgent  # noqa: E402

print("\n6. Trình bày số: bỏ thập phân toàn 0, ô bằng không thành '-'")
for src, want, why in (
    ("Tỷ trọng 8,00% và 8,0%.", "Tỷ trọng 8% và 8%.", "bỏ thập phân toàn 0"),
    ("Tỷ trọng 35,2% giữ nguyên.", "Tỷ trọng 35,2% giữ nguyên.", "1 chữ số có nghĩa thì giữ"),
    ("Tỷ trọng 35,20% giữ nguyên.", "Tỷ trọng 35,20% giữ nguyên.", "0 sau chữ số có nghĩa thì giữ"),
    ("| Doanh thu | 0,00 | 376,64 |", "| Doanh thu | - | 376,64 |", "ô bằng 0 -> gạch ngang"),
    ("| Tỷ trọng | 0,00% | 8,0% |", "| Tỷ trọng | - | 8% |", "cả hai luật cùng lúc"),
    ("| Nợ quá hạn | 0 | 1,20 |", "| Nợ quá hạn | - | 1,20 |", "số 0 trần"),
    ("|---|---:|---:|", "|---|---:|---:|", "hàng phân cách không bị đụng"),
    ("| Ghi chú | | Chưa nêu |", "| Ghi chú | | Chưa nêu |", "Ô TRỐNG vẫn trống, không thành '-'"),
    ("| Năm | 2020 | 100 |", "| Năm | 2020 | 100 |", "số khác 0 không bị đụng"),
    ("Doanh nghiệp có 0 lao động thời vụ.", "Doanh nghiệp có 0 lao động thời vụ.",
     "số 0 trong câu văn không thành gạch ngang"),
    ("```mermaid\nA -->|8,0%| B\n```", "```mermaid\nA -->|8%| B\n```",
     "nhãn sơ đồ cũng là thứ người đọc nhìn thấy"),
):
    got = tidy_numbers(src)
    print(f"   {'✅' if got == want else '❌'} {why}")
    if got != want:
        fails.append(f"tidy_numbers: {src!r} -> {got!r}, mong {want!r}")

rules = SpecialistAgent.__init__.__doc__ or ""
import inspect  # noqa: E402
prompt_src = inspect.getsource(SpecialistAgent)
for needed, why in (
    ("NUMBER FORMAT RULE", "luật có trong prompt chung của mọi agent"),
    ('nghĩa là hồ sơ nêu và bằng không', "prompt nói rõ '-' khác ô trống"),
):
    ok = needed in prompt_src
    print(f"   {'✅' if ok else '❌'} {why}")
    if not ok:
        fails.append(f"prompt thiếu: {needed!r}")

print("\n" + "=" * 68)
if fails:
    print("❌ HỎNG:", *fails, sep="\n   - "); sys.exit(1)
print("✅ Tất cả kiểm chứng đơn vị + làm tròn đều đạt")
