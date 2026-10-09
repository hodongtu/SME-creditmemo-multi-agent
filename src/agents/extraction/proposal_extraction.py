"""LLM extraction of the credit application form into structured JSON."""

from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
    scale_amount,
    scale_declared_blocks,
)
from src.prompts import system_prompt


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

    return build_extraction_chain(system_prompt("proposal_extraction"), llm)


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
