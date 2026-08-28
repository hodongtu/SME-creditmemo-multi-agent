"""Deciding what each document is: keyword scoring, and the LLM prompt for ties."""

from functools import lru_cache
from typing import Any

from src.matrix.document_matrix import (
    describe_types_for_prompt,
    document_type_keywords,
    load_matrix,
)
from src.utils.common import normalize_text

FILENAME_KEYWORD_WEIGHT = 3
CANDIDATE_SCORE_RATIO = 0.5


@lru_cache(maxsize=8)
def _types_in_group(group_id: str) -> frozenset[str] | None:
    """Type ids inside one upload box, or None when no box was declared."""
    if group_id:
        return frozenset(
            doc.id for doc in load_matrix().types.values() if doc.group_id == group_id
        )


@lru_cache(maxsize=1)
def _normalized_type_keywords() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Matrix keywords, normalised once, in matrix order — the order is the
    deterministic last-resort tie-break for equal scores."""

    return tuple(
        (type_id, tuple(normalize_text(keyword) for keyword in keywords))
        for type_id, keywords in document_type_keywords().items()
    )


def document_type_scores(
    filename: str,
    content: str,
    group_id: str = "",
) -> dict[str, int]:
    """Weighted keyword hits per document type."""

    name = normalize_text(filename)
    body = normalize_text(content)
    allowed = _types_in_group(group_id)

    return {
        type_id: (
            FILENAME_KEYWORD_WEIGHT * sum(kw in name for kw in keywords)
            + sum(kw in body for kw in keywords)
        )
        for type_id, keywords in _normalized_type_keywords()
        if allowed is None or type_id in allowed
    }


def routing_is_unambiguous(scores: dict[str, int]) -> bool:
    """True when every plausible type routes to the same agents."""

    top_score = max(scores.values(), default=0)
    if top_score <= 0:
        return False
    threshold = max(1, CANDIDATE_SCORE_RATIO * top_score)
    matrix = load_matrix()
    signatures = {
        matrix.types[type_id].routing_signature
        for type_id, score in scores.items()
        if score >= threshold
    }
    return len(signatures) == 1


def filename_keyword_owner(filename: str, group_id: str = "") -> str | None:
    """The one type whose keywords hit the filename, or None if none or several
    do. Sole ownership is the point: two types matching the name is a real
    ambiguity only the document body can settle. Scored with an empty body."""

    owners = [
        type_id
        for type_id, score in document_type_scores(filename, "", group_id).items()
        if score
    ]
    return owners[0] if len(owners) == 1 else None


def _longest_filename_hit(type_id: str, normalized_name: str) -> int:
    """Length of this type's longest keyword found in the filename."""

    keywords = dict(_normalized_type_keywords()).get(type_id, ())
    return max((len(kw) for kw in keywords if kw in normalized_name), default=0)


def rule_classify_document(
    filename: str,
    content: str,
    group_id: str = "",
) -> dict[str, Any]:
    """Classify by keyword signal, with three flags saying whether an LLM tie-break
    is still needed: confidence, routing_unambiguous, filename_decisive."""

    scores = document_type_scores(filename, content, group_id)
    matrix_order = {type_id: index for index, type_id in enumerate(scores)}
    normalized_name = normalize_text(filename)

    # Score first, then how much of the filename the winning keyword covers, and
    # only then matrix position.
    best_type, best_score = min(
        scores.items(),
        key=lambda item: (
            -item[1],
            -_longest_filename_hit(item[0], normalized_name),
            matrix_order[item[0]],
        ),
    )
    if best_score == 0:
        return {
            "document_type": "",
            "reasoning": "No document type in the matrix matched this document.",
            "confidence": 0.0,
            "scores": scores,
            "routing_unambiguous": False,
            "filename_decisive": False,
            "group_id": group_id,
        }

    sorted_scores = sorted(scores.values(), reverse=True)
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0
    margin = best_score - second_score
    margin_ratio = margin / best_score
    confidence = min(0.95, 0.40 + 0.35 * margin_ratio + 0.03 * min(best_score, 6))
    if best_score < FILENAME_KEYWORD_WEIGHT:
        confidence = min(confidence, 0.6)

    return {
        "document_type": best_type,
        "reasoning": (
            "Rule-based classification from keyword signals "
            f"(score={best_score}, margin={margin}, ratio={margin_ratio:.2f})."
        ),
        "confidence": confidence,
        "scores": scores,
        "routing_unambiguous": routing_is_unambiguous(scores),
        "filename_decisive": best_type == filename_keyword_owner(filename, group_id),
        "group_id": group_id,
    }


_CATALOGUE_PLACEHOLDER = "__DOCUMENT_TYPE_CATALOGUE__"

_CLASSIFICATION_PROMPT_TEMPLATE = """
You classify extracted text from SME underwriting documents.

Pick the ONE document type from the catalogue below that best describes this
document. Answer with its exact id string (the value in quotes).

Document type catalogue:
__DOCUMENT_TYPE_CATALOGUE__

Rules:
- Judge what the document *is*, not what it mentions. A business plan that
  quotes revenue figures is still a loan application, not a financial statement.
- Use the group headings as context: they say what kind of file each type is.
- If the document genuinely matches none of the types, return "" for
  document_type. Do not force a bad match — an unmatched document is shared
  with every agent, whereas a wrong type sends it to the wrong ones.
- Do not invent ids. Only ids listed above are valid.

"description" names this document for a reader of the finished credit memo, who
sees the filename and nothing else:
- Vietnamese, ONE line, AT MOST 20 WORDS. A longer one is dropped and replaced
  by the document type's own name, so the words have to earn their place.
- Say what the document IS, then whichever of these the text actually shows:
  number, counterparty, signing date, period covered.
- Never guess. A detail that is not on the page is left out, not inferred from
  the filename or the type.

- Good: "Hợp đồng đại lý số 01/2023/HĐĐL/TDA-CKQN với Tôn Đông Á, ký 03/01/2023"
- Good: "Bảng tổng hợp phải thu khách hàng TK 131, kỳ 01/01/2024-31/12/2024"
- Too long: "Hợp đồng đại lý phân phối sản phẩm tôn mạ được ký kết giữa Công ty
  Cổ phần Tôn Đông Á và Công ty TNHH Cơ khí Quy Nhơn vào ngày 03/01/2023 với
  thời hạn hiệu lực một năm kể từ ngày ký"

Return JSON only with:
{{
  "document_type": "...",
  "reasoning": "short reason",
  "description": "...",
  "confidence": 0.0
}}
"""


def build_document_classification_prompt(group_id: str = "") -> str:
    """Render the classifier system prompt with the catalogue from the matrix."""

    catalogue = describe_types_for_prompt(group_id).replace("{", "{{").replace("}", "}}")
    return _CLASSIFICATION_PROMPT_TEMPLATE.replace(
        _CATALOGUE_PLACEHOLDER,
        catalogue,
    )
