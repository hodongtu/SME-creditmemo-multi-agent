"""LLM extraction of the site-visit report into structured JSON."""

from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
    scale_declared_blocks,
)

REQUIRED_TOP_LEVEL_KEYS = {
    "survey_info",
    "business_profile",
    "supply_chain",
    "business_plan_next_year",
    "lc_terms",
    "conclusion",
}


_AMOUNT_FIELDS: dict[str, tuple[str, ...]] = {
    "business_plan_next_year": (
        "net_revenue",
        "cogs",
        "gross_profit",
        "profit_before_tax",
    ),
}

SITEVISIT_EXTRACTION_SYSTEM_PROMPT = """
You turn a corporate customer's site visit report into JSON.

This is free prose, not a form with fixed boxes. The facts are scattered through
the paragraphs — look for them BY CONTENT, not by section number.

MANDATORY RULES:
- Record only what the document actually says. Do not guess, do not fill in a
  "reasonable" value. Not found means null (or [] for a list).
- Keep OBSERVATION apart from OPINION. Every assessment, judgement or
  recommendation made by the surveying officer belongs in "conclusion" and
  nowhere else; the other four blocks carry only what was read off the page.
- Do NOT convert monetary units. Write the number EXACTLY AS PRINTED: a report
  reading "240.800 triệu đồng" gives 240800, not 240800000000. The program
  multiplies by "source_unit" itself — converting again makes the figure a
  million times wrong.
- "source_unit" is the unit printed on the page: "dong" | "trieu dong" |
  "ty dong". A page that names no unit gets "dong".
- The GSO code (the General Statistics Office industry code) is recorded only
  when the document prints it. Do not look it up, do not infer it from the
  industry name.
- Fill "lc_terms" only where the report EXPLICITLY discusses importing and
  settling by L/C. Write every ratio as a decimal (60% -> 0.6). Do not infer a
  ratio from a list of foreign suppliers — where the report is silent leave
  null, and the program will use its default and say so.

Answer with exactly this JSON structure and no explanation. Its keys are English
but the values you write stay in the document's Vietnamese — they are read on
into a Vietnamese report:

{{
  "customer": {{
    "ten": "tên doanh nghiệp như in trên tài liệu, hoặc ''",
    "ma_so_thue": "mã số thuế: ĐÚNG 10 chữ số, hoặc 13 với ba chữ số chi nhánh. Chép
      nguyên chữ số, bỏ dấu cách và gạch nối. Không thấy in trên tài liệu thì '' —
      KHÔNG suy ra từ mã nào khác, con số này dùng để tra cứu dữ liệu tín dụng và
      một chữ số sai sẽ kéo về hồ sơ của doanh nghiệp khác"
  }},
  "survey_info": {{
    "survey_date": "YYYY-MM-DD, or verbatim when unclear, null when absent",
    "officers": ["surveying officer's name"],
    "location": "where the visit took place",
    "customer_participants": ["name - role, on the customer's side"]
  }},
  "business_profile": {{
    "industry": "line of business",
    "gso_code": "GSO industry code when the document prints it, else null",
    "main_products": [
      {{"name": "product or service", "note": "note, if any"}}
    ]
  }},
  "supply_chain": {{
    "inputs": [
      {{"supplier": "supplier", "item": "goods", "terms": "terms, if any"}}
    ],
    "outputs": [
      {{"customer": "customer", "item": "goods", "terms": "terms, if any"}}
    ]
  }},
  "business_plan_next_year": {{
    "year": "the plan year, null when not stated",
    "source_unit": "dong | trieu dong | ty dong — the unit PRINTED on the page, default dong",
    "net_revenue": 0,
    "cogs": 0,
    "gross_profit": 0,
    "profit_before_tax": 0,
    "assumptions": ["the plan's stated basis or assumptions, if the report gives them"]
  }},
  "lc_terms": {{
    "import_ratio": <imports over total purchases, 0.0-1.0, null when not stated>,
    "lc_share_of_import": <share of imports needing an L/C, 0.0-1.0, null when not stated>,
    "sight_share": <sight L/C over total L/C turnover, 0.0-1.0, null when not stated>,
    "deferred_share": <deferred L/C share, 0.0-1.0, null when not stated>,
    "sight_days": <average days from opening to settling a sight L/C, null when not stated>,
    "deferred_days": <average days for a deferred L/C, null when not stated>
  }},
  "conclusion": {{
    "overall_assessment": "the surveying officer's overall assessment",
    "risks_noted": ["risks the officer recorded on site"],
    "recommendation": "the officer's recommendation",
    "conditions": ["attached conditions, if any"]
  }},
  "extraction_notes": ["notes on missing sections, uncertainty, or poor OCR"]
}}
"""


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using the block's own source_unit, in place.

    This is the only place the conversion happens — see the module docstring for
    what it cost to have it in two. Runs even when the multiplier is 1, because
    the rounding is wanted whether or not there was a unit to apply.
    """

    scale_declared_blocks(result, _AMOUNT_FIELDS)
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
    path: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Run the extraction chain and validate its shape. Never raises."""

    return run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No sitevisit extraction LLM configured.",
        lambda result: normalize_lc_ratios(normalize_amounts(result)),
    )
