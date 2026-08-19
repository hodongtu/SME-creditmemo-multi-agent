"""BCTC: the code converts money units, the model no longer does."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import json, copy; from src.agents.bctc_extraction import (
    normalize_amounts, BCTC_EXTRACTION_SYSTEM_PROMPT as P)
from src.agents.financial_ratio_calculator import FinancialRatioCalculator

fails = []
def check(name, ok, detail=""):
    print(f"   {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok: fails.append(name)

def bctc(unit_bs, rev, unit_notes="dong", amt=500):
    return {"balance_sheet": {"unit": "VNĐ", "source_unit": unit_bs, "line_items": [
                {"label": "TỔNG CỘNG TÀI SẢN", "code": "270", "values": {"Năm 2025": rev}}]},
            "income_statement": {"unit": "VNĐ", "source_unit": unit_bs, "line_items": [
                {"label": "Doanh thu thuần", "code": "10", "values": {"Năm 2025": rev}}]},
            "cash_flow_statement": {"unit": "VNĐ", "source_unit": unit_bs, "line_items": []},
            "notes_summary": {"source_unit": unit_notes,
                "related_party_transactions": [{"counterparty": "A", "amount": amt}],
                "key_item_breakdowns": [{"item": "Vay", "amount": amt}]}}

print("1. Quy đổi đúng một lần")
r = normalize_amounts(bctc("trieu dong", 240_800))
got = r["income_statement"]["line_items"][0]["values"]["Năm 2025"]
check("bảng ghi 'triệu đồng', model trả 240.800 -> 240.800.000.000", got == 240_800_000_000, f"{got:,.0f}")

print("\n2. Mặc định đồng khi trang không chú thích")
for unit, label in [("", "rỗng"), ("dong", "'dong'"), (None, "null")]:
    got = normalize_amounts(bctc(unit, 240_800_000_000))["income_statement"]["line_items"][0]["values"]["Năm 2025"]
    check(f"source_unit {label}", got == 240_800_000_000, f"{got:,.0f}")
d = bctc("dong", 240_800_000_000); del d["balance_sheet"]["source_unit"]
got = normalize_amounts(d)["balance_sheet"]["line_items"][0]["values"]["Năm 2025"]
check("thiếu hẳn khoá source_unit", got == 240_800_000_000, f"{got:,.0f}")

print("\n3. Mỗi bảng dùng đơn vị của chính nó")
d = bctc("trieu dong", 240_800, unit_notes="ty dong", amt=5)
r = normalize_amounts(d)
check("bảng chính x10^6", r["income_statement"]["line_items"][0]["values"]["Năm 2025"] == 240_800_000_000)
check("thuyết minh x10^9 (đơn vị riêng)", r["notes_summary"]["related_party_transactions"][0]["amount"] == 5_000_000_000)
check("key_item_breakdowns cũng được quy đổi", r["notes_summary"]["key_item_breakdowns"][0]["amount"] == 5_000_000_000)

print("\n4. Không hỏng dữ liệu không phải số")
d = bctc("trieu dong", 240_800); d["income_statement"]["line_items"][0]["values"]["Năm 2024"] = None
d["notes_summary"]["related_party_transactions"][0]["amount"] = None
r = normalize_amounts(d)
check("null giữ nguyên null", r["income_statement"]["line_items"][0]["values"]["Năm 2024"] is None)
check("amount null giữ nguyên", r["notes_summary"]["related_party_transactions"][0]["amount"] is None)

print("\n5. Prompt không còn bảo model tự quy đổi")
check("bỏ 'ghi bằng SỐ NGUYÊN đơn vị VNĐ'", "SỐ NGUYÊN đơn vị VNĐ" not in P)
check("có 'GHI ĐÚNG CON SỐ IN TRÊN GIẤY'", "GHI ĐÚNG CON SỐ IN TRÊN GIẤY" in P)
check('nêu mặc định "dong"', 'thì để\n  "dong"' in P or '"dong". Thuyết minh' in P)

print("\n6. Chốt chặn độ lớn trong khối metrics")
calc = FinancialRatioCalculator()
ok_metrics = {"Năm 2024": {"net_revenue": 200e9, "total_assets": 150e9},
              "Năm 2025": {"net_revenue": 240.8e9, "total_assets": 160e9}}
bad_metrics = {"Năm 2024": {"net_revenue": 200e9, "total_assets": 150e9},
               "Năm 2025": {"net_revenue": 240_800, "total_assets": 160e9}}
# Threshold 1000, not 100: two consecutive statement years can genuinely be far
# apart for a newly incorporated company, and the smallest unit error is 1000x.
grow = {"Năm 2024": {"net_revenue": 0.5e9}, "Năm 2025": {"net_revenue": 60e9}}
check("số liệu thật -> im", calc.detect_unit_anomalies(ok_metrics) == [])
check("DN mới tăng 120 lần -> KHÔNG báo nhầm", calc.detect_unit_anomalies(grow) == [])
w = calc.detect_unit_anomalies(bad_metrics)
check("một năm chưa scale -> cảnh báo", len(w) == 1, w[0][:60] if w else "không có")
blk = calc.format_markdown(bad_metrics, calc.compute_ratios(bad_metrics))
i_warn, i_num = blk.find("NGHI SAI ĐƠN VỊ"), blk.find("net_revenue")
check("cảnh báo có trong khối metrics", i_warn >= 0)
check("và nằm TRÊN bảng số", 0 <= i_warn < i_num, f"vị trí {i_warn} < {i_num}")

print("\n" + "="*66)
if fails: print("❌ HỎNG:", *fails, sep="\n   - "); sys.exit(1)
print("✅ BCTC quy đổi ở code, có chốt chặn độ lớn")
