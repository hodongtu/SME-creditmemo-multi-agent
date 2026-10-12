"""LLM extraction of the site-visit report into structured JSON."""

from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
    scale_declared_blocks,
)
from src.prompts import system_prompt


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


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Convert every amount to đồng using the block's own source_unit, in place."""

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
    """Force lc_terms shares onto a 0-1 scale, in place, and say what changed."""

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
                f"lc_terms.{field}: read as {value} — taken as a percentage "
                f"and normalised to {value / 100}."
            )
        else:
            block[field] = None
            notes.append(
                f"lc_terms.{field}: {value} is not a valid rate (outside "
                "0-100%) — dropped, the system default applies."
            )
    return result


def build_sitevisit_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the site-visit report."""

    return build_extraction_chain(system_prompt("sitevisit_extraction"), llm)


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
