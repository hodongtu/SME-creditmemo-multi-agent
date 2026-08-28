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
        return None, f"{type(exc).__name__}: {exc}"[:500]

    if not isinstance(result, dict):
        return None, f"Extraction returned non-dict result: {type(result).__name__}"
    missing = required_keys - result.keys()
    if missing:
        return None, f"Extraction result missing keys: {sorted(missing)}"
    return (normalize(result) if normalize else result), ""
