"""Smoke tests for deterministic ledger table analysis."""

from decimal import Decimal
from pathlib import Path
import unittest

from src.utils.reading.ledger_table_analysis import analyze_ledger_workbook


SAMPLE_FILE = (
    Path(__file__).resolve().parents[1]
    / "samples"
    / "case_1"
    / "ho_so_tai_chinh"
    / "VIMID_so_chi_tiet.xlsx"
)


class LedgerTableAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.analyses = {
            analysis.sheet_name: analysis
            for analysis in analyze_ledger_workbook(SAMPLE_FILE)
        }

    def test_detects_all_supported_sheets(self) -> None:
        self.assertEqual(
            set(self.analyses),
            {"TK VAY", "P.TRA KHAC", "TK_331", "TK_131", "PTHU_KHAC", "NXT"},
        )

    def test_counterparty_totals_and_rankings(self) -> None:
        sheet = self.analyses["TK VAY"]
        column = _column(sheet, "Phát sinh nợ")
        self.assertEqual(column.calculated_total, Decimal("1210969204908"))
        self.assertEqual(column.reported_total, Decimal("1210969204908"))
        self.assertEqual(column.delta, Decimal("0"))
        self.assertEqual(column.top_rows[0].excel_row, 23)
        self.assertEqual(column.top_rows[0].labels["Mã khách hàng"], "NH30")

    def test_inventory_merged_headers(self) -> None:
        sheet = self.analyses["NXT"]
        column = _column(sheet, "Dư cuối / Giá trị")
        self.assertEqual(sheet.header_rows, [6, 7])
        self.assertEqual(column.calculated_total, Decimal("1788182207060"))
        self.assertEqual(column.reported_total, Decimal("1788182207060"))
        self.assertEqual(column.top_rows[0].labels["Loại xe"], "Đầu kéo")

    def test_missing_reported_total_is_explicit(self) -> None:
        sheet = self.analyses["PTHU_KHAC"]
        column = _column(sheet, "Dư có cuối kỳ")
        self.assertEqual(column.calculated_total, Decimal("96735674467"))
        self.assertIsNone(column.reported_total)
        self.assertIsNone(column.delta)


def _column(sheet, column_name):
    for column in sheet.columns:
        if column.column_name == column_name:
            return column
    raise AssertionError(f"Column not found: {column_name}")


if __name__ == "__main__":
    unittest.main()
