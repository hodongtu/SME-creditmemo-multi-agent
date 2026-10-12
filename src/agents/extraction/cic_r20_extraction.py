"""LLM extraction of the CIC R20 collateral report into structured JSON"""

from typing import Any

from src.agents.extraction.structured_extraction import (
    TRIEU_VND,
    build_extraction_chain,
    run_extraction,
    scale_amount,
)
from src.prompts import system_prompt

REQUIRED_TOP_LEVEL_KEYS = {
    "bao_cao",
    "khach_hang",
    "danh_sach_tctd",
    "tai_san_bao_dam",
}


def build_cic_r20_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the CIC R20 report."""

    return build_extraction_chain(system_prompt("cic_r20_extraction"), llm)


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Scale every collateral value from triệu đồng to đồng, in place."""

    for row in result.get("tai_san_bao_dam") or []:
        if isinstance(row, dict):
            row["gia_tri_trieu_vnd"] = scale_amount(
                row.get("gia_tri_trieu_vnd"), TRIEU_VND
            )
    return result


def extract_cic_r20_structured_data(
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
        "No CIC R20 extraction LLM configured.",
        normalize_amounts,
    )
