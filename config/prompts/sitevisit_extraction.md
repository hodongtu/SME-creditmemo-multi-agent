---
name: sitevisit_extraction
description: Site-visit report -> structured JSON
---
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
  reading "777.777 triệu đồng" gives 777777, not 777777000000. The program
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

{
  "customer": {
    "ten": "tên doanh nghiệp như in trên tài liệu, hoặc ''",
    "ma_so_thue": "mã số thuế: ĐÚNG 10 chữ số, hoặc 13 với ba chữ số chi nhánh. Chép
      nguyên chữ số, bỏ dấu cách và gạch nối. Không thấy in trên tài liệu thì '' —
      KHÔNG suy ra từ mã nào khác, con số này dùng để tra cứu dữ liệu tín dụng và
      một chữ số sai sẽ kéo về hồ sơ của doanh nghiệp khác"
  },
  "survey_info": {
    "survey_date": "YYYY-MM-DD, or verbatim when unclear, null when absent",
    "officers": ["surveying officer's name"],
    "location": "where the visit took place",
    "customer_participants": ["name - role, on the customer's side"]
  },
  "business_profile": {
    "industry": "line of business",
    "gso_code": "GSO industry code when the document prints it, else null",
    "main_products": [
      {"name": "product or service", "note": "note, if any"}
    ]
  },
  "supply_chain": {
    "inputs": [
      {"supplier": "supplier", "item": "goods", "terms": "terms, if any"}
    ],
    "outputs": [
      {"customer": "customer", "item": "goods", "terms": "terms, if any"}
    ]
  },
  "business_plan_next_year": {
    "year": "the plan year, null when not stated",
    "source_unit": "dong | trieu dong | ty dong — the unit PRINTED on the page, default dong",
    "net_revenue": 0,
    "cogs": 0,
    "gross_profit": 0,
    "profit_before_tax": 0,
    "assumptions": ["the plan's stated basis or assumptions, if the report gives them"]
  },
  "lc_terms": {
    "import_ratio": <imports over total purchases, 0.0-1.0, null when not stated>,
    "lc_share_of_import": <share of imports needing an L/C, 0.0-1.0, null when not stated>,
    "sight_share": <sight L/C over total L/C turnover, 0.0-1.0, null when not stated>,
    "deferred_share": <deferred L/C share, 0.0-1.0, null when not stated>,
    "sight_days": <average days from opening to settling a sight L/C, null when not stated>,
    "deferred_days": <average days for a deferred L/C, null when not stated>
  },
  "conclusion": {
    "overall_assessment": "the surveying officer's overall assessment",
    "risks_noted": ["risks the officer recorded on site"],
    "recommendation": "the officer's recommendation",
    "conditions": ["attached conditions, if any"]
  },
  "extraction_notes": ["notes on missing sections, uncertainty, or poor OCR"]
}
