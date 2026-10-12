"""Deciding what each document is: keyword scoring, and the LLM prompt for ties."""

from functools import lru_cache
from typing import Any

from src.agents.documents.document_matrix import (
    describe_types_for_prompt,
    document_type_filename_keywords,
    document_type_keywords,
    load_matrix,
)
from src.utils.common import normalize_text
from src.prompts import system_prompt

FILENAME_KEYWORD_WEIGHT = 3
CANDIDATE_SCORE_RATIO = 0.5
# One filename hit is the weakest signal this project acts on anywhere: it is
# the floor _cross_box_owner uses before it will even warn, and below it the
# confidence formula caps itself at 0.6.
MIN_ROUTING_EVIDENCE = FILENAME_KEYWORD_WEIGHT
MIN_PLAUSIBLE_TYPES = 2


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


def _tokens(text: str) -> tuple[str, ...]:
    """The normalised text as whole words."""

    return tuple(normalize_text(text).split())


@lru_cache(maxsize=1)
def _normalized_filename_keywords() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Filename-only keywords, normalised once and split into tokens."""

    return {
        type_id: tuple(_tokens(keyword) for keyword in keywords)
        for type_id, keywords in document_type_filename_keywords().items()
        if keywords
    }


def _has_token_run(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """True when `needle`'s words appear consecutively in `haystack`.

    Whole words, unlike the substring test the body keywords use. That test is
    right for prose, where "no phai tra" has to be found inside a sentence, and
    wrong for a bare number: normalize_text turns 131.2025.xls into
    "131 2025 xls", but it turns BCTC_20131231.xls into "bctc 20131231", and
    "131" is a substring of that date.
    """

    if not needle:
        return False
    width = len(needle)
    return any(
        haystack[start:start + width] == needle
        for start in range(len(haystack) - width + 1)
    )


def document_type_scores(
    filename: str,
    content: str,
    group_id: str = "",
) -> dict[str, int]:
    """Weighted keyword hits per document type.

    `keywords` are scored against both the filename and the body, the filename
    at FILENAME_KEYWORD_WEIGHT. `filename_keywords` are scored against the
    filename ALONE, at the same weight, and are matched by whole word. That is
    for terms which identify a document when someone puts them in a filename
    and mean nothing inside one: an account number is the case this was added
    for — 131.2025.xls is a detail ledger of account 131, while "131" appearing
    in a body is a line item, an amount, or part of a date.
    """

    name = normalize_text(filename)
    name_tokens = tuple(name.split())
    body = normalize_text(content)
    allowed = _types_in_group(group_id)
    filename_only = _normalized_filename_keywords()

    return {
        type_id: (
            FILENAME_KEYWORD_WEIGHT
            * (
                sum(kw in name for kw in keywords)
                + sum(
                    _has_token_run(name_tokens, kw)
                    for kw in filename_only.get(type_id, ())
                )
            )
            + sum(kw in body for kw in keywords)
        )
        for type_id, keywords in _normalized_type_keywords()
        if allowed is None or type_id in allowed
    }


def routing_is_unambiguous(scores: dict[str, int]) -> bool:
    """True when SEVERAL plausible types all route to the same agents.

    The flag exists to skip an LLM call that could not change the outcome: when
    every type still in the running feeds the same specialists, the label may be
    wrong but the routing is not. Because it bypasses the confidence floor, it
    must only answer that question, never a vacuous version of it. Two shapes
    made it vacuous and both used to return True:

    A single weak hit with nothing else scoring. A keyword lands by accident
    more often than it looks: separators are flattened before matching, so a
    "Nợ" column standing next to a "Phải trả…" label reads as "no phai tra" and
    scores a balance sheet. One accidental point then vouched for itself, and a
    ledger named 131.2025.xls came out as bang_can_doi_ke_toan at 0.600 — the
    value the formula produces when it caps itself for distrusting the signal.

    And a single candidate: "they all agree" said of one type is not agreement.
    """

    top_score = max(scores.values(), default=0)
    if top_score < MIN_ROUTING_EVIDENCE:
        return False
    threshold = max(1, CANDIDATE_SCORE_RATIO * top_score)
    plausible = [
        type_id for type_id, score in scores.items() if score >= threshold
    ]
    if len(plausible) < MIN_PLAUSIBLE_TYPES:
        return False
    matrix = load_matrix()
    signatures = {
        matrix.types[type_id].routing_signature for type_id in plausible
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
    longest = max((len(kw) for kw in keywords if kw in normalized_name), default=0)
    tokens = tuple(normalized_name.split())
    for needle in _normalized_filename_keywords().get(type_id, ()):
        if _has_token_run(tokens, needle):
            longest = max(longest, len(" ".join(needle)))
    return longest


def _no_type_matched(
    scores: dict[str, int],
    group_id: str,
    reasoning: str,
) -> dict[str, Any]:
    """The verdict that leaves a document unclassified, and therefore shared."""

    return {
        "document_type": "",
        "reasoning": reasoning,
        "confidence": 0.0,
        "scores": scores,
        "routing_unambiguous": False,
        "filename_decisive": False,
        "group_id": group_id,
    }


def rule_classify_document(
    filename: str,
    content: str,
    group_id: str = "",
) -> dict[str, Any]:
    """Classify by keyword signal, with three flags saying whether an LLM tie-break
    is still needed: confidence, routing_unambiguous, filename_decisive."""

    scores = document_type_scores(filename, content, group_id)
    if not scores:
        # A catch-all upload box: there is nothing to match against, and that is
        # the declared intent, not a failure to recognise the document.
        return _no_type_matched(
            scores,
            group_id,
            f"Upload box {group_id!r} classifies nothing; the document is "
            f"general context.",
        )

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
        return _no_type_matched(
            scores,
            group_id,
            "No document type in the matrix matched this document.",
        )

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


def build_document_classification_prompt(group_id: str = "") -> str:
    """Render the classifier system prompt with the catalogue from the matrix."""

    return system_prompt(
        "document_classification",
        document_type_catalogue=describe_types_for_prompt(group_id),
    )
