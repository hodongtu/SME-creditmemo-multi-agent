"""Verify the sitevisit extraction pass is wired end to end."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.supervisor import Supervisor as S
from src.config import Config
from src.matrix.document_matrix import (
    agent_relevance_for_type,
    is_sitevisit_type,
    load_matrix,
)
from src.types import ClassifiedDocument

failures = []
AGENTS = list(S.SITEVISIT_JSON_AGENTS)
TYPE_ID = "bao_cao_khao_sat_thuc_dia"

# --- 1. matrix flag ----------------------------------------------------------
m = load_matrix()
flagged = [t for t in m.types if is_sitevisit_type(t)]
print(f"1. Cờ matrix: {flagged}  (21 type còn lại False: "
      f"{all(not is_sitevisit_type(t) for t in m.types if t != TYPE_ID)})")
if flagged != [TYPE_ID]:
    failures.append(f"sitevisit flag on {flagged}")

# --- 2. routing --------------------------------------------------------------
rel = agent_relevance_for_type(TYPE_ID, None)
print(f"\n2. Routing: {rel}")
if sorted(rel) != sorted(AGENTS) or set(rel.values()) != {"R"}:
    failures.append(f"routing mismatch: {rel}")

# --- 3. route gating ---------------------------------------------------------
print("\n3. Pass 'Sitevisit' chạy ở route nào")
for route in S.ROUTE_AGENTS:
    needed = S._passes_needed_for_route(route)
    has = "Sitevisit" in needed
    expect = bool(set(S.ROUTE_AGENTS[route]) & set(AGENTS))
    print(f"   {route:28} {'có' if has else '—':4} (kỳ vọng {'có' if expect else '—'})")
    if has != expect:
        failures.append(f"{route}: sitevisit gating {has} != {expect}")

# --- 4. every agent sees the block, raw OCR gone -----------------------------
sup = Supervisor = S(Config())
EXTRACTION = {
    "survey_info": {"survey_date": "2026-03-01", "officers": ["Nguyễn Văn A"],
                    "location": "KCN X", "customer_participants": ["Trần B - GĐ"]},
    "business_profile": {"industry": "May mặc", "gso_code": "1410",
                         "main_products": [{"name": "Áo sơ mi", "note": None}]},
    "supply_chain": {"inputs": [{"supplier": "Vải Y", "item": "Vải", "terms": None}],
                     "outputs": [{"customer": "Z Corp", "item": "Áo", "terms": None}]},
    "business_plan_next_year": {"year": "2027", "source_unit": "ty dong",
                                "net_revenue": 120, "cogs": 90,
                                "gross_profit": 30, "profit_before_tax": 20,
                                "assumptions": []},
    "conclusion": {"overall_assessment": "Hoạt động ổn định",
                   "risks_noted": ["Phụ thuộc 1 khách hàng"],
                   "recommendation": "Đề xuất cấp hạn mức", "conditions": []},
}
RAW = "NỘI DUNG OCR THÔ CỦA BÁO CÁO KHẢO SÁT" * 40

def make_doc():
    return ClassifiedDocument(
        filename="BaoCaoKhaoSat.pdf", path="/tmp/x.pdf", content=RAW,
        agent="BUSINESS_ACTIVITY_AGENT", reasoning="", confidence=0.9,
        document_type=TYPE_ID, extraction_status="success",
        agent_relevance=dict(rel), relevant_agents=sorted(rel),
        is_sitevisit=True, sitevisit_extraction=dict(EXTRACTION),
    )

print("\n4. Mỗi agent nhận khối JSON, mất OCR thô")
for agent in AGENTS:
    text = sup._build_user_input("phân tích", [make_doc()], "", agent, "", {})
    has_block = S.SITEVISIT_BLOCK_HEADING in text
    raw_gone = RAW[:60] not in text
    opinion = "Ý KIẾN CHỦ QUAN" in text
    print(f"   {agent:28} khối={has_block!s:5} OCR-thô-đã-thay={raw_gone!s:5} "
          f"cảnh-báo-ý-kiến={opinion}")
    if not (has_block and raw_gone and opinion):
        failures.append(f"{agent}: block={has_block} raw_gone={raw_gone} opinion={opinion}")

# an agent NOT in the list must not get it
block_only = S._build_sitevisit_structured_block([make_doc()])
if not block_only:
    failures.append("block builder returned empty for a flagged doc")

# --- 5. unit normalisation ---------------------------------------------------
from src.agents.sitevisit_extraction import normalize_amounts
scaled = normalize_amounts({"business_plan_next_year": dict(
    EXTRACTION["business_plan_next_year"])})
plan = scaled["business_plan_next_year"]
print(f"\n5. Đơn vị: 120 tỷ -> {plan['net_revenue']:,} đồng")
if plan["net_revenue"] != 120 * 10**9 or plan["cogs"] != 90 * 10**9:
    failures.append(f"unit scaling wrong: {plan}")

# --- 6. budget + money_blocks ------------------------------------------------
doc = make_doc()
with_block = len(sup._build_user_input("q", [doc], "", "CREDIT_PROPOSAL_AGENT", "", {}))
budget = Config().agent_input_char_budgets["CREDIT_PROPOSAL_AGENT"]
print(f"\n6. Ngân sách: prompt {with_block:,} ký tự, trần {budget:,} "
      f"-> {'trong trần' if with_block <= budget else 'VƯỢT TRẦN'}")
if with_block > budget:
    failures.append(f"prompt {with_block} exceeds budget {budget}")

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")
