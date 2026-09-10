"""LLM extraction of the credit application form into structured JSON."""

from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
    scale_amount,
    scale_declared_blocks,
)


REQUIRED_TOP_LEVEL_KEYS = {
    "capital_plan",
    "business_plan",
    "plan_efficiency",
    "repayment_plan",
    "collateral",
    "credit_request",
}

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
You turn a corporate customer's credit application into JSON.

Take sections B, C and D only. Skip section A (customer details, related
parties) and every appendix.

- Section B: THÔNG TIN VỀ PHƯƠNG ÁN SỬ DỤNG VỐN VAY/TÍN DỤNG VÀ HIỆU QUẢ KINH DOANH
- Section C: TÀI SẢN BẢO ĐẢM
- Section D: ĐỀ NGHỊ CẤP TÍN DỤNG

MONETARY UNITS — the most important rule:
This form mixes units inside a single section: the contract table is usually in
ĐỒNG, "Hiệu quả của phương án" in TRIỆU ĐỒNG, and the collateral table in TỶ
ĐỒNG.
- Do NOT convert. Write the number EXACTLY AS PRINTED: a line reading "240.800
  triệu đồng" gives 240800, not 240800000000. The program multiplies by
  "source_unit" itself — converting again makes the figure a million times wrong.
- Each block's "source_unit" is the unit printed on that table or section:
  "dong" | "trieu dong" | "ty dong". A section that names no unit gets "dong".

GENERAL RULES:
- Extract only what is on the page. An empty cell is null — do not infer it, and
  do not carry a figure over from another section.
- List EVERY row of the contract table and the collateral table; do not abridge.
- Keep contract/project names and asset names exactly as written.
- "page" is the page number printed in the footer ("Trang số: 4/6" -> 4), null
  when unclear.

Answer with EXACTLY this JSON schema and no other text. Its keys are English but
the values you write stay in the document's Vietnamese — they are read on into a
Vietnamese report:
{{
  "customer": {{
    "ten": "tên doanh nghiệp như in trên tài liệu, hoặc ''",
    "ma_so_thue": "mã số thuế: ĐÚNG 10 chữ số, hoặc 13 với ba chữ số chi nhánh. Chép
      nguyên chữ số, bỏ dấu cách và gạch nối. Không thấy in trên tài liệu thì '' —
      KHÔNG suy ra từ mã nào khác, con số này dùng để tra cứu dữ liệu tín dụng và
      một chữ số sai sẽ kéo về hồ sơ của doanh nghiệp khác"
  }},
  "capital_plan": {{
    "total": <number as printed, or null>,
    "own_capital": <number as printed, or null>,
    "loan_capital": <number as printed, or null>,
    "other_capital": <number as printed, or null>,
    "source_unit": "dong | trieu dong | ty dong",
    "page": <integer or null>
  }},
  "business_plan": {{
    "plan_year": "e.g. 2026, or ''",
    "narrative": "description of the plan or project, or ''",
    "sections": [
      {{"title": "e.g. Các gói thầu/công trình đã ký",
        "items": [
          {{"name": "contract or project name",
            "contract_value": <number as printed, or null>,
            "planned_value": <value planned for the coming year, as printed, or null>,
            "note": "note, or ''"}}
        ],
        "total_contract_value": <number as printed, or null>,
        "total_planned_value": <number as printed, or null>}}
    ],
    "source_unit": "dong | trieu dong | ty dong",
    "page": <integer or null>
  }},
  "plan_efficiency": {{
    "revenue": <number as printed, or null>,
    "cogs": <cost of goods sold, as printed, or null>,
    "selling_admin_expense": <selling and administrative expense, as printed, or null>,
    "interest_expense": <interest expense, as printed, or null>,
    "depreciation": <depreciation, as printed, or null>,
    "profit": <profit, as printed, or null>,
    "source_unit": "dong | trieu dong | ty dong",
    "page": <integer or null>
  }},
  "repayment_plan": {{
    "sources": "sources of repayment, or ''",
    "principal_method": "how principal is repaid, or ''",
    "interest_method": "how interest is repaid, or ''",
    "page": <integer or null>
  }},
  "collateral": {{
    "items": [
      {{"category": "e.g. Bất động sản",
        "description": "description of the asset",
        "value": <number as printed, or null>,
        "owner": "owner, or ''",
        "status": "pledge/mortgage status, or ''",
        "note": "note, or ''"}}
    ],
    "total_value": <number as printed, or null>,
    "source_unit": "dong | trieu dong | ty dong",
    "page": <integer or null>
  }},
  "credit_request": {{
    "total_limit": <total limit requested, as printed, or null>,
    "facilities": [
      {{"name": "the facility name verbatim, e.g. Hạn mức cho vay",
        "amount": <number as printed, or null>,
        "method": "lending method, or ''",
        "tenor": "tenor, or ''"}}
    ],
    "source_unit": "dong | trieu dong | ty dong",
    "page": <integer or null>
  }},
  "extraction_notes": ["notes on missing sections, uncertainty, or poor OCR"]
}}
"""


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using each block's own source_unit, in place."""

    multipliers = scale_declared_blocks(result, _AMOUNT_FIELDS)

    for section in (result.get("business_plan") or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        multiplier = multipliers.get("business_plan", 1)
        for key in ("total_contract_value", "total_planned_value"):
            section[key] = scale_amount(section.get(key), multiplier)
        for item in section.get("items") or []:
            if isinstance(item, dict):
                for key in ("contract_value", "planned_value"):
                    item[key] = scale_amount(item.get(key), multiplier)

    for item in (result.get("collateral") or {}).get("items") or []:
        if isinstance(item, dict):
            item["value"] = scale_amount(item.get("value"), multipliers.get("collateral", 1))

    for facility in (result.get("credit_request") or {}).get("facilities") or []:
        if isinstance(facility, dict):
            facility["amount"] = scale_amount(
                facility.get("amount"), multipliers.get("credit_request", 1)
            )
    return result


def build_proposal_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the credit application form."""

    return build_extraction_chain(PROPOSAL_EXTRACTION_SYSTEM_PROMPT, llm)


def extract_proposal_structured_data(
    chain: Any,
    filename: str,
    content: str,
    path: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Run the extraction chain and validate its shape. Never raises."""

    return run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No proposal extraction LLM configured.",
        normalize_amounts,
    )
