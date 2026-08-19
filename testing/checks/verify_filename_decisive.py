"""Verify filename_decisive saves LLM calls without changing any label."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.document_classification import (
    document_type_scores,
    filename_keyword_owner,
    rule_classify_document,
)
from src.config import Config
from src.matrix.document_matrix import load_matrix

TH = Config().document_classifier_rule_confidence_threshold
matrix = load_matrix()
tids = list(matrix.types)
failures = []


def calls_llm(rule, *, with_new_rule):
    if not rule["document_type"]:
        return True
    if rule["confidence"] >= TH or rule["routing_unambiguous"]:
        return False
    return not (with_new_rule and rule["filename_decisive"])


# --- 1. 22x22 scan -----------------------------------------------------------
saved = wrong = still = 0
for a in tids:
    keywords = list(matrix.types[a].keywords)
    if not keywords:
        continue
    fn = f"{keywords[0]}.pdf"
    fn_hits = {t: s for t, s in document_type_scores(fn, "").items() if s}
    if a not in fn_hits:
        continue
    fn_top = max(fn_hits, key=lambda t: fn_hits[t])
    for b in tids:
        if b == a:
            continue
        rule = rule_classify_document(fn, "\n".join(list(matrix.types[b].keywords)[:3]))
        if not calls_llm(rule, with_new_rule=False):
            continue  # already skipped before the change
        if calls_llm(rule, with_new_rule=True):
            still += 1
        elif rule["document_type"] == fn_top:
            saved += 1
        else:
            wrong += 1
            failures.append(f"scan: {fn} -> {rule['document_type']} != {fn_top}")

print("1. Quét 22x22 (các ca trước đây phải gọi LLM)")
print(f"   bỏ được LLM, đúng ý tên file : {saved}")
print(f"   bỏ LLM nhưng mâu thuẫn (SAI) : {wrong}")
print(f"   vẫn gọi LLM                  : {still}")
# Only "wrong" is a fixed invariant. The saved count moves with the matrix —
# adding an agent to a type changes its routing signature, which lets
# routing_is_unambiguous settle more pairs on its own. It was 74 when written
# and 76 after the site-visit routing change; pinning the exact number would
# fail on every legitimate matrix edit.
if wrong != 0:
    failures.append(f"scan expected 0 wrong, got {wrong}")
if saved < 70:
    failures.append(f"saved dropped to {saved}, expected ~74+")

# --- 2. no label changes on realistic filenames ------------------------------
NAMES = [
    "BCTC_VVS_2024.pdf", "BCTC_MZG_2025.pdf", "VIMID_so_chi_tiet.xlsx",
    "CIC_S10A.pdf", "Bang can doi ke toan 2024.xlsx", "SO 331.xlsx",
    "To khai thue GTGT Q1.pdf", "Giay de nghi cap tin dung.pdf",
    "Sao ke tai khoan TCTD.pdf", "Hop dong thue nha xuong.pdf",
    "BCTC va bang can doi ke toan.pdf", "CIC khach hang vay.pdf",
    "Bao cao ket qua kinh doanh.pdf", "Chung thu dinh gia TSBD.pdf",
    "Bang ke xuat nhap ton.xlsx",
]
BODIES = {
    "": "",
    "bctc": "BÁO CÁO TÀI CHÍNH\nBẢNG CÂN ĐỐI KẾ TOÁN\nNỢ PHẢI TRẢ\nVỐN CHỦ SỞ HỮU\n"
            "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\nPhải trả người bán\nSố dư cuối kỳ",
    "cic": "BÁO CÁO CIC\nquan hệ tín dụng\nnhóm nợ\ntài sản bảo đảm",
}
print()
print("2. 15 tên file thực tế — nhãn có đổi không?")
changed = 0
for fn in NAMES:
    for _, body in BODIES.items():
        rule = rule_classify_document(fn, body)
        # The new flag may only flip calls_llm, never the label the rule returns.
        if rule["filename_decisive"] and rule["document_type"] != filename_keyword_owner(fn):
            changed += 1
            failures.append(f"label: {fn} decisive but label != owner")
print(f"   nhãn bị đổi bởi luật mới: {changed}  (phải là 0)")

# --- 3. VIMID: no longer ambiguous once the two generic keywords are gone ----
# This fixture used to be held up as the genuinely ambiguous case, and the check
# asserted it MUST reach the LLM. Measured again, that ambiguity was one the
# matrix created: "so du dau ky"/"so du cuoi ky" on bang_ke_xuat_nhap_ton_cong_no
# appear in every set of accounting records, enough to put that type at 6 against
# so_chi_tiet's 5 — so the rule picked the WRONG type for a document opening with
# "SỔ CHI TIẾT TÀI KHOẢN" and named "so_chi_tiet". The LLM call existed to correct
# the rule's own mistake. With those keywords gone it scores 5 to 4 the right way,
# so the expectation is inverted.
SO_CHI_TIET_BODY = (
    "SỔ CHI TIẾT TÀI KHOẢN\nTài khoản 131 - Phải thu khách hàng\n"
    "Số dư đầu kỳ / Số dư cuối kỳ\nCông nợ phải thu, công nợ phải trả\n"
    "Phải trả người bán"
)
rule = rule_classify_document("VIMID_so_chi_tiet.xlsx", SO_CHI_TIET_BODY)
vimid_llm = calls_llm(rule, with_new_rule=True)
print()
print("3. VIMID_so_chi_tiet.xlsx (rule tự phân định được)")
print(f"   rule chọn      : {rule['document_type']}")
print(f"   chủ tên file   : {filename_keyword_owner('VIMID_so_chi_tiet.xlsx')}")
print(f"   filename_decisive: {rule['filename_decisive']}")
print(f"   nhãn rule      : {'ĐÚNG' if rule['document_type'] == 'so_chi_tiet_khoan_muc_khac' else 'SAI'}")
print(f"   gọi LLM?       : {'CÓ' if vimid_llm else 'KHÔNG (rule đã đủ chắc)'}")
# What must hold is that the LABEL is right, not whether the LLM was called.
if rule["document_type"] != "so_chi_tiet_khoan_muc_khac":
    failures.append(f"VIMID label wrong: {rule['document_type']}")

# --- 4. saving on the well-named files ---------------------------------------
print()
print("4. Tiết kiệm trên tên file thực tế (nội dung BCTC dày)")
body = BODIES["bctc"]
for fn in NAMES:
    r = rule_classify_document(fn, body)
    before, after = calls_llm(r, with_new_rule=False), calls_llm(r, with_new_rule=True)
    if before != after:
        print(f"   TIẾT KIỆM: {fn:34} -> {r['document_type']} (conf={r['confidence']:.2f})")

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")
