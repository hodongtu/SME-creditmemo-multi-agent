"""Internal credit relationship, read from the bank's own systems.

This is the one part of the credit-relationship report no document can supply:
sections 1.1 and 1.2 are the customer's relationship with *this* bank, and the
customer does not carry that in their folder. Section 2 — their relationships
with every other institution — comes from CIC, on paper or from the bureau feed.

Written as LangChain tools, one function per query. The pipeline calls them
before the agent runs rather than letting the model call them, so both arguments
are ``InjectedToolArg``: they never reach the model's tool schema, and no model
can decide whose credit history to pull.

``extras`` carries what the prompt assembly needs — the block heading, and which
uploaded document types make the query unnecessary.
"""

import json
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

# :tax_code is the only parameter. Named rather than positional so a driver that
# reorders nothing can still bind it, and so the guard in specialist.py can check
# the query's parameters against the ones the pipeline supplies.
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
    extras={"heading": "[HẠN MỨC TÍN DỤNG NỘI BỘ — TRUY VẤN HỆ THỐNG]"},
)
def get_internal_facilities(
    tax_code: Annotated[str, InjectedToolArg],
    executor: Annotated[Any, InjectedToolArg],
) -> str:
    """Hạn mức đã được cấp và dư nợ hiện tại của khách hàng tại CHÍNH NGÂN HÀNG NÀY.

    Dùng cho MỤC 1.1 của báo cáo quan hệ tín dụng. KHÔNG nhầm với mục 2 là quan hệ
    tại các TCTD khác — số của mục 2 nằm ở khối CIC.

    Số liệu truy vấn thẳng từ hệ thống, không qua OCR, dùng được nguyên văn. Đơn
    vị: ĐỒNG. Khối rỗng nghĩa là truy vấn không trả về dòng nào; ghi "Không có dữ
    liệu" chứ đừng suy hạn mức nội bộ từ dữ liệu CIC — đó là số của ngân hàng khác.
    """

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
    extras={"heading": "[CHẤT LƯỢNG QUAN HỆ TÍN DỤNG NỘI BỘ — TRUY VẤN HỆ THỐNG]"},
)
def get_internal_credit_quality(
    tax_code: Annotated[str, InjectedToolArg],
    executor: Annotated[Any, InjectedToolArg],
) -> str:
    """Lịch sử trả nợ và nhóm nợ của khách hàng tại CHÍNH NGÂN HÀNG NÀY.

    Dùng cho MỤC 1.2 của báo cáo quan hệ tín dụng: nhóm nợ hiện tại, số ngày và số
    dư quá hạn, số lần quá hạn 12 tháng, nhóm nợ xấu nhất 36 tháng, tình trạng cơ
    cấu lại nợ, và ngày bắt đầu quan hệ.

    Số liệu truy vấn thẳng từ hệ thống. Một khách hàng đúng ra chỉ có một dòng;
    nhiều hơn thì được nêu trong "extraction_notes" chứ không tự chọn bớt.
    """

    rows = executor(RELATIONSHIP_QUALITY_SQL, {"tax_code": tax_code})
    record: dict[str, Any] = {"quality": dict(rows[0]) if rows else {}}
    if len(rows) > 1:
        record["extraction_notes"] = [
            f"Truy vấn trả về {len(rows)} dòng chất lượng quan hệ cho một khách "
            f"hàng; chỉ dòng đầu được dùng."
        ]
    return json.dumps(record, ensure_ascii=False)
