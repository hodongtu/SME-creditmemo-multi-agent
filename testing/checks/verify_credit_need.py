"""Verify the credit-need calculator and its wiring into the CP prompt."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.credit_need_calculator import build_credit_need_table
from src.agents.supervisor import Supervisor as S
from src.config import Config
from src.types import ClassifiedDocument

M = 10**6
failures = []
row = lambda t, label: next(r for r in t.rows if r.label == label)

METRICS = {"2025": {"net_revenue": 200_000 * M, "cogs": 180_000 * M}}
RATIOS = {"2025": {"cash_conversion_cycle": 128, "dso": 60, "dio": 90, "dpo": 22,
                   "net_working_capital": 30_000 * M, "revenue_growth": 10.0}}
PROPOSAL = {"plan_efficiency": {"revenue": 240_800 * M, "cogs": 220_050 * M},
            "business_plan": {"plan_year": "2026"}}
SURVEY = {"business_plan_next_year": {"year": "2026", "net_revenue": 150_000 * M,
                                      "cogs": 130_000 * M}}
CIC = [{"du_no_hien_tai": [
    {"tctd": "NH A", "khoan_muc": "Dư nợ cho vay ngắn hạn", "vnd": 8_000 * M},
    {"tctd": "NH A", "khoan_muc": "Tổng cộng", "vnd": 12_000 * M},
    {"tctd": "NH B", "khoan_muc": "Dư nợ cho vay ngắn hạn", "vnd": 5_000 * M},
]}]


def near(actual, expected, tol=1.0):
    return actual is not None and abs(actual / M - expected) <= tol


# --- 1. working-capital chain ------------------------------------------------
t = build_credit_need_table(METRICS, RATIOS, PROPOSAL, SURVEY, CIC)
need = row(t, "Nhu cầu vốn lưu động theo chu kỳ tiền").plan
equity = row(t, "Vốn chủ sở hữu tham gia tài trợ vốn lưu động").plan
other = row(t, "Nguồn vốn khác (dư nợ tại TCTD khác)").plan
loan = row(t, "Nhu cầu vốn vay").plan
print("1. Chuỗi vốn lưu động (triệu)")
print(f"   nhu cầu VLĐ {need/M:>10,.0f}  = 220.050 / (365/128)")
print(f"   − VCSH      {equity/M:>10,.0f}")
print(f"   − nguồn khác{other/M:>10,.0f}   (CIC: NH A lấy Tổng cộng 12.000, không cộng 8.000)")
print(f"   = nhu cầu vay {loan/M:>8,.0f}")
if not near(need, 77_168): failures.append(f"need={need}")
if not near(other, 17_000): failures.append(f"other debt={other} (double counting?)")
if not near(loan, 30_168): failures.append(f"loan={loan}")

# --- 2. guarantee tiers -------------------------------------------------------
print("\n2. Ba tầng giá trị hợp đồng cần bảo lãnh")
NOGROWTH = {"2025": {k: v for k, v in RATIOS["2025"].items()
                     if k != "revenue_growth"}}
tiers = [
    ("tầng 1: kế hoạch HĐ",
     dict(PROPOSAL, business_plan={"plan_year": "2026",
                                   "sections": [{"total_planned_value": 500_000 * M}]}),
     RATIOS, 500_000, "đề nghị", 6_164),
    ("tầng 2: DT × (1+growth)", PROPOSAL, RATIOS, 220_000, "BCTC", 2_712),
    ("tầng 3: DT năm KH × 30%", PROPOSAL, NOGROWTH, 72_240, "mặc định", 891),
]
for label, prop, rat, want_base, want_src, want_bid in tiers:
    tt = build_credit_need_table(METRICS, rat, prop, None, CIC)
    base = row(tt, "Giá trị hợp đồng cần bảo lãnh")
    bid = row(tt, "Bảo lãnh dự thầu")
    ok = near(base.plan, want_base) and base.source == want_src and near(bid.plan, want_bid)
    print(f"   {label:26} cơ sở {base.plan/M:>9,.0f} {base.source:9} "
          f"BL dự thầu {bid.plan/M:>7,.0f}  {'OK' if ok else 'SAI'}")
    if not ok:
        failures.append(f"{label}: base={base.plan} src={base.source} bid={bid.plan}")

# --- 3. LC on projected COGS --------------------------------------------------
print("\n3. LC tính trên GIÁ VỐN dự phóng")
t3 = build_credit_need_table(METRICS, RATIOS, PROPOSAL, None, CIC)
for label, want in (("Doanh số mở LC dự kiến", 55_012), ("Số dư LC trung bình", 15_826)):
    got = row(t3, label).plan
    ok = near(got, want, tol=2)
    print(f"   {label:32} {got/M:>9,.0f}  (kỳ vọng {want:,})  {'OK' if ok else 'SAI'}")
    if not ok:
        failures.append(f"{label}={got}")
days = row(t3, "Số ngày trung bình thanh toán LC bình quân").plan
if days != 105:
    failures.append(f"lc days={days}")

# --- 3b. LC parameters from the site-visit report -----------------------------
print("\n3b. Tham số LC lấy từ báo cáo khảo sát")
SURVEY_LC = {"lc_terms": {"import_ratio": 0.8, "lc_share_of_import": 0.6,
                          "sight_share": 0.3, "deferred_share": 0.7,
                          "sight_days": 45, "deferred_days": 150}}
t3b = build_credit_need_table(METRICS, RATIOS, PROPOSAL, SURVEY_LC, CIC)
for label, want, want_src in (
    ("Tỷ lệ nhập khẩu", 80.0, "khảo sát"),
    ("Tỷ lệ hàng nhập cần mở LC", 60.0, "khảo sát"),
    ("Số ngày trung bình thanh toán LC bình quân", 118.5, "tính toán"),
):
    r = row(t3b, label)
    ok = abs(r.plan - want) < 0.1 and r.source == want_src
    print(f"   {label:42} {r.plan:>8,.1f}  {r.source:10} {'OK' if ok else 'SAI'}")
    if not ok:
        failures.append(f"{label}: {r.plan} / {r.source}")

# --- 4. priority chain -------------------------------------------------------
print("\n4. Chuỗi ưu tiên doanh thu năm kế hoạch")
for label, prop, surv, want_val, want_src in (
    ("đề nghị + khảo sát", PROPOSAL, SURVEY, 240_800, "đề nghị"),
    ("chỉ khảo sát", None, SURVEY, 150_000, "khảo sát"),
    ("không có gì", None, None, 200_000, "BCTC"),
):
    tt = build_credit_need_table(METRICS, RATIOS, prop, surv, CIC)
    r = row(tt, "Doanh thu thuần")
    ok = near(r.plan, want_val) and r.source == want_src
    print(f"   {label:20} -> {r.plan/M:>9,.0f}  nguồn={r.source:10} {'OK' if ok else 'SAI'}")
    if not ok: failures.append(f"priority {label}: {r.plan} / {r.source}")

# --- 5. source flags + negative branch ---------------------------------------
print("\n5. Cờ nguồn và nhánh âm")
defaults = [r.label for r in t.rows if r.source == "mặc định"]
print(f"   số dòng 'mặc định': {len(defaults)}")
# With a growth rate available the guarantee base is tier 2, so its flag is
# BCTC — the point is that it names a real source, never blank.
if row(t, "Bảo lãnh dự thầu").source not in {"đề nghị", "BCTC", "mặc định"}:
    failures.append(f"guarantee source unset: {row(t, 'Bảo lãnh dự thầu').source!r}")
# The LC ratios have no document source at all, so they must stay flagged
# default — that is what tells the reviewer they are assumptions.
if row(t, "Tỷ lệ nhập khẩu").source != "mặc định":
    failures.append("import ratio not flagged as default")
if row(t, "Nguồn vốn khác (dư nợ tại TCTD khác)").source != "CIC":
    failures.append("CIC row source wrong")

neg = build_credit_need_table(
    METRICS, {"2025": {**RATIOS["2025"], "net_working_capital": 200_000 * M}},
    PROPOSAL, None, CIC)
nr = row(neg, "Nhu cầu vốn vay")
print(f"   nhu cầu vay khi VLĐ ròng lớn: {nr.plan/M:,.0f} triệu")
print(f"   diễn giải: {nr.note[:60]}...")
if nr.plan is None or nr.plan >= 0: failures.append("negative branch not exercised")
if "âm" not in nr.note.lower(): failures.append("negative note missing")

# --- 6. CIC gate + 7. block/budget -------------------------------------------
print("\n6. Cổng CIC cho CP")
needed = S._passes_needed_for_route("CREDIT_PROPOSAL_AGENT")
print(f"   passes: {sorted(needed)}")
if "CIC S10A" not in needed: failures.append("CIC S10A gate not opened for CP")
if "CREDIT_PROPOSAL_AGENT" in S.CIC_S10A_JSON_AGENTS:
    failures.append("CP wrongly added to CIC_S10A_JSON_AGENTS")

sup = S(Config())


def line(label, code, value):
    return {"label": label, "ma_so": code, "values": {"2025": value}}


BCTC = {
    "report_years": {"current": "2025"},
    "income_statement": {"line_items": [
        line("Doanh thu thuần", "10", 200_000 * M),
        line("Giá vốn hàng bán", "11", 180_000 * M),
    ]},
    "balance_sheet": {"line_items": [
        line("Tài sản ngắn hạn", "100", 90_000 * M),
        line("Hàng tồn kho", "140", 44_000 * M),
        line("Phải thu khách hàng", "131", 33_000 * M),
        line("Nợ ngắn hạn", "310", 60_000 * M),
        line("Phải trả người bán", "311", 11_000 * M),
        line("Vốn chủ sở hữu", "400", 50_000 * M),
    ]},
}
docs = [
    ClassifiedDocument(
        filename="BCTC.pdf", path="/tmp/a", content="x" * 500, agent="X", reasoning="",
        confidence=0.9, document_type="bao_cao_tai_chinh", extraction_status="success",
        agent_relevance={"CREDIT_PROPOSAL_AGENT": "R", "FINANCIAL_ANALYSIS_AGENT": "R"},
        relevant_agents=["CREDIT_PROPOSAL_AGENT", "FINANCIAL_ANALYSIS_AGENT"],
        is_bctc=True, bctc_extraction=BCTC,
    ),
    ClassifiedDocument(
        filename="CIC.pdf", path="/tmp/b", content="y" * 500, agent="X", reasoning="",
        confidence=0.9, document_type="cic_khach_hang_vay", extraction_status="success",
        agent_relevance={"CREDIT_RELATIONSHIP_AGENT": "R"},
        relevant_agents=["CREDIT_RELATIONSHIP_AGENT"],
        is_cic_s10a=True, cic_s10a_extraction=CIC[0],
    ),
]
print("\n7. Khối trong prompt CP")
for agent in ("CREDIT_PROPOSAL_AGENT", "FINANCIAL_ANALYSIS_AGENT"):
    text = sup._build_user_input("q", docs, "", agent, "", {})
    has = S.CREDIT_NEED_BLOCK_HEADING in text
    budget = Config().agent_input_char_budgets[agent]
    print(f"   {agent:26} khối={has!s:5} prompt={len(text):,} / trần {budget:,}")
    if agent == "CREDIT_PROPOSAL_AGENT":
        if not has:
            failures.append("CP did NOT receive the credit-need block")
        if len(text) > budget:
            failures.append("CP prompt exceeds budget")
        # The CIC document is not routed to CP, yet its balance must reach the
        # table — that is the whole point of reading the full document list.
        # Rendered in tỷ VNĐ now, matching the metrics block, so 17.000.000.000
        # đồng appears as "17,00".
        debt_row = next(
            (l for l in text.splitlines() if l.startswith("| Nguồn vốn khác")), "")
        if "17,00" not in debt_row:
            failures.append(
                f"other-lender debt missing or mis-scaled in the CP block: {debt_row[:80]}")
    if agent == "FINANCIAL_ANALYSIS_AGENT" and has:
        failures.append("FA wrongly received the credit-need block")

# --- 8. sections hidden when the application asks for nothing -----------------
print("\n8. Ẩn mục theo nhu cầu trong giấy đề nghị")
loan_only = dict(PROPOSAL, credit_request={"facilities": [
    {"name": "Hạn mức cho vay ngắn hạn", "amount": 150_000 * M}]})
t8 = build_credit_need_table(METRICS, RATIOS, loan_only, None, CIC)
n_guar = sum(1 for r in t8.rows if r.label.startswith("Bảo lãnh"))
n_lc = sum(1 for r in t8.rows if "LC" in r.label)
warned = any("không nêu nhu cầu" in w for w in t8.warnings)
print(f"   chỉ có hạn mức vay -> bảo lãnh {n_guar} dòng, LC {n_lc} dòng, có cảnh báo: {warned}")
if n_guar or n_lc or not warned:
    failures.append(f"hiding failed: guar={n_guar} lc={n_lc} warned={warned}")

t8b = build_credit_need_table(METRICS, RATIOS, PROPOSAL, None, CIC)
n_guar_b = sum(1 for r in t8b.rows if r.label.startswith("Bảo lãnh"))
print(f"   không có giấy đề nghị -> bảo lãnh {n_guar_b} dòng (phải là 5)")
if n_guar_b != 5:
    failures.append(f"no-application case hid the guarantee block: {n_guar_b}")

# --- 9. no false matches on ordinary facility names --------------------------
print("\n9. Không khớp nhầm tên hạn mức thường")
ordinary = dict(PROPOSAL, credit_request={"facilities": [
    {"name": "Hạn mức cho vay ngắn hạn", "amount": 150_000 * M},
    {"name": "Hạn mức thấu chi", "amount": 10_000 * M},
    {"name": "Hạn mức chiết khấu BCT", "amount": 30_000 * M},
    # contains "thanh toán" but is not a guarantee — the marker gate must hold
    {"name": "Hạn mức thanh toán quốc tế", "amount": 40_000 * M},
]})
t9 = build_credit_need_table(METRICS, RATIOS, ordinary, None, CIC)
# None of these names is a guarantee or an LC, so both sections are dropped —
# the false-match test is now "the block never appeared", which is stronger than
# checking the source flag on a row that should not exist.
guar_rows = [r.label for r in t9.rows if r.label.startswith("Bảo lãnh")]
lc_rows = [r.label for r in t9.rows if "LC" in r.label]
print(f"   dòng bảo lãnh sinh ra: {guar_rows or '(không)'}")
print(f"   dòng LC sinh ra      : {lc_rows or '(không)'}")
if guar_rows:
    failures.append(f"false guarantee match: {guar_rows}")
if lc_rows:
    failures.append(f"false LC match: {lc_rows}")

# --- 10. the two deliberate exceptions survive -------------------------------
print("\n10. Hai ngoại lệ không bị ghi đè")
with_capital = dict(PROPOSAL, capital_plan={
    "own_capital": 110_000 * M, "other_capital": 25_000 * M})
t10 = build_credit_need_table(METRICS, RATIOS, with_capital, None, CIC)
eq = row(t10, "Vốn chủ sở hữu tham gia tài trợ vốn lưu động")
oth = row(t10, "Nguồn vốn khác (dư nợ tại TCTD khác)")
print(f"   VCSH tham gia   {eq.plan/M:>9,.0f}  {eq.source:8} (phải là 30.000 / BCTC)")
print(f"   Nguồn vốn khác  {oth.plan/M:>9,.0f}  {oth.source:8} (phải là 17.000 / CIC)")
if not (near(eq.plan, 30_000) and eq.source == "BCTC"):
    failures.append("equity exception overridden by capital_plan")
if not (near(oth.plan, 17_000) and oth.source == "CIC"):
    failures.append("other-debt exception overridden by capital_plan")

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")
