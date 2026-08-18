"""LLM extraction of the site-visit report into structured JSON.

Runs once per document whose matrix type is flagged ``sitevisit_extraction``
(see src/matrix/document_matrix.yaml and ``is_sitevisit_type``). The report is
the only document in the set written *after* someone went and looked: it carries
the industry and GSO code, what the customer actually makes, who it buys from and
sells to, and the plan for next year. Every specialist consumes some part of
that, which is why all five read this block.

Two things make it different from the other four passes:

- It is prose, not a form. The other passes lift numbered boxes; this one has to
  find facts spread through paragraphs, so the prompt names the fields rather
  than the section numbers.
- It mixes observation with judgement. The officer's verdict — the risks noted
  on site, the recommendation — sits in ``conclusion`` and nowhere else, kept
  apart on purpose: it is one person's opinion, and an agent that cites it as
  document evidence would be telling the reader the file says something it does
  not. The block header says so out loud.

Next year's plan carries money, and Vietnamese reports write it in triệu or tỷ as
often as in đồng, so amounts are normalised to đồng on the way out — the same net
``proposal_extraction`` needs for the same reason.
"""

from __future__ import annotations

from typing import Any

from src.agents.structured_extraction import (
    build_extraction_chain,
    resolve_money_multiplier,
    run_extraction,
)

REQUIRED_TOP_LEVEL_KEYS = {
    "survey_info",
    "business_profile",
    "supply_chain",
    "business_plan_next_year",
    "lc_terms",
    "conclusion",
}


# Only one block holds money. Kept as a mapping anyway so adding a second block
# later is a one-line change rather than a rewrite of the normaliser.
_AMOUNT_FIELDS: dict[str, tuple[str, ...]] = {
    "business_plan_next_year": (
        "net_revenue",
        "cogs",
        "gross_profit",
        "profit_before_tax",
    ),
}

SITEVISIT_EXTRACTION_SYSTEM_PROMPT = """
Bạn trích xuất Báo cáo khảo sát thực địa của khách hàng doanh nghiệp thành JSON.

Đây là văn bản tự do, không phải biểu mẫu có ô cố định. Thông tin nằm rải rác
trong các đoạn văn — hãy tìm theo NỘI DUNG, đừng tìm theo số thứ tự mục.

QUY TẮC BẮT BUỘC:
- Chỉ ghi thông tin CÓ THẬT trong tài liệu. Không suy đoán, không điền giá trị
  "hợp lý". Không tìm thấy thì để null (hoặc [] với danh sách).
- Tách bạch QUAN SÁT và Ý KIẾN. Mọi đánh giá, nhận định, khuyến nghị của cán bộ
  khảo sát chỉ được đặt trong khối "conclusion". Bốn khối còn lại chỉ chứa dữ
  kiện đọc được.
- Mọi số tiền quy về ĐỒNG. Nếu báo cáo ghi triệu/tỷ thì nhân lên và ghi đơn vị
  gốc vào "source_unit" của khối đó.
- Mã GSO (mã ngành kinh tế theo Tổng cục Thống kê) chỉ ghi khi tài liệu in rõ
  mã đó. Không tự tra, không tự suy từ tên ngành.
- Khối "lc_terms": chỉ điền khi báo cáo NÓI RÕ về hoạt động nhập khẩu và thanh
  toán bằng L/C. Mọi tỷ lệ ghi dạng thập phân (60% -> 0.6). Không suy ra tỷ lệ
  từ danh sách nhà cung cấp nước ngoài — không nêu thì để null, hệ thống sẽ
  dùng giá trị mặc định và ghi rõ đó là giả định.

Trả về JSON đúng cấu trúc sau, không kèm giải thích:

{{
  "survey_info": {{
    "survey_date": "YYYY-MM-DD hoặc nguyên văn nếu không rõ, null nếu không có",
    "officers": ["họ tên cán bộ khảo sát"],
    "location": "địa điểm khảo sát",
    "customer_participants": ["họ tên - chức vụ phía khách hàng"]
  }},
  "business_profile": {{
    "industry": "ngành nghề kinh doanh",
    "gso_code": "mã ngành GSO nếu tài liệu in rõ, null nếu không",
    "main_products": [
      {{"name": "sản phẩm/dịch vụ", "note": "ghi chú nếu có"}}
    ]
  }},
  "supply_chain": {{
    "inputs": [
      {{"supplier": "nhà cung cấp", "item": "mặt hàng", "terms": "điều khoản nếu có"}}
    ],
    "outputs": [
      {{"customer": "khách hàng đầu ra", "item": "mặt hàng", "terms": "điều khoản nếu có"}}
    ]
  }},
  "business_plan_next_year": {{
    "year": "năm kế hoạch, null nếu không nêu",
    "source_unit": "đơn vị tiền ghi trong báo cáo (dong/trieu dong/ty dong)",
    "net_revenue": 0,
    "cogs": 0,
    "gross_profit": 0,
    "profit_before_tax": 0,
    "assumptions": ["căn cứ/giả định của kế hoạch nếu báo cáo có nêu"]
  }},
  "lc_terms": {{
    "import_ratio": <tỷ lệ nhập khẩu trên tổng mua vào, dạng 0.0-1.0, null nếu không nêu>,
    "lc_share_of_import": <tỷ lệ hàng nhập cần mở L/C, 0.0-1.0, null nếu không nêu>,
    "sight_share": <tỷ lệ L/C trả ngay trên doanh số mở L/C, 0.0-1.0, null nếu không nêu>,
    "deferred_share": <tỷ lệ L/C trả chậm, 0.0-1.0, null nếu không nêu>,
    "sight_days": <số ngày trung bình từ mở đến thanh toán L/C trả ngay, null nếu không nêu>,
    "deferred_days": <số ngày trung bình L/C trả chậm, null nếu không nêu>
  }},
  "conclusion": {{
    "overall_assessment": "đánh giá chung của cán bộ khảo sát",
    "risks_noted": ["rủi ro cán bộ ghi nhận tại chỗ"],
    "recommendation": "đề xuất của cán bộ",
    "conditions": ["điều kiện kèm theo nếu có"]
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
    """Convert every amount to đồng using the block's own source_unit, in place."""

    for block_name, fields in _AMOUNT_FIELDS.items():
        block = result.get(block_name)
        if not isinstance(block, dict):
            continue
        multiplier = _unit_multiplier(block.get("source_unit"))
        if multiplier == 1:
            continue
        for field in fields:
            block[field] = _scale(block.get(field), multiplier)
    return result


# Fields in lc_terms that are shares, so must land between 0 and 1.
_RATIO_FIELDS = (
    "import_ratio",
    "lc_share_of_import",
    "sight_share",
    "deferred_share",
)


def normalize_lc_ratios(result: dict[str, Any]) -> dict[str, Any]:
    """Force lc_terms shares onto a 0-1 scale, in place, and say what changed.

    The prompt asks for decimals and a model will still answer 60 for "60%"
    often enough to matter: the credit-need table multiplies these by 100 to
    display and by the projected COGS to size the facility, so an unnoticed 60
    becomes 6000% and a hundredfold LC turnover.

    A value between 1 and 100 can only be a percentage — the scale it is
    supposed to be on stops at 1 — so it is divided and the reinterpretation is
    written into extraction_notes rather than done quietly. Anything above 100,
    or negative, is not a share at all: it is dropped so the calculator falls
    back to its documented default instead of computing on nonsense.
    """

    block = result.get("lc_terms")
    if not isinstance(block, dict):
        return result
    notes = result.setdefault("extraction_notes", [])
    if not isinstance(notes, list):
        notes = result["extraction_notes"] = []

    for field in _RATIO_FIELDS:
        value = block.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if 0 <= value <= 1:
            continue
        if 1 < value <= 100:
            block[field] = value / 100
            notes.append(
                f"lc_terms.{field}: đọc được {value} — hiểu là phần trăm và "
                f"quy về {value / 100}."
            )
        else:
            block[field] = None
            notes.append(
                f"lc_terms.{field}: giá trị {value} không phải tỷ lệ hợp lệ "
                "(ngoài khoảng 0-100%) — đã bỏ, hệ thống dùng mặc định."
            )
    return result


def build_sitevisit_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the site-visit report."""

    return build_extraction_chain(SITEVISIT_EXTRACTION_SYSTEM_PROMPT, llm)


def extract_sitevisit_structured_data(
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
        "No sitevisit extraction LLM configured.",
    )
    if result is None:
        return None, error
    return normalize_lc_ratios(normalize_amounts(result)), ""
