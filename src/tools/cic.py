"""Credit-bureau data, queried instead of read off an uploaded CIC report."""

import json
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

OUTSTANDING_SQL = """
SELECT
    institution_name,
    line_item,
    amount_vnd,
    currency_amount,
    currency_code
FROM   v_bureau_outstanding
WHERE  tax_code = :tax_code
ORDER  BY amount_vnd DESC
"""

MONTHLY_DEBT_SQL = """
SELECT
    report_month,
    loan_balance,
    card_balance,
    total_balance
FROM   v_bureau_monthly_debt
WHERE  tax_code = :tax_code
ORDER  BY report_month
"""

CREDIT_RATING_SQL = """
SELECT rating_year, rating
FROM   v_bureau_rating
WHERE  tax_code = :tax_code
ORDER  BY rating_year DESC
"""


@tool(
    "get_bureau_credit_report",
    extras={
        "heading": "[BUREAU CREDIT REPORT]",
        "superseded_by": ("cic_khach_hang_vay", "cic_tai_san_bao_dam"),
    },
)
def get_bureau_credit_report(
    tax_code: Annotated[str, InjectedToolArg],
    executor: Annotated[Any, InjectedToolArg],
) -> str:
    """Quan hệ tín dụng của khách hàng tại CÁC TCTD, tra từ trung tâm thông tin tín dụng."""

    outstanding = executor(OUTSTANDING_SQL, {"tax_code": tax_code})
    monthly = executor(MONTHLY_DEBT_SQL, {"tax_code": tax_code})
    rating = executor(CREDIT_RATING_SQL, {"tax_code": tax_code})
    return json.dumps(
        {
            "bao_cao": {
                "so_hieu": "",
                "ngay_gui": "",
                "don_vi_tra_cuu": "Truy vấn hệ thống",
                "page": None,
            },
            "khach_hang": {
                "ten": "",
                "ma_cic": "",
                "ma_so_thue": tax_code,
                "nguoi_dai_dien": "",
                "dia_chi": "",
                "page": None,
            },
            "du_no_hien_tai": [
                {
                    "tctd": row.get("institution_name") or "",
                    "khoan_muc": row.get("line_item") or "",
                    "vnd": row.get("amount_vnd"),
                    "ngoai_te": row.get("currency_amount"),
                    "loai_ngoai_te": row.get("currency_code") or "",
                    "page": None,
                }
                for row in outstanding
            ],
            "du_no_12_thang": [
                {
                    "thang": row.get("report_month") or "",
                    "du_no_vay": row.get("loan_balance"),
                    "du_no_the": row.get("card_balance"),
                    "tong_du_no": row.get("total_balance"),
                    "page": None,
                }
                for row in monthly
            ],
            "xep_hang_tin_dung": [
                {"nam": str(row.get("rating_year") or ""),
                 "hang": row.get("rating") or "",
                 "page": None}
                for row in rating
            ],
            "extraction_notes": [
                "Dữ liệu truy vấn từ hệ thống, không qua OCR bản scan."
            ],
        },
        ensure_ascii=False,
    )
