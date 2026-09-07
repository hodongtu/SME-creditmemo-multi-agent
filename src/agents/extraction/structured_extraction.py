"""Shared plumbing for the LLM passes that turn a scanned document into JSON."""

from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.utils.common import normalize_text


# The two CIC forms print their unit in the column heading ("Triệu VNĐ") rather
# than in a field, so their multiplier is a constant here instead of something
# resolve_money_multiplier reads off the page.
TRIEU_VND = 10 ** 6


def resolve_money_multiplier(source_unit: Any) -> int:
    """Turn a unit a form printed into the multiplier that takes it to đồng."""

    tokens = normalize_text(str(source_unit or "")).split()
    if "ty" in tokens:
        return 10 ** 9
    if "trieu" in tokens:
        return 10 ** 6
    if "nghin" in tokens:
        return 10 ** 3
    return 1


def scale_amount(value: Any, multiplier: int) -> Any:
    """Multiply a printed figure into đồng, leaving anything else untouched."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(value * multiplier, 2)


def scale_declared_blocks(
    result: dict[str, Any],
    amount_fields: dict[str, tuple[str, ...]],
) -> dict[str, int]:
    """Scale each block's flat money fields by that block's own source_unit."""

    multipliers: dict[str, int] = {}
    for block_name, fields in amount_fields.items():
        block = result.get(block_name)
        if not isinstance(block, dict):
            continue
        multiplier = resolve_money_multiplier(block.get("source_unit"))
        multipliers[block_name] = multiplier
        for field in fields:
            block[field] = scale_amount(block.get(field), multiplier)
    return multipliers


def build_extraction_chain(system_prompt: str, llm: Any):
    """Wire a system prompt to the LLM with a JSON-parsing tail."""

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            (
                "human",
                """
                Tên file: {filename}

                Văn bản OCR:
                {content}

                Trích xuất theo đúng schema JSON đã mô tả.
                """,
            ),
        ]
    )
    return prompt | llm | JsonOutputParser()


def _explain(exc: Exception) -> str:
    """The failure as a sentence somebody can act on.

    One case earns its own branch: a max_tokens above the model's ceiling is a
    400 that stops the whole run, and the API's own wording names neither the
    environment variable that set it nor the fact that it is settable at all.
    Everything else passes through unchanged: a message nobody can act on still
    beats a wrong guess about which knob to turn.
    """

    text = str(exc)
    if "max_tokens" in text and "at most" in text:
        return (
            "max_tokens exceeds the model's ceiling — the request was refused "
            "before the model ran. Lower LLM_MAX_TOKENS (or the pass's own "
            "max_tokens_env, e.g. LLM_LEDGER_MAX_TOKENS) in .env; run "
            "testing/probe_max_tokens.py to read the model's real ceiling. "
            f"API said: {text}"
        )[:500]
    return f"{type(exc).__name__}: {text}"[:500]


def run_extraction(
    chain: Any,
    filename: str,
    content: str,
    required_keys: set[str],
    missing_llm_message: str,
    normalize: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """Invoke an extraction chain, validate its shape, and normalise the result."""

    if chain is None:
        return None, missing_llm_message
    try:
        result = chain.invoke({"filename": filename, "content": content})
    except Exception as exc:
        return None, _explain(exc)

    if not isinstance(result, dict):
        return None, f"Extraction returned non-dict result: {type(result).__name__}"
    missing = required_keys - result.keys()
    if missing:
        return None, f"Extraction result missing keys: {sorted(missing)}"
    return (normalize(result) if normalize else result), ""
