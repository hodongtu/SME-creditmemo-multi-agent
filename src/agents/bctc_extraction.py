"""LLM extraction of full BCTC (báo cáo tài chính) bundles into structured JSON.

Runs once per document whose matrix type is flagged ``bctc_extraction``
(see src/matrix/document_matrix.yaml and ``is_bctc_type``),
turning noisy raw OCR text into a compact structured record — report type, period,
audit opinion, the 3 core statements, and a bounded summary of the notes section —
so FINANCIAL_ANALYSIS_AGENT can consume clean data instead of a multi-page raw dump.
"""

from __future__ import annotations

from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

REQUIRED_TOP_LEVEL_KEYS = {
    "document_type",
    "reporting_period",
    "audit_opinion",
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "notes_summary",
}

BCTC_EXTRACTION_SYSTEM_PROMPT = """
Bạn trích xuất dữ liệu có cấu trúc từ văn bản OCR thô của một bộ Báo cáo tài
chính (BCTC) doanh nghiệp Việt Nam, phục vụ thẩm định tín dụng SME.

YÊU CẦU QUAN TRỌNG NHẤT — TRÍCH XUẤT ĐẦY ĐỦ:
Với mỗi bảng (cân đối kế toán, kết quả kinh doanh, lưu chuyển tiền tệ), liệt kê
ĐẦY ĐỦ TỪNG DÒNG chỉ tiêu xuất hiện trong bảng, theo đúng thứ tự trong tài liệu
— cả dòng tổng hợp lẫn dòng chi tiết. Một bảng cân đối kế toán đầy đủ thường có
40-60 dòng; nếu bạn chỉ trả về vài dòng tổng hợp là SAI. Tuyệt đối KHÔNG tóm
lược, KHÔNG chọn lọc "các chỉ tiêu quan trọng", KHÔNG bỏ dòng nào có số liệu.

Chỉ dùng dữ kiện có trong văn bản nguồn. Không tự suy diễn hay bịa số liệu.
Nếu một trường không xác định được, để giá trị null (hoặc mảng/chuỗi rỗng),
KHÔNG bỏ qua trường đó.

Toàn bộ giá trị tiền tệ ghi bằng SỐ NGUYÊN đơn vị VNĐ (không chia tỷ, không
định dạng dấu phẩy/chấm), giữ đúng dấu (âm cho khoản mục ghi âm/trong ngoặc).

Văn bản OCR có các mốc "--- Page N ---" đánh dấu ranh giới trang, dùng để trích
dẫn nguồn cho người đọc. Với MỖI BẢNG, bắt buộc điền "page" của bảng đó (số
trang nơi bảng bắt đầu). Với từng dòng chỉ tiêu, điền "page" nếu xác định được,
không xác định được thì để null — nhưng việc này TUYỆT ĐỐI KHÔNG được làm giảm
số dòng bạn trích xuất; đầy đủ dòng quan trọng hơn đầy đủ số trang. Không bịa
số trang.

Cấu trúc "notes_summary" tóm tắt phần Thuyết minh báo cáo tài chính — đây
thường là phần dài nhất, chỉ giữ lại nội dung có ý nghĩa cho thẩm định tín
dụng (không chép lại nguyên văn): chính sách kế toán trọng yếu, giao dịch bên
liên quan, nợ tiềm tàng, sự kiện sau ngày kết thúc kỳ, và chi tiết các khoản
mục lớn (vay nợ, phải thu/phải trả lớn, hàng tồn kho...). Dùng
"other_material_disclosures" cho bất kỳ điểm quan trọng nào khác không thuộc
các mục trên — không được bỏ sót thông tin trọng yếu chỉ vì nó không khớp một
mục có sẵn.

Trả về CHÍNH XÁC JSON theo schema sau, không thêm text nào khác:
{{
  "document_type": "BCTC hợp nhất | BCTC riêng lẻ | không xác định",
  "reporting_period": {{
    "period_label": "vd: Năm 2024",
    "start_date": "YYYY-MM-DD hoặc null",
    "end_date": "YYYY-MM-DD hoặc null",
    "comparative_period_label": "vd: Năm 2023, hoặc null"
  }},
  "audit_opinion": {{
    "is_audited": true hoặc false,
    "opinion_type": "chấp nhận toàn phần | ngoại trừ | không đủ cơ sở | trái ngược | không xác định",
    "auditor_name": "tên công ty kiểm toán hoặc null",
    "notes": "ghi chú ngắn nếu có, hoặc chuỗi rỗng",
    "page": <số trang nguyên hoặc null>
  }},
  "balance_sheet": {{
    "unit": "VNĐ",
    "page": <số trang nơi bảng bắt đầu, bắt buộc nếu xác định được>,
    "years": ["danh sách các năm/kỳ xuất hiện trong bảng"],
    "line_items": [
      {{"label": "tên chỉ tiêu", "code": "mã số nếu có hoặc null",
        "values": {{"<năm>": <số VNĐ>}}, "page": <số trang nguyên hoặc null>}}
      // LIỆT KÊ HẾT MỌI DÒNG CỦA BẢNG, không rút gọn
    ]
  }},
  "income_statement": {{"unit": "VNĐ", "page": <số trang>, "years": [], "line_items": []}},
  "cash_flow_statement": {{"unit": "VNĐ", "page": <số trang>, "years": [], "line_items": []}},
  "notes_summary": {{
    "accounting_policies": "tóm tắt ngắn gọn hoặc chuỗi rỗng (kèm '(trang N)' ở cuối nếu xác định được)",
    "related_party_transactions": [
      {{"counterparty": "...", "nature": "...", "amount": <số VNĐ hoặc null>, "year": "...",
        "page": <số trang nguyên hoặc null>}}
    ],
    "contingent_liabilities": ["... (trang N)"],
    "subsequent_events": ["... (trang N)"],
    "key_item_breakdowns": [
      {{"item": "vd: Vay và nợ thuê tài chính", "breakdown": "...", "amount": <số VNĐ hoặc null>,
        "page": <số trang nguyên hoặc null>}}
    ],
    "other_material_disclosures": ["... (trang N)"]
  }},
  "extraction_notes": ["ghi chú về dữ liệu thiếu, không chắc chắn, hoặc OCR kém"]
}}

Với các mục dạng chuỗi tự do (contingent_liabilities, subsequent_events,
other_material_disclosures, accounting_policies): nếu xác định được số trang,
thêm "(trang N)" vào cuối chuỗi đó; nếu không, để nguyên chuỗi không có phần
này — không ghi "(trang null)" hay tương tự.
"""


def build_bctc_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain, mirroring the document classifier chain."""

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", BCTC_EXTRACTION_SYSTEM_PROMPT),
            (
                "human",
                """
                Tên file: {filename}

                Văn bản OCR (BCTC):
                {content}

                Trích xuất theo đúng schema JSON đã mô tả.
                """,
            ),
        ]
    )
    return prompt | llm | JsonOutputParser()


def extract_bctc_structured_data(
    chain: Any,
    filename: str,
    content: str,
) -> tuple[dict[str, Any] | None, str]:
    """Run the extraction chain and validate its shape.

    Never raises — returns (None, error_message) on any failure so the caller
    always has a clean signal to fall back to raw OCR text for this document.
    """

    if chain is None:
        return None, "No BCTC extraction LLM configured."
    try:
        result = chain.invoke({"filename": filename, "content": content})
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]

    if not isinstance(result, dict):
        return None, f"Extraction returned non-dict result: {type(result).__name__}"
    missing = REQUIRED_TOP_LEVEL_KEYS - result.keys()
    if missing:
        return None, f"Extraction result missing keys: {sorted(missing)}"
    return result, ""
