"""Internal credit relationship, read from the bank's own systems."""

import json
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

FACILITIES_SQL = """
SELECT
    facility_code,
    facility_type,
    currency,
    approved_limit,
    outstanding,
    utilisation_pct,
    start_date,
    maturity_date,
    collateral_type,
    collateral_value,
    status
FROM   v_credit_facility
WHERE  tax_code = :tax_code
ORDER  BY maturity_date DESC
"""

RELATIONSHIP_QUALITY_SQL = """
SELECT
    as_of_date,
    debt_group,
    overdue_days,
    overdue_amount,
    restructured_flag,
    times_overdue_12m,
    worst_debt_group_36m,
    relationship_start_date
FROM   v_credit_quality
WHERE  tax_code = :tax_code
"""


@tool(
    "get_internal_facilities",
    extras={"heading": "[INTERNAL CREDIT FACILITIES]"},
)
def get_internal_facilities(
    tax_code: Annotated[str, InjectedToolArg],
    executor: Annotated[Any, InjectedToolArg],
) -> str:
    """Hạn mức đã được cấp và dư nợ hiện tại của khách hàng tại CHÍNH NGÂN HÀNG NÀY."""

    rows = executor(FACILITIES_SQL, {"tax_code": tax_code})
    return json.dumps(
        {
            "facilities": [dict(row) for row in rows],
            "facility_count": len(rows),
            "total_approved_limit": sum(
                row.get("approved_limit") or 0 for row in rows
            ),
            "total_outstanding": sum(row.get("outstanding") or 0 for row in rows),
        },
        ensure_ascii=False,
    )


@tool(
    "get_internal_credit_quality",
    extras={"heading": "[INTERNAL CREDIT QUALITY]"},
)
def get_internal_credit_quality(
    tax_code: Annotated[str, InjectedToolArg],
    executor: Annotated[Any, InjectedToolArg],
) -> str:
    """Lịch sử trả nợ và nhóm nợ của khách hàng tại CHÍNH NGÂN HÀNG NÀY."""

    rows = executor(RELATIONSHIP_QUALITY_SQL, {"tax_code": tax_code})
    record: dict[str, Any] = {"quality": dict(rows[0]) if rows else {}}
    if len(rows) > 1:
        record["extraction_notes"] = [
            f"Truy vấn trả về {len(rows)} dòng chất lượng quan hệ cho một khách "
            f"hàng; chỉ dòng đầu được dùng."
        ]
    return json.dumps(record, ensure_ascii=False)
