"""Document discovery + rule-based classification (extracted from the notebook).

Classification answers one question: *what kind of document is this* — a matrix
row id, not an agent. Turning that into "which agents consume it" is the
matrix's job (src/agents/document_matrix.py), so routing is a declared business
rule rather than a by-product of keyword thresholds.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.agents.document_matrix import (
    describe_types_for_prompt,
    document_type_keywords,
    load_matrix,
)
from src.types import VALID_DOCUMENT_AGENTS
from src.utils.common import normalize_text
from src.utils.paths import PROJECT_ROOT


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "VALID_DOCUMENT_AGENTS",
    "build_document_classification_prompt",
    "compute_file_hash",
    "detect_loan_program",
    "discover_documents",
    "document_type_scores",
    # Re-exported from src.utils.common: it lives there so the matrix loader can
    # share it without an import cycle, but callers have always imported it from
    # this module.
    "normalize_text",
    "resolve_input_path",
    "rule_classify_document",
]

SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".txt", ".md", ".pptx"}


def compute_file_hash(path: str) -> str:
    """Compute SHA-256 from file content."""

    sha256 = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def resolve_input_path(raw_path: str) -> Path:
    """Resolve relative notebook inputs against the project root."""

    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def discover_documents(input_paths: list[str], max_files: int = 50) -> list[str]:
    """Find supported documents from files or folders."""

    files = []
    for raw_path in input_paths:
        path = resolve_input_path(raw_path)
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(str(path))
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(str(item))

    seen = set()
    deduped = []
    for file in files:
        if file not in seen:
            deduped.append(file)
            seen.add(file)

    if len(deduped) > max_files:
        dropped = len(deduped) - max_files
        print(
            f"[discover_documents] WARNING: discovered {len(deduped)} documents; "
            f"processing the first {max_files} and DROPPING {dropped}. "
            f"Increase MAX_FILES to include them."
        )
        deduped = deduped[:max_files]
    return deduped


# A hit in the filename is a much stronger signal than one in the body: a
# well-named file ("BCTC_2024.pdf", "CIC_KHCN.pdf") names the document type
# outright, whereas body text routinely quotes vocabulary from other types.
FILENAME_KEYWORD_WEIGHT = 3

# Types scoring within this ratio of the winner are treated as live candidates.
# Used only to decide whether the choice between them actually matters — see
# routing_is_unambiguous.
CANDIDATE_SCORE_RATIO = 0.5


@lru_cache(maxsize=1)
def _normalized_type_keywords() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Matrix keywords, pre-normalized, in matrix order.

    Normalizing on every document would redo the same NFD work for every file;
    the matrix is immutable within a process so it is done once. Matrix order is
    preserved because it is the deterministic tie-break for equal scores.
    """

    return tuple(
        (type_id, tuple(normalize_text(keyword) for keyword in keywords))
        for type_id, keywords in document_type_keywords().items()
    )


def document_type_scores(filename: str, content: str) -> dict[str, int]:
    """Score every document type in the matrix by weighted keyword hits."""

    name = normalize_text(filename)
    body = normalize_text(content)
    return {
        type_id: (
            FILENAME_KEYWORD_WEIGHT * sum(kw in name for kw in keywords)
            + sum(kw in body for kw in keywords)
        )
        for type_id, keywords in _normalized_type_keywords()
    }


def routing_is_unambiguous(scores: dict[str, int]) -> bool:
    """True when every plausible type for this document routes to the same agents.

    23 document types compete for the same keywords, so near-ties are common —
    but only 8 distinct agent sets exist across them. A tie between two types
    that feed identical agents (e.g. a VAT return vs a bank statement, both
    FINANCIAL+RISK) changes the label and nothing else, so there is no reason to
    spend an LLM call resolving it.
    """

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


def rule_classify_document(filename: str, content: str) -> dict[str, Any]:
    """Classify a document into a matrix document type using keyword signals."""

    scores = document_type_scores(filename, content)
    matrix_order = {type_id: index for index, type_id in enumerate(scores)}
    # Sort by score, then by matrix position, so equal scores always resolve the
    # same way regardless of dict construction order.
    best_type, best_score = min(
        scores.items(),
        key=lambda item: (-item[1], matrix_order[item[0]]),
    )
    if best_score == 0:
        return {
            "document_type": "",
            "reasoning": "No document type in the matrix matched this document.",
            "confidence": 0.0,
            "scores": scores,
            "routing_unambiguous": False,
        }

    sorted_scores = sorted(scores.values(), reverse=True)
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0
    margin = best_score - second_score
    # Confidence is driven by how dominant the top type is (margin ratio),
    # tempered by how much absolute evidence exists. Scale-relative, so the
    # filename weighting does not need a matching absolute threshold.
    margin_ratio = margin / best_score
    confidence = min(0.95, 0.40 + 0.35 * margin_ratio + 0.03 * min(best_score, 6))
    if best_score < FILENAME_KEYWORD_WEIGHT:
        # Weak absolute evidence (fewer than one filename hit's worth).
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
    }


# Substituted by plain string replacement, never str.format() or an f-string.
# The template below is consumed as a LangChain ChatPromptTemplate, where `{{`
# and `}}` mean "literal brace". str.format() would eat exactly that escaping
# and hand LangChain a bare `{`, which it then parses as the start of a template
# variable — turning the JSON example into a phantom required input.
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

Return JSON only with:
{{
  "document_type": "...",
  "reasoning": "short reason",
  "confidence": 0.0
}}
"""


def build_document_classification_prompt() -> str:
    """Render the classifier system prompt with the catalogue from the matrix.

    Generated rather than hand-written so the prompt can never drift from the
    matrix — a type added to the YAML is offered to the LLM automatically.

    The result is consumed as a ChatPromptTemplate, so every literal brace it
    contains must stay doubled: the catalogue is escaped here, and the template
    itself is substituted by str.replace rather than str.format (see
    _CATALOGUE_PLACEHOLDER).
    """

    catalogue = describe_types_for_prompt().replace("{", "{{").replace("}", "}}")
    return _CLASSIFICATION_PROMPT_TEMPLATE.replace(
        _CATALOGUE_PLACEHOLDER,
        catalogue,
    )


@lru_cache(maxsize=1)
def _alias_token_sequences() -> tuple[tuple[tuple[str, ...], str], ...]:
    """Loan program aliases as token tuples, longest first.

    Longest-first so a multi-word alias ("b1 cp") is preferred over any shorter
    alias it contains, and pre-split so detection does no string work per call.
    """

    return tuple(
        sorted(
            (
                (tuple(alias.split()), program)
                for alias, program in load_matrix().loan_program_aliases.items()
            ),
            key=lambda item: -len(item[0]),
        )
    )


def _programs_named_in(text: str) -> dict[str, str]:
    """{program id: alias that matched} for every program named in `text`.

    Matches whole token runs rather than substrings: "PL++" normalizes to the
    two-letter token "pl", which as a substring would fire inside unrelated
    words, while "P/L" normalizes to two separate tokens and must NOT match.
    """

    tokens = normalize_text(text).split()
    found: dict[str, str] = {}
    for alias_tokens, program in _alias_token_sequences():
        if program in found:
            continue
        span = len(alias_tokens)
        for start in range(len(tokens) - span + 1):
            if tuple(tokens[start:start + span]) == alias_tokens:
                found[program] = " ".join(alias_tokens)
                break
    return found


def detect_loan_program(query: str = "", history: str = "") -> dict[str, Any]:
    """Find which loan program the user is asking about.

    Deliberately deterministic string matching, not an LLM call: the vocabulary
    is a closed set of four ids declared in the matrix, so exact matching is both
    cheaper and more reliable than asking a model.

    Returns ``program=None`` whenever the answer is not unambiguous — nothing
    named, or two different programs named ("so sánh B1CP với PLO"). Callers
    then fall back to the strongest relevance across all programs, which can
    only over-prioritise a document, never demote real evidence.
    """

    for source, text in (("query", query), ("history", history)):
        found = _programs_named_in(text or "")
        if len(found) == 1:
            program, alias = next(iter(found.items()))
            return {
                "program": program,
                "matched_alias": alias,
                "candidates": [program],
                "source": source,
            }
        if len(found) > 1:
            # Ambiguity in the current query is authoritative: falling through
            # to older history would silently answer a question the user just
            # re-opened.
            return {
                "program": None,
                "matched_alias": "",
                "candidates": sorted(found),
                "source": source,
            }
    return {
        "program": None,
        "matched_alias": "",
        "candidates": [],
        "source": "",
    }
