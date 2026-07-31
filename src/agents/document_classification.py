"""Document discovery + rule-based classification (extracted from the notebook)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

from src.utils.paths import PROJECT_ROOT

from src.agents.financial_ratio_calculator import (
    FinancialRatioCalculator,
)
from src.utils.extractors import extract_document_text

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".txt", ".md"}
VALID_DOCUMENT_AGENTS = {
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL",
    "RISK_ASSESSMENT_AGENT",
    "GENERAL_CONTEXT",
}

FINANCIAL_KEYWORDS = [
    "finance",
    "financial",
    "financial statement",
    "financial analysis",
    "balance sheet",
    "income statement",
    "cash flow",
    "audit report",
    "bank statement",
    "ledger",
    "vat",
    "revenue",
    "profit",
    "expense",
    "assets",
    "liabilities",
    "receivable",
    "payable",
    "bctc",
    "báo cáo tài chính",
    "bảng cân đối",
    "kết quả kinh doanh",
    "lưu chuyển tiền tệ",
    "sổ chi tiết",
    "sổ cái",
    "tờ khai thuế",
    "doanh thu",
    "lợi nhuận",
    "tài sản",
    "nợ phải trả",
    "công nợ",
    "phát sinh nợ",
    "phát sinh có",
    "dư nợ cuối kỳ",
    "bctc",
    "kqkd",
    "lctt",
    "cdkt",
    "tmbctc",
    "gtgt",
    "bảng cân đối kế toán",
    "báo cáo kết quả kinh doanh",
    "báo cáo lưu chuyển tiền tệ",
    "thuyết minh báo cáo tài chính",
    "tờ khai thuế gtgt",
    "khấu hao",
    "hàng tồn kho",
    "vốn chủ sở hữu",
    "giá vốn hàng bán",
    "ebitda",
]
BUSINESS_KEYWORDS = [
    "business activity",
    "business operation",
    "contract",
    "invoice",
    "purchase order",
    "supplier",
    "customer",
    "sales",
    "market",
    "hợp đồng",
    "hóa đơn",
    "nhà cung cấp",
    "khách hàng",
    "hoạt động kinh doanh",
    "sản phẩm",
    "sản lượng",
    "đơn hàng",
    "đăng ký kinh doanh",
    "giấy chứng nhận đăng ký doanh nghiệp",
    "giấy phép kinh doanh",
    "ngành nghề kinh doanh",
    "công suất",
    "thị trường",
    "đối thủ cạnh tranh",
    "hồ sơ năng lực",
    "báo giá",
    "đơn đặt hàng",
]
CREDIT_RELATIONSHIP_DOCUMENT_KEYWORDS = [
    "credit relationship",
    "credit history",
    "bureau",
    "cic",
    "pcb",
    "t24",
    "facility id",
    "outstanding balance",
    "repayment status",
    "overdue",
    "debt group",
    "quan hệ tín dụng",
    "lịch sử tín dụng",
    "trung tâm thông tin tín dụng",
    "dư nợ",
    "nợ quá hạn",
    "nhóm nợ",
    "tổ chức tín dụng",
    "cic",
    "pcb",
    "nợ xấu",
    "nợ cần chú ý",
    "hạn mức tín dụng",
    "dư nợ vay",
    "sao kê tín dụng",
    "lịch sử vay",
    "báo cáo tín dụng",
]
CREDIT_PROPOSAL_DOCUMENT_KEYWORDS = [
    "credit proposal",
    "credit facility proposal",
    "facility proposal",
    "loan proposal",
    "proposed credit limit",
    "loan amount proposal",
    "đề xuất tín dụng",
    "đề xuất cấp tín dụng",
    "báo cáo đề xuất cấp tín dụng",
    "hạn mức đề xuất",
    "số tiền đề xuất",
    "mức cấp tín dụng đề xuất",
    "hdtd",
    "tờ trình cấp tín dụng",
    "hợp đồng tín dụng",
    "giấy đề nghị vay vốn",
    "đề nghị cấp tín dụng",
    "phương án vay vốn",
    "phương án sử dụng vốn",
]
RISK_KEYWORDS = [
    "credit memo",
    "risk assessment",
    "collateral",
    "repayment source",
    "tờ trình",
    "đánh giá rủi ro",
    "tài sản bảo đảm",
    "khoản vay",
    "phương án trả nợ",
    "tsbd",
    "tài sản thế chấp",
    "định giá tài sản",
    "nguồn trả nợ",
    "xếp hạng tín dụng",
    "thẩm định",
    "tờ trình thẩm định",
    "cảnh báo rủi ro",
    "biện pháp giảm thiểu rủi ro",
]
DOCUMENT_KEYWORD_GROUPS = {
    "FINANCIAL_ANALYSIS_AGENT": FINANCIAL_KEYWORDS,
    "BUSINESS_ACTIVITY_AGENT": BUSINESS_KEYWORDS,
    "CREDIT_RELATIONSHIP_AGENT": CREDIT_RELATIONSHIP_DOCUMENT_KEYWORDS,
    "CREDIT_PROPOSAL": CREDIT_PROPOSAL_DOCUMENT_KEYWORDS,
    "RISK_ASSESSMENT_AGENT": RISK_KEYWORDS,
}


def compute_file_hash(path: str) -> str:
    """Compute SHA-256 from file content."""

    sha256 = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def resolve_input_path(raw_path: str) -> Path:
    """Resolve relative notebook inputs against the project root."""

    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def discover_documents(input_paths: list[str], max_files: int = 50) -> list[str]:
    """Find supported documents from files or folders."""

    files = []
    for raw_path in input_paths:
        path = resolve_input_path(raw_path)
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(str(path))
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(str(item))

    seen = set()
    deduped = []
    for file in files:
        if file not in seen:
            deduped.append(file)
            seen.add(file)

    if len(deduped) > max_files:
        dropped = len(deduped) - max_files
        print(
            f"[discover_documents] WARNING: discovered {len(deduped)} documents; "
            f"processing the first {max_files} and DROPPING {dropped}. "
            f"Increase MAX_FILES to include them."
        )
        deduped = deduped[:max_files]
    return deduped


def normalize_text(text: str) -> str:
    """Lowercase and strip Vietnamese accents for robust keyword matching.

    OCR output frequently drops or mangles diacritics, so matching must be
    accent-insensitive (e.g. "báo cáo" and "bao cao" must match the same key).
    """

    # Vietnamese "đ"/"Đ" do NOT decompose under NFD, so map them explicitly.
    lowered = (text or "").lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    stripped = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )
    # Collapse separators/punctuation to single spaces so filename tokens
    # ("BCTC_2024", "de_xuat_cap_tin_dung") match space-delimited keywords.
    return re.sub(r"[^0-9a-z]+", " ", stripped).strip()


# A hit in the filename is a much stronger routing signal than one in the body.
FILENAME_KEYWORD_WEIGHT = 3


def document_keyword_scores(filename: str, content: str) -> dict[str, int]:
    """Score each document target by weighted keyword hits.

    Filename hits are weighted higher than body hits because a well-named file
    ("BCTC_2024.pdf", "CIC_report.pdf") is a strong, low-noise routing signal,
    whereas body text of one domain often mentions terms from another.
    """

    name = normalize_text(filename)
    body = normalize_text(content)
    scores: dict[str, int] = {}
    for agent, keywords in DOCUMENT_KEYWORD_GROUPS.items():
        normalized_keywords = [normalize_text(keyword) for keyword in keywords]
        name_hits = sum(keyword in name for keyword in normalized_keywords)
        body_hits = sum(keyword in body for keyword in normalized_keywords)
        scores[agent] = FILENAME_KEYWORD_WEIGHT * name_hits + body_hits
    return scores


DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT = """
You classify extracted text from SME underwriting documents.

Choose exactly one target agent:
- FINANCIAL_ANALYSIS_AGENT: balance sheet, income statement, cash flow,
  audit report, financial statement notes, subsidiary ledger, detailed ledger,
  VAT declaration, bank statement, receivables/payables, revenue, expenses,
  assets, liabilities, and capital structure.

- BUSINESS_ACTIVITY_AGENT: business model, business registration, contracts,
  invoices, purchase orders, customers, suppliers, sales, operations,
  production, market, products.
  
- CREDIT_RELATIONSHIP_AGENT: internal T24 credit relationship exports,
  CIC/PCB bureau reports, existing facilities, outstanding balance,
  repayment status, overdue debt, debt group, or credit institutions.

- CREDIT_PROPOSAL: credit proposal, loan proposal, proposed credit limit,
  facility terms, proposed amount, or đề xuất cấp tín dụng documents.

- RISK_ASSESSMENT_AGENT: collateral, repayment source, risk report, and
  underwriting memo documents.

- GENERAL_CONTEXT: useful contextual document but not clearly one of the above.

Return JSON only with:
{{
  "agent": "...",
  "reasoning": "short reason",
  "confidence": 0.0
}}
"""


def rule_classify_document(filename: str, content: str) -> dict[str, Any]:
    """Classify document using deterministic keyword signals."""

    scores = document_keyword_scores(filename, content)
    best_agent, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score == 0:
        return {
            "agent": "GENERAL_CONTEXT",
            "reasoning": "No strong keyword signal found.",
            "confidence": 0.0,
            "scores": scores,
        }

    sorted_scores = sorted(scores.values(), reverse=True)
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0
    margin = best_score - second_score
    # Confidence is driven by how dominant the top agent is (margin ratio),
    # tempered by how much absolute evidence exists. Robust to the filename
    # weighting because it is scale-relative, not an absolute score threshold.
    margin_ratio = margin / best_score
    confidence = min(0.95, 0.40 + 0.35 * margin_ratio + 0.03 * min(best_score, 6))
    if best_score < FILENAME_KEYWORD_WEIGHT:
        # Weak absolute evidence (fewer than one filename hit's worth).
        confidence = min(confidence, 0.6)

    return {
        "agent": best_agent,
        "reasoning": (
            "Rule-based classification from keyword signals "
            f"(score={best_score}, margin={margin}, ratio={margin_ratio:.2f})."
        ),
        "confidence": confidence,
        "scores": scores,
    }
