"""LLM extraction of the credit application form into structured JSON.

Runs once per document whose matrix type is flagged ``proposal_extraction``
(see src/matrix/document_matrix.yaml and ``is_proposal_type``). Sections B, C
and D of the form carry the numbers the credit proposal is built on — the
capital plan, next year's contracted pipeline, the projected P&L, the repayment
plan, the collateral and the facility being asked for. Left as raw OCR those are
exactly the figures an LLM is most tempted to invent.

The form mixes money units *within a single section*: the pipeline table is in
đồng, the efficiency block in triệu đồng, the collateral table in tỷ đồng. Every
amount is therefore normalised to đồng on the way out, with the unit it was read
in kept alongside so a number can be traced back to the page.
"""

from __future__ import annotations

import re
from typing import Any

from src.agents.structured_extraction import build_extraction_chain, run_extraction
from src.utils.common import normalize_text

REQUIRED_TOP_LEVEL_KEYS = {
    "capital_plan",
    "business_plan",
    "plan_efficiency",
    "repayment_plan",
    "collateral",
    "credit_request",
}

# Multipliers for the units the form actually uses. Applied only when a block
# reports a unit other than đồng — the prompt asks for đồng, this is the net.
_UNIT_MULTIPLIERS = {
    "dong": 1,
    "vnd": 1,
    "trieu dong": 10**6,
    "trieu": 10**6,
    "ty dong": 10**9,
    "ty": 10**9,
}

# Where amounts live, per block: scalar fields, and list fields with their own
# amount keys. Used by the unit normaliser.
_AMOUNT_FIELDS: dict[str, tuple[str, ...]] = {
    "capital_plan": ("total", "own_capital", "loan_capital", "other_capital"),
    "business_plan": ("total_contract_value", "total_planned_value"),
    "plan_efficiency": (
        "revenue", "cogs", "selling_admin_expense",
        "interest_expense", "depreciation", "profit",
    ),
    "collateral": ("total_value",),
    "credit_request": ("total_limit",),
}

PROPOSAL_EXTRACTION_SYSTEM_PROMPT = """
Bạn trích xuất Giấy đề nghị cấp tín dụng của khách hàng doanh nghiệp thành JSON.

Chỉ lấy các mục B, C, D. Bỏ qua mục A (thông tin khách hàng, người có liên quan)
và mọi phụ lục khác.

- Mục B: THÔNG TIN VỀ PHƯƠNG ÁN SỬ DỤNG VỐN VAY/TÍN DỤNG VÀ HIỆU QUẢ KINH DOANH
- Mục C: TÀI SẢN BẢO ĐẢM
- Mục D: ĐỀ NGHỊ CẤP TÍN DỤNG

QUY TẮC ĐƠN VỊ TIỀN — quan trọng nhất:
Biểu mẫu này dùng nhiều đơn vị khác nhau ngay trong cùng một mục: bảng gói thầu
thường ghi bằng ĐỒNG, phần "Hiệu quả của phương án" ghi bằng TRIỆU ĐỒNG, bảng tài
sản bảo đảm ghi bằng TỶ ĐỒNG.
- MỌI số tiền trong JSON phải quy về ĐỒNG (VNĐ). "240.800 triệu đồng" -> 240800000000.
- Ghi đơn vị đọc được trên bản gốc vào "source_unit" của khối đó ("đồng",
  "triệu đồng", "tỷ đồng"), để người đọc truy ngược được.
- Không đoán đơn vị. Không thấy ghi đơn vị thì để source_unit là "" và giữ nguyên
  con số như trên giấy.

QUY TẮC CHUNG:
- Chỉ trích thứ có trên giấy. Ô trống thì để null, KHÔNG suy diễn, KHÔNG lấy số
  từ mục khác sang.
- Liệt kê HẾT các dòng của bảng gói thầu và bảng tài sản bảo đảm, không rút gọn.
- Giữ nguyên tên gói thầu/công trình và tên tài sản như bản gốc.
- "page" là số trang in ở chân trang (vd "Trang số: 4/6" -> 4), null nếu không rõ.

Trả về CHÍNH XÁC JSON theo schema sau, không thêm text nào khác:
{{
  "capital_plan": {{
    "total": <số VNĐ hoặc null>,
    "own_capital": <số VNĐ hoặc null>,
    "loan_capital": <số VNĐ hoặc null>,
    "other_capital": <số VNĐ hoặc null>,
    "source_unit": "đồng | triệu đồng | tỷ đồng | ''",
    "page": <số nguyên hoặc null>
  }},
  "business_plan": {{
    "plan_year": "vd: 2026, hoặc ''",
    "narrative": "mô tả phương án/dự án, hoặc ''",
    "sections": [
      {{"title": "vd: Các gói thầu/công trình đã ký",
        "items": [
          {{"name": "tên gói thầu/công trình",
            "contract_value": <số VNĐ hoặc null>,
            "planned_value": <giá trị dự kiến thực hiện năm kế hoạch, số VNĐ hoặc null>,
            "note": "ghi chú hoặc ''"}}
        ],
        "total_contract_value": <số VNĐ hoặc null>,
        "total_planned_value": <số VNĐ hoặc null>}}
    ],
    "source_unit": "...",
    "page": <số nguyên hoặc null>
  }},
  "plan_efficiency": {{
    "revenue": <số VNĐ hoặc null>,
    "cogs": <giá vốn, số VNĐ hoặc null>,
    "selling_admin_expense": <chi phí bán hàng và quản lý, số VNĐ hoặc null>,
    "interest_expense": <chi phí lãi vay, số VNĐ hoặc null>,
    "depreciation": <khấu hao, số VNĐ hoặc null>,
    "profit": <lợi nhuận, số VNĐ hoặc null>,
    "source_unit": "...",
    "page": <số nguyên hoặc null>
  }},
  "repayment_plan": {{
    "sources": "nguồn trả nợ, hoặc ''",
    "principal_method": "phương thức trả nợ gốc, hoặc ''",
    "interest_method": "phương thức trả nợ lãi, hoặc ''",
    "page": <số nguyên hoặc null>
  }},
  "collateral": {{
    "items": [
      {{"category": "vd: Bất động sản",
        "description": "mô tả tài sản",
        "value": <số VNĐ hoặc null>,
        "owner": "chủ sở hữu hoặc ''",
        "status": "tình trạng cầm cố/thế chấp hoặc ''",
        "note": "ghi chú hoặc ''"}}
    ],
    "total_value": <số VNĐ hoặc null>,
    "source_unit": "...",
    "page": <số nguyên hoặc null>
  }},
  "credit_request": {{
    "total_limit": <tổng mức/hạn mức đề nghị, số VNĐ hoặc null>,
    "facilities": [
      {{"name": "vd: Hạn mức cho vay",
        "amount": <số VNĐ hoặc null>,
        "method": "phương thức cho vay hoặc ''",
        "tenor": "thời hạn hoặc ''"}}
    ],
    "source_unit": "...",
    "page": <số nguyên hoặc null>
  }},
  "extraction_notes": ["ghi chú về mục thiếu, không chắc chắn, hoặc OCR kém"]
}}
"""


def _unit_multiplier(source_unit: Any) -> int:
    """Multiplier that turns a block's stated unit into đồng, 1 when unknown."""

    return _UNIT_MULTIPLIERS.get(normalize_text(str(source_unit or "")), 1)


def _scale(value: Any, multiplier: int) -> Any:
    if multiplier == 1 or not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return value * multiplier


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using each block's own source_unit, in place.

    The prompt already asks for đồng; this is the safety net for when the model
    echoes the page's unit instead. Blocks are scaled independently because the
    form genuinely mixes units between adjacent tables — treating the document as
    having one unit is how a figure ends up a million times off.
    """

    for block_name, fields in _AMOUNT_FIELDS.items():
        block = result.get(block_name)
        if not isinstance(block, dict):
            continue
        multiplier = _unit_multiplier(block.get("source_unit"))
        if multiplier == 1:
            continue
        for field in fields:
            block[field] = _scale(block.get(field), multiplier)

        if block_name == "business_plan":
            for section in block.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                for key in ("total_contract_value", "total_planned_value"):
                    section[key] = _scale(section.get(key), multiplier)
                for item in section.get("items") or []:
                    if isinstance(item, dict):
                        for key in ("contract_value", "planned_value"):
                            item[key] = _scale(item.get(key), multiplier)
        elif block_name == "collateral":
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    item["value"] = _scale(item.get("value"), multiplier)
        elif block_name == "credit_request":
            for facility in block.get("facilities") or []:
                if isinstance(facility, dict):
                    facility["amount"] = _scale(facility.get("amount"), multiplier)
    return result


def build_proposal_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the credit application form."""

    return build_extraction_chain(PROPOSAL_EXTRACTION_SYSTEM_PROMPT, llm)


def extract_proposal_structured_data(
    chain: Any,
    filename: str,
    content: str,
) -> tuple[dict[str, Any] | None, str]:
    """Run the extraction chain and validate its shape. Never raises."""

    result, error = run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No proposal extraction LLM configured.",
    )
    if result is None:
        return None, error
    return normalize_amounts(result), ""
