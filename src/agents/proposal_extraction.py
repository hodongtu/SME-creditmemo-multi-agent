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

from src.agents.structured_extraction import (
    build_extraction_chain,
    resolve_money_multiplier,
    run_extraction,
)

REQUIRED_TOP_LEVEL_KEYS = {
    "capital_plan",
    "business_plan",
    "plan_efficiency",
    "repayment_plan",
    "collateral",
    "credit_request",
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
- KHÔNG tự quy đổi. Ghi con số ĐÚNG NHƯ IN trên giấy: mục ghi "240.800 triệu
  đồng" thì trả 240800, KHÔNG phải 240800000000. Hệ thống tự nhân theo
  "source_unit" — bạn quy đổi thêm lần nữa là số sai gấp triệu lần.
- "source_unit" của TỪNG khối ghi đơn vị in ngay trên bảng/mục đó:
  "dong" | "trieu dong" | "ty dong". Mục không chú thích đơn vị nào thì để "dong".

QUY TẮC CHUNG:
- Chỉ trích thứ có trên giấy. Ô trống thì để null, KHÔNG suy diễn, KHÔNG lấy số
  từ mục khác sang.
- Liệt kê HẾT các dòng của bảng gói thầu và bảng tài sản bảo đảm, không rút gọn.
- Giữ nguyên tên gói thầu/công trình và tên tài sản như bản gốc.
- "page" là số trang in ở chân trang (vd "Trang số: 4/6" -> 4), null nếu không rõ.

Trả về CHÍNH XÁC JSON theo schema sau, không thêm text nào khác:
{{
  "capital_plan": {{
    "total": <số như in trên giấy, hoặc null>,
    "own_capital": <số như in trên giấy, hoặc null>,
    "loan_capital": <số như in trên giấy, hoặc null>,
    "other_capital": <số như in trên giấy, hoặc null>,
    "source_unit": "dong | trieu dong | ty dong",
    "page": <số nguyên hoặc null>
  }},
  "business_plan": {{
    "plan_year": "vd: 2026, hoặc ''",
    "narrative": "mô tả phương án/dự án, hoặc ''",
    "sections": [
      {{"title": "vd: Các gói thầu/công trình đã ký",
        "items": [
          {{"name": "tên gói thầu/công trình",
            "contract_value": <số như in trên giấy, hoặc null>,
            "planned_value": <giá trị dự kiến thực hiện năm kế hoạch, số như in trên giấy, hoặc null>,
            "note": "ghi chú hoặc ''"}}
        ],
        "total_contract_value": <số như in trên giấy, hoặc null>,
        "total_planned_value": <số như in trên giấy, hoặc null>}}
    ],
    "source_unit": "dong | trieu dong | ty dong",
    "page": <số nguyên hoặc null>
  }},
  "plan_efficiency": {{
    "revenue": <số như in trên giấy, hoặc null>,
    "cogs": <giá vốn, số như in trên giấy, hoặc null>,
    "selling_admin_expense": <chi phí bán hàng và quản lý, số như in trên giấy, hoặc null>,
    "interest_expense": <chi phí lãi vay, số như in trên giấy, hoặc null>,
    "depreciation": <khấu hao, số như in trên giấy, hoặc null>,
    "profit": <lợi nhuận, số như in trên giấy, hoặc null>,
    "source_unit": "dong | trieu dong | ty dong",
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
        "value": <số như in trên giấy, hoặc null>,
        "owner": "chủ sở hữu hoặc ''",
        "status": "tình trạng cầm cố/thế chấp hoặc ''",
        "note": "ghi chú hoặc ''"}}
    ],
    "total_value": <số như in trên giấy, hoặc null>,
    "source_unit": "dong | trieu dong | ty dong",
    "page": <số nguyên hoặc null>
  }},
  "credit_request": {{
    "total_limit": <tổng mức/hạn mức đề nghị, số như in trên giấy, hoặc null>,
    "facilities": [
      {{"name": "vd: Hạn mức cho vay",
        "amount": <số như in trên giấy, hoặc null>,
        "method": "phương thức cho vay hoặc ''",
        "tenor": "thời hạn hoặc ''"}}
    ],
    "source_unit": "dong | trieu dong | ty dong",
    "page": <số nguyên hoặc null>
  }},
  "extraction_notes": ["ghi chú về mục thiếu, không chắc chắn, hoặc OCR kém"]
}}
"""


def _unit_multiplier(source_unit: Any) -> int:
    """Multiplier that turns a block's stated unit into đồng, 1 when unknown."""

    return resolve_money_multiplier(source_unit)


def _scale(value: Any, multiplier: int) -> Any:
    if multiplier == 1 or not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return value * multiplier


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using each block's own source_unit, in place.

    The prompt asks for the figure exactly as printed plus the unit the page
    states; the conversion is here and nowhere else. It used to be in both — the
    prompt said convert *and* record the unit, and this then converted again — so
    a model that obeyed produced a figure a million times too large.

    Blocks are scaled independently because the form genuinely mixes units
    between adjacent tables: the tender table is in đồng while "Hiệu quả của
    phương án" right below it is in triệu.
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
