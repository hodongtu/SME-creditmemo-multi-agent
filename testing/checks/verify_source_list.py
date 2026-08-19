"""The source list is computed in code, and drops no document."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import re; from src.utils.source_list import build_source_lines
from src.agents.supervisor import Supervisor as S
from src.config import Config
from src.types import ClassifiedDocument

fails = []
def check(name, ok, detail=""):
    print(f"   {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not ok: fails.append(name)

def docs(pairs):
    return [ClassifiedDocument(path=f"/x/{f}", filename=f, content="c",
            agent="FINANCIAL_ANALYSIS_AGENT", reasoning="", confidence=1.0,
            document_type=t) for f, t in pairs]

TKT = [(f"TKT_{m:02d}:2025.xlsx", "to_khai_thue_gtgt") for m in range(1, 13)] \
    + [(f"TKT_{m:02d}:2026.xlsx", "to_khai_thue_gtgt") for m in range(1, 5)]

print("1. Đúng ca 16 file của người dùng")
lines = build_source_lines(TKT)
check("gộp thành 1 dòng", lines == ["TKT 01-12/2025, 01-04/2026"], str(lines))

print("\n2. KHÔNG bỏ sót — giải nén ngược danh sách rồi đếm")

def expand_count(line):
    """How many documents a collapsed line actually stands for."""
    # '- TKT 01-12/2025, 01-04/2026' -> 12 + 4 ;  'BCTC VVS 2024-2026' -> 3
    total = 0
    for chunk in re.findall(r"((?:\d{1,2}(?:-\d{1,2})?)(?:,\s*\d{1,2}(?:-\d{1,2})?)*)/(\d{4})", line):
        for run in chunk[0].split(","):
            run = run.strip()
            if "-" in run:
                a, b = (int(x) for x in run.split("-")); total += b - a + 1
            else: total += 1
    if total: return total
    m = re.search(r"((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2})", line)
    if m: return int(m.group(2)) - int(m.group(1)) + 1
    years = re.findall(r"(?:19|20)\d{2}", line)
    return len(years) if years else 1

CASES = {
    "16 TKT": TKT,
    "BCTC 3 năm": [(f"BCTC_VVS_{y}.pdf", "bao_cao_tai_chinh") for y in (2024, 2025, 2026)],
    "BCTC 2 năm": [(f"BCTC_VVS_{y}.pdf", "bao_cao_tai_chinh") for y in (2024, 2025)],
    "tháng rời rạc": [(f"TKT_{m:02d}.2025.xlsx", "to_khai_thue_gtgt") for m in (1, 2, 5, 9)],
    "một file": [("VIMID_so_chi_tiet.xlsx", "so_chi_tiet_khoan_muc_khac")],
    "tên không đồng nhất": [("TKT_01.2025.xlsx", "to_khai_thue_gtgt"),
                            ("ToKhaiThue_02.2025.xlsx", "to_khai_thue_gtgt")],
    "không có kỳ": [("Giay_DKKD.pdf", "giay_dang_ky_kinh_doanh"),
                    ("Dieu_le.pdf", "giay_dang_ky_kinh_doanh")],
    "trộn 3 loại": TKT[:4] + [("BCTC_VVS_2025.pdf", "bao_cao_tai_chinh"),
                              ("VIMID_so_chi_tiet.xlsx", "so_chi_tiet_khoan_muc_khac")],
    "cả năm 2025 + 2026 đủ 24": [(f"TKT_{m:02d}.{y}.xlsx", "to_khai_thue_gtgt")
                                 for y in (2025, 2026) for m in range(1, 13)],
}
for name, pairs in CASES.items():
    out = build_source_lines(pairs)
    got = sum(expand_count(l) for l in out)
    check(f"{name:24} {len(pairs):2} file -> {len(out)} dòng, giải nén = {got}",
          got == len(pairs), "" if got == len(pairs) else f"THIẾU {len(pairs)-got}")

print("\n3. Ca 16 file: mọi tháng đều còn mặt")
text = build_source_lines(TKT)[0]
check("phủ 01-12/2025", "01-12/2025" in text)
check("phủ 01-04/2026", "01-04/2026" in text)
check("KHÔNG phải kết quả sai cũ", text != "TKT 01-04/2025", text)

print("\n4. Đường lùi không bịa nhãn")
# One line per file, and since 410d1aa without the extension (section 9 covers
# the stripping in detail).
check("tên không đồng nhất -> liệt kê từng file",
      build_source_lines(CASES["tên không đồng nhất"]) == ["TKT_01.2025", "ToKhaiThue_02.2025"])
check("không đọc được kỳ -> liệt kê từng file",
      build_source_lines(CASES["không có kỳ"]) == ["Giay_DKKD", "Dieu_le"])

print("\n5. Loại khác nhau nằm ở dòng khác nhau")
out = build_source_lines(CASES["trộn 3 loại"])
check("3 loại -> 3 dòng", len(out) == 3, str(out))

print("\n6. Mọi dấu ngăn cho cùng kết quả")
base = None
for sep in (":", "/", ".", "-", "_"):
    pairs = [(f"TKT_{m:02d}{sep}2025.xlsx", "to_khai_thue_gtgt") for m in range(1, 13)]
    got = build_source_lines(pairs)[0]
    if base is None: base = got
    check(f"dấu {sep!r}", got == base, got)

print("\n7. Khối vào đủ 5 specialist và nằm trong prompt")
sup = S(Config())
d = docs(TKT)
blk = S._build_source_list_block(d)
check("khối có dòng đã gộp", "TKT 01-12/2025, 01-04/2026" in blk)
check("khối KHÔNG chứa dấu ngoặc placeholder", "{" not in blk and "}" not in blk)
for agent in ("BUSINESS_ACTIVITY_AGENT", "FINANCIAL_ANALYSIS_AGENT",
              "CREDIT_RELATIONSHIP_AGENT", "CREDIT_PROPOSAL_AGENT", "RISK_ASSESSMENT_AGENT"):
    for doc in d: doc.agent_relevance = {agent: "R"}
    ui = sup._build_user_input("phân tích", d, "", agent, "", {})
    check(f"{agent} nhận khối", "TKT 01-12/2025, 01-04/2026" in ui)

print("\n8. Prompt 5 specialist không còn ví dụ cũ")
from src.agents.specialist import (BusinessActivityAnalysis, FinancialAnalysis,
    CreditRelationshipAnalysis, CreditProposalAnalysis, RiskAssessment)
for C in (BusinessActivityAnalysis, FinancialAnalysis, CreditRelationshipAnalysis,
          CreditProposalAnalysis, RiskAssessment):
    sp = C(llm=None).system_prompt
    check(f"{C.__name__} sạch ví dụ", "TKT 01-04/2025" not in sp and "DANH SÁCH NGUỒN" in sp)

print("\n9. Bỏ đuôi file — và KHÔNG cắt nhầm")
from src.utils.source_list import _strip_extension
from src.utils.common import SUPPORTED_EXTENSIONS

check("một file -> hết đuôi",
      build_source_lines([("VIMID_so_chi_tiet.xlsx", "sct")]) == ["VIMID_so_chi_tiet"])
check("tên lệch -> hết đuôi",
      build_source_lines([("TKT_01.2025.xlsx", "t"), ("ToKhaiThue_02.2025.xlsx", "t")])
      == ["TKT_01.2025", "ToKhaiThue_02.2025"])
check("không đọc được kỳ -> hết đuôi",
      build_source_lines([("Giay_DKKD.pdf", "d"), ("Dieu_le.pdf", "d")]) == ["Giay_DKKD", "Dieu_le"])

# Where a bare splitext breaks: a file with NO extension whose name ends in a year.
for name in ("TKT_01.2025", "Bao cao Q1.2025", "Giay DKKD"):
    check(f"{name!r} giữ nguyên (không có đuôi thật)", _strip_extension(name) == name,
          _strip_extension(name))

for ext in sorted(SUPPORTED_EXTENSIONS):
    check(f"cắt {ext}", _strip_extension(f"BCTC_2024{ext}") == "BCTC_2024")
for ext in (".2025", ".final", ".bak"):
    check(f"KHÔNG cắt {ext}", _strip_extension(f"BCTC{ext}") == f"BCTC{ext}")
check("đuôi viết hoa vẫn cắt", _strip_extension("BCTC_2024.PDF") == "BCTC_2024")

check("va chạm đuôi -> giữ cả hai, phân biệt được",
      build_source_lines([("BCTC.pdf", "b"), ("BCTC.xlsx", "b")]) == ["BCTC.pdf", "BCTC.xlsx"])

print("\n10. Notebook vẫn import được đường cũ")
from src.agents.document_classification import SUPPORTED_EXTENSIONS as VIA_AGENTS
check("document_classification vẫn export", VIA_AGENTS is SUPPORTED_EXTENSIONS)

print("\n" + "=" * 66)
if fails: print("❌ HỎNG:", *fails, sep="\n   - "); sys.exit(1)
print("✅ Danh sách nguồn tính bằng code, không bỏ sót")
