"""Select and load one industry's reference deck for a case.

30 industries is a closed, enumerable catalogue where a customer belongs to
exactly one — a classification problem, not a fuzzy-retrieval one. So this
picks by asking an LLM to choose an id from the list, the same division of
labour ``document_classification.py`` uses for its LLM classification
fallback (closed catalogue, JSON-only answer, invalid ids rejected rather
than trusted).

The decks themselves are converted once, offline, by
``scripts/ingest_industry_knowledge.py`` — this module only reads what that
script already wrote to ``src/knowledge/``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.utils.paths import PROJECT_ROOT

KNOWLEDGE_DIR = PROJECT_ROOT / "src" / "knowledge"
MANIFEST_PATH = KNOWLEDGE_DIR / "industries_manifest.yaml"
INDUSTRIES_TEXT_DIR = KNOWLEDGE_DIR / "industries"

_INDUSTRY_PLACEHOLDER = "__INDUSTRY_CATALOGUE__"

# Same brace-escaping discipline as document_classification.py's
# _CLASSIFICATION_PROMPT_TEMPLATE: substituted by str.replace, never
# str.format/f-string, because the JSON example's literal braces must survive
# being wrapped in a ChatPromptTemplate.
_SELECTION_PROMPT_TEMPLATE = """
Bạn chọn ĐÚNG MỘT ngành phù hợp nhất với hồ sơ khách hàng SME dưới đây, trong
danh mục ngành cố định sau. Mỗi dòng là "id": tên ngành — trả lời bằng đúng id.

Danh mục ngành:
__INDUSTRY_CATALOGUE__

Quy tắc:
- Đánh giá đúng NGÀNH NGHỀ THỰC SỰ khách hàng đang kinh doanh, không phải một
  từ khoá tình cờ xuất hiện trong hồ sơ.
- Nếu không có ngành nào trong danh mục thực sự phù hợp, trả "" cho
  industry_id — đừng cố ép một ngành gần đúng. Hồ sơ không khớp ngành nào thì
  đơn giản là không có khối tham khảo ngành, không sao cả.
- Không tự bịa id. Chỉ dùng đúng id có trong danh mục trên.

Trả về CHÍNH XÁC JSON, không thêm chữ nào khác:
{{
  "industry_id": "...",
  "reasoning": "lý do ngắn gọn"
}}
"""


@lru_cache(maxsize=1)
def load_industry_manifest() -> list[dict[str, Any]]:
    """The industry catalogue, or [] if ``ingest_industry_knowledge.py`` has
    never been run yet — callers treat that as "no industry reference
    available", not an error."""

    if not MANIFEST_PATH.exists():
        return []
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else []


def load_industry_reference_text(industry_id: str) -> str:
    """The cached extracted text for one industry, or "" if missing."""

    path = INDUSTRIES_TEXT_DIR / f"{industry_id}.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _describe_industries_for_prompt(manifest: list[dict[str, Any]]) -> str:
    return "\n".join(f'- "{item["id"]}": {item["display_name"]}' for item in manifest)


def build_industry_selection_prompt(manifest: list[dict[str, Any]]) -> str:
    catalogue = _describe_industries_for_prompt(manifest).replace("{", "{{").replace("}", "}}")
    return _SELECTION_PROMPT_TEMPLATE.replace(_INDUSTRY_PLACEHOLDER, catalogue)


def select_industry(
    llm: Any,
    evidence_excerpt: str,
    manifest: list[dict[str, Any]] | None = None,
) -> str | None:
    """Pick the best-matching industry id for this case, or None.

    Fails safe: a missing LLM, an empty manifest, an LLM error, or an answer
    that names an id outside the manifest all return None rather than raise —
    the caller's response is simply "no industry reference block", the same
    degrade-gracefully behaviour ``_classify_document`` uses when its LLM
    fallback errors.
    """

    manifest = load_industry_manifest() if manifest is None else manifest
    if not manifest or llm is None:
        return None

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", build_industry_selection_prompt(manifest)),
            (
                "human",
                "Bằng chứng hồ sơ khách hàng:\n{evidence_excerpt}\n\n"
                "Chọn ngành phù hợp nhất.",
            ),
        ]
    )
    chain = prompt | llm | JsonOutputParser()
    try:
        result = chain.invoke(
            {"evidence_excerpt": evidence_excerpt or "Không có bằng chứng."}
        )
    except Exception:
        return None

    industry_id = (result or {}).get("industry_id") or ""
    valid_ids = {item["id"] for item in manifest}
    return industry_id if industry_id in valid_ids else None
