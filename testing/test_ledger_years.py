"""Ledger years the report does not need are left out before the call."""

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.agents.extraction import ledger_extraction as ledger
from src.agents.extraction.ledger_extraction import (
    keep_recent_sheets,
    reporting_cutoff,
    sheet_years,
)
from src.agents.extraction.structured_extraction import build_extraction_chain
from src.utils.reading.extractors import read_sheets

SAMPLE_WORKBOOK = (
    Path(__file__).resolve().parents[1]
    / "samples" / "case_1" / "ho_so_tai_chinh" / "VIMID_so_chi_tiet.xlsx"
)


def _sheet(name: str, header: str) -> dict[str, str]:
    return {"filename": "so.xlsx", "sheet_name": name,
            "content": f"A\tB\n{header}\nKH01\t1.000.000"}


class SheetYearsTest(unittest.TestCase):
    def test_every_way_a_period_is_written(self) -> None:
        cases = {
            "Từ ngày 01/01/2024 đến ngày 31/12/2024": {2024},
            "Từ 01-01-2024 đến 31-12-2024": {2024},
            "SỔ CHI TIẾT TÀI KHOẢN - Năm 2024": {2024},
            "So chi tiet tai khoan nam 2024": {2024},
            "Quý 4/2024": {2024},
            "Tháng 12 năm 2024": {2024},
            "Niên độ kế toán 2024": {2024},
            "Tại ngày 31/12/2024": {2024},
            "Từ ngày 01/12/2023 đến ngày 31/12/2024": {2023, 2024},
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(sheet_years(line), want)

    def test_what_only_looks_like_a_year(self) -> None:
        cases = {
            # A figure: normalises to "2 024 000 000", never the token 2024.
            "Dư nợ đầu kỳ 2.024.000.000": set(),
            # "nam" of Việt Nam, with no year after it.
            "Số 2024 đường Tố Hữu, Việt Nam": set(),
            "SỔ CHI TIẾT CÔNG NỢ PHẢI THU": set(),
            # An invoice number beside a date: the date is the period.
            "Hóa đơn số 2024 ngày 05/01/2025": {2025},
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(sheet_years(line), want)

    def test_a_declared_period_outranks_the_transaction_dates(self) -> None:
        text = ("Từ ngày 01/01/2025 đến ngày 31/12/2025\n"
                "31/12/2024\tSố dư chuyển sang\t1.000\n05/01/2025\tThu tiền\t2.000")
        self.assertEqual(sheet_years(text), {2025})


class CutoffTest(unittest.TestCase):
    FIVE = [{2021}, {2022}, {2023}, {2024}, {2025}]

    def test_without_statements_the_two_newest_years(self) -> None:
        self.assertEqual(reporting_cutoff(self.FIVE, set()), 2024)

    def test_statements_reach_further_back_never_forward(self) -> None:
        # The newest ledger is kept even though the statements stop a year
        # short of it: that is the most current data in the dossier.
        self.assertEqual(reporting_cutoff(self.FIVE, {2023, 2024}), 2023)

    def test_nothing_known_means_no_filter(self) -> None:
        self.assertIsNone(reporting_cutoff([set(), set()], set()))


class KeepRecentSheetsTest(unittest.TestCase):
    def _five_years(self) -> list[dict[str, str]]:
        return [_sheet(str(year), f"Năm {year}") for year in range(2021, 2026)]

    def _names(self, sheets) -> list[str]:
        return [s["sheet_name"] for s in sheets]

    def test_five_uploaded_two_sent(self) -> None:
        kept, notes = keep_recent_sheets(self._five_years(), set())
        self.assertEqual(self._names(kept), ["2024", "2025"])
        self.assertEqual(len(notes), 1)
        for year in ("2021", "2022", "2023"):
            self.assertIn(f"so.xlsx › {year}", notes[0])

    def test_a_sheet_spanning_the_boundary_is_kept(self) -> None:
        sheets = [_sheet("cầu", "Từ ngày 01/12/2023 đến ngày 31/12/2024"),
                  _sheet("2025", "Năm 2025")]
        kept, _ = keep_recent_sheets(sheets, set())
        self.assertEqual(self._names(kept), ["cầu", "2025"])

    def test_a_sheet_with_no_year_is_kept_and_named(self) -> None:
        sheets = [*self._five_years(), _sheet("không rõ", "SỔ CHI TIẾT CÔNG NỢ")]
        kept, notes = keep_recent_sheets(sheets, set())
        self.assertIn("không rõ", self._names(kept))
        self.assertTrue(any("không rõ" in n and "could be read" in n for n in notes))

    def test_a_filter_that_would_leave_nothing_sends_everything(self) -> None:
        sheets = [_sheet("2021", "Năm 2021"), _sheet("2022", "Năm 2022")]
        kept, notes = keep_recent_sheets(sheets, {2024, 2025})
        self.assertEqual(len(kept), 2)
        self.assertIn("sent anyway", notes[0])


class BannerSurvivesTest(unittest.TestCase):
    def test_the_period_line_is_not_trimmed_away(self) -> None:
        """TK VAY's title and period sat in the row-counter's column A.

        Dropping that column took all four banner lines with it, the model
        never saw the period, and the account came back as 341@unknown.
        """

        sheet = next(s for s in read_sheets(str(SAMPLE_WORKBOOK))
                     if s.sheet_name == "TK VAY")
        trimmed = ledger._without_row_counter(sheet.content)
        self.assertIn("Từ ngày 01/01/2025 đến ngày 31/12/2025", trimmed)
        self.assertIn("BÁO CÁO CÂN ĐỐI PHÁT SINH CÔNG NỢ", trimmed)
        self.assertNotIn("\nStt\t", trimmed, "the counter's header must still go")
        self.assertEqual(sheet_years(trimmed), {2025})


class EndToEndTest(unittest.TestCase):
    def test_only_the_recent_years_reach_the_model(self) -> None:
        sent: list[str] = []

        def model(prompt_value):
            sent.append(prompt_value.to_messages()[-1].content)
            return AIMessage(
                content=json.dumps({"accounts": {"131@2024": {}, "131@2025": {}}}),
                response_metadata={"finish_reason": "stop"})

        with tempfile.TemporaryDirectory() as folder:
            documents = []
            for year in range(2021, 2026):
                path = Path(folder) / f"so_131_{year}.csv"
                path.write_text(
                    f"SỔ CHI TIẾT TÀI KHOẢN 131 - Năm {year}\n"
                    f"Đối tác,Số tiền\nKH01,{year}000\n", encoding="utf-8")
                documents.append((path.name, "(text)", str(path)))
            chain = build_extraction_chain("SYSTEM", RunnableLambda(model))
            record, error = ledger.extract_ledger_batch(chain, documents)[0]

        self.assertEqual(error, "")
        self.assertEqual(len(sent), 1, "still exactly one call")
        for year in (2024, 2025):
            self.assertIn(f"so_131_{year}.csv", sent[0])
        for year in (2021, 2022, 2023):
            self.assertNotIn(f"so_131_{year}.csv", sent[0])
        notes = " ".join(record["extraction_notes"])
        self.assertIn("so_131_2021.csv", notes)
        # The sheets the filter left out must not be counted as sheets the
        # model skipped. (Other warnings may fire: this fake model returns
        # empty accounts, which the slice check rightly objects to.)
        self.assertNotIn("sheets were sent but only", notes)


if __name__ == "__main__":
    unittest.main()
