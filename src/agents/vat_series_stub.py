"""PLACEHOLDER monthly VAT revenue series — invented numbers, not real data.

The credit-relationship chart plots outstanding debt against VAT-declared
revenue. Debt comes from section 2.6 of the CIC S10A report; revenue would come
from the monthly VAT filings (``to_khai_thue_gtgt``), which nothing extracts
yet. This module stands in for that extraction so the chart can be built and
reviewed end to end.

Two things are deliberate and must survive any edit to this file:

* ``IS_STUB`` is exported and the renderer prints a caption on the chart itself.
  An unlabelled invented series sitting in a credit memo is precisely what the
  guardrails in this repo exist to prevent — the reader has no way to tell it
  from a figure traced to a document.
* The series is keyed by month, so replacing it later means deleting this file
  and passing the real extraction into the same argument. Nothing else changes.

Replace with: a ``to_khai_thue_gtgt`` extraction pass producing
``[{"thang": "MM/YYYY", "doanh_thu": <đồng>}]``.
"""

from __future__ import annotations

IS_STUB = True

STUB_NOTICE = "Số liệu doanh thu VAT là GIẢ LẬP để minh hoạ, chưa trích từ hồ sơ."

# Window matches section 2.6 of the sample S10A report (12 months, oldest first).
# Amounts are in đồng, same unit the debt series is normalised to, and in the
# same order of magnitude so both lines stay legible on one axis.
VAT_SERIES_STUB: list[dict[str, object]] = [
    {"thang": "04/2025", "doanh_thu": 31_400_000_000},
    {"thang": "05/2025", "doanh_thu": 28_750_000_000},
    {"thang": "06/2025", "doanh_thu": 35_120_000_000},
    {"thang": "07/2025", "doanh_thu": 42_600_000_000},
    {"thang": "08/2025", "doanh_thu": 39_880_000_000},
    {"thang": "09/2025", "doanh_thu": 33_240_000_000},
    {"thang": "10/2025", "doanh_thu": 45_910_000_000},
    {"thang": "11/2025", "doanh_thu": 51_300_000_000},
    {"thang": "12/2025", "doanh_thu": 58_470_000_000},
    {"thang": "01/2026", "doanh_thu": 24_150_000_000},
    {"thang": "02/2026", "doanh_thu": 21_900_000_000},
    {"thang": "03/2026", "doanh_thu": 37_640_000_000},
]


def vat_series_for_months(months: list[str]) -> list[float | None]:
    """Line up the stub against a month axis, ``None`` where it has no figure.

    The axis is driven by the debt series, which comes from a real document and
    may cover a different window than this stub. Returning ``None`` for months
    the stub does not cover keeps the two lines on one shared axis instead of
    silently stretching the stub to fit.
    """

    by_month = {str(row["thang"]): float(row["doanh_thu"]) for row in VAT_SERIES_STUB}
    return [by_month.get(month) for month in months]
