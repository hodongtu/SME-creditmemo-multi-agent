"""Verify the debt chart only appears when CREDIT_RELATIONSHIP_AGENT ran."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.supervisor import Supervisor
from src.types import ClassifiedDocument

CIC_DOC = ClassifiedDocument(
    filename="CIC_S10A.pdf",
    path="/tmp/CIC_S10A.pdf",
    content="",
    agent="CREDIT_RELATIONSHIP_AGENT",
    reasoning="",
    confidence=0.9,
    is_cic_s10a=True,
    cic_s10a_extraction={
        "du_no_12_thang": [
            {"thang": "01/2024", "tong_du_no": 5_000_000_000},
            {"thang": "02/2024", "tong_du_no": 6_000_000_000},
            {"thang": "03/2024", "tong_du_no": 7_000_000_000},
        ]
    },
)

VAT_BLOCK = """```vat-doanh-thu
01/2024: 8.000.000.000
02/2024: 9.000.000.000
03/2024: 10.000.000.000
```"""

# A report with no credit-relationship heading — what BA/FA/CP/RISK produce.
PLAIN_REPORT = "# Bao cao\n\n## 1. Tong quan\n\nnoi dung"

MEMO = {
    "BUSINESS_ACTIVITY_AGENT": "x",
    "CREDIT_RELATIONSHIP_AGENT": VAT_BLOCK,
    "FINANCIAL_ANALYSIS_AGENT": "x",
    "CREDIT_PROPOSAL_AGENT": "x",
    "RISK_ASSESSMENT_AGENT": "x",
    "CREDIT_MEMO": "x",
}

FLOWS = [
    ("single BUSINESS_ACTIVITY", {"BUSINESS_ACTIVITY_AGENT": "x"}, False),
    ("single FINANCIAL_ANALYSIS", {"FINANCIAL_ANALYSIS_AGENT": "x"}, False),
    ("single CREDIT_PROPOSAL", {"CREDIT_PROPOSAL_AGENT": "x"}, False),
    ("single RISK_ASSESSMENT", {"RISK_ASSESSMENT_AGENT": "x"}, False),
    ("CONVERSATION", {"CONVERSATION_AGENT": "x"}, False),
    ("single CREDIT_RELATIONSHIP", {"CREDIT_RELATIONSHIP_AGENT": VAT_BLOCK}, True),
    ("full CREDIT_MEMO", MEMO, True),
]

failures = []

print(f"{'FLOW':29} {'CHART?':8} {'EXPECTED':10} RESULT")
print("-" * 62)
for name, subs, should_build in FLOWS:
    block, title = Supervisor._build_debt_chart_block([CIC_DOC], subs)
    built = bool(block)
    ok = built == should_build
    if not ok:
        failures.append(f"{name}: built={built}, expected={should_build}")
    print(
        f"{name:29} {('YES' if built else 'no'):8} "
        f"{('YES' if should_build else 'no'):10} {'PASS' if ok else 'FAIL'}"
    )
    if not built and title:
        failures.append(f"{name}: no block but title={title!r}")

print("\n--- anchor placement (chart must land in its section) ---")
block, title = Supervisor._build_debt_chart_block(
    [CIC_DOC], {"CREDIT_RELATIONSHIP_AGENT": VAT_BLOCK}
)
for label, report, expected in [
    ("CR report (## 2. Dien bien du no)", "# CR\n\n## 2. Dien bien du no\n\nx", "dien bien du no"),
    ("Memo (## 3. Quan hệ tín dụng)", "# Memo\n\n## 3. Quan hệ tín dụng\n\nx", "quan he tin dung"),
]:
    _, anchor = Supervisor._insert_debt_chart(report, block, title)
    ok = anchor == expected
    if not ok:
        failures.append(f"{label}: anchor={anchor!r}, expected={expected!r}")
    print(f"{label:38} -> {anchor:20} {'PASS' if ok else 'FAIL'}")

print("\n--- title tracks the data actually drawn ---")
for label, cr_output, want_vat in [
    ("CR emitted vat-doanh-thu block", VAT_BLOCK, True),
    ("CR emitted no VAT block", "khong co block vat", False),
]:
    block, title = Supervisor._build_debt_chart_block(
        [CIC_DOC], {"CREDIT_RELATIONSHIP_AGENT": cr_output}
    )
    columns = next(l for l in block.splitlines() if l.startswith("columns:"))
    embedded = next(l for l in block.splitlines() if l.startswith("title:"))
    has_vat_col = "Doanh thu VAT" in columns
    vat_in_title = "doanh thu VAT" in title
    # The title must promise exactly what the chart draws, and the block's own
    # embedded title must match the one handed to the fallback heading.
    ok = has_vat_col == want_vat and vat_in_title == want_vat
    ok = ok and embedded == f"title: {title}"
    if not ok:
        failures.append(f"{label}: title={title!r}, {columns}")
    print(f"{label:32} {'PASS' if ok else 'FAIL'}")
    print(f"    title:   {title}")
    print(f"    {columns}")

    # The fallback heading must carry that same title.
    text, anchor = Supervisor._insert_debt_chart(PLAIN_REPORT, block, title)
    assert anchor == "appended"
    if f"## {title}" not in text:
        failures.append(f"{label}: fallback heading missing {title!r}")

print()
if failures:
    print("FAILURES:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("All checks passed.")
