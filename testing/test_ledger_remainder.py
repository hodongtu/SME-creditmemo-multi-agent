"""Every top-N list closes with one row for everything it leaves out."""

import copy
import json
import unittest
from pathlib import Path

from src.agents.extraction import ledger_extraction as ledger
from src.prompts import system_prompt

ROOT = Path(__file__).resolve().parents[1]
REAL_RECORD = ROOT / "logs" / "case_1_20261011_224847" / "ledger_extraction.json"
DEBT_COLUMNS = ["counterparty_code", "counterparty_name",
                "debit_movement", "closing_debit", "booking_unit"]


def _account(rows, totals, item_count, columns=DEBT_COLUMNS):
    sorted_by = next(c for c in columns if c in totals)
    return {"accounts": {"131@2025": {
        "item_columns": columns, "item_count": item_count, "totals": totals,
        "rankings": [{"sorted_by": sorted_by, "items": rows}]}}}


def _five():
    return [[f"KH{i}", f"Khách {i}", 100 * (6 - i), 10 * (6 - i), "VIMID"]
            for i in range(1, 6)]


def _items(record):
    return record["accounts"]["131@2025"]["rankings"][0]["items"]


class RemainderRowTest(unittest.TestCase):
    def test_the_rest_is_totals_minus_the_listed_rows(self) -> None:
        record = _account(_five(), {"debit_movement": 2000, "closing_debit": 400}, 143)
        ledger.add_remainder_rows(record)
        rows = _items(record)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1], ["", "Các đối tác còn lại (138)", 500, 250, None])

    def test_the_listed_rows_and_the_rest_add_up_to_totals(self) -> None:
        totals = {"debit_movement": 2000, "closing_debit": 400}
        record = _account(_five(), totals, 143)
        ledger.add_remainder_rows(record)
        for column, total in totals.items():
            at = DEBT_COLUMNS.index(column)
            self.assertEqual(sum(r[at] for r in _items(record)), total)

    def test_a_sheet_no_longer_than_the_list_gets_no_row(self) -> None:
        record = _account(_five(), {"debit_movement": 1500}, 5)
        ledger.add_remainder_rows(record)
        self.assertEqual(len(_items(record)), 5)

    def test_a_missing_total_is_unknown_not_zero(self) -> None:
        record = _account(_five(), {"debit_movement": 2000}, 143)
        ledger.add_remainder_rows(record)
        self.assertIsNone(_items(record)[-1][DEBT_COLUMNS.index("closing_debit")])

    def test_rows_that_exceed_the_total_give_no_remainder(self) -> None:
        # The slice check names this inconsistency; a negative "rest" would hide it.
        record = _account(_five(), {"debit_movement": 1000, "closing_debit": 400}, 143)
        ledger.add_remainder_rows(record)
        self.assertIsNone(_items(record)[-1][DEBT_COLUMNS.index("debit_movement")])

    def test_a_stock_ledger_names_items_not_counterparties(self) -> None:
        columns = ["item_name", "outflow_value"]
        record = _account([[f"SP{i}", 10] for i in range(5)],
                          {"outflow_value": 80}, 9, columns)
        ledger.add_remainder_rows(record)
        self.assertEqual(_items(record)[-1], ["Các mặt hàng còn lại (4)", 30])

    def test_running_twice_adds_one_row(self) -> None:
        record = _account(_five(), {"debit_movement": 2000}, 143)
        ledger.add_remainder_rows(record)
        ledger.add_remainder_rows(record)
        self.assertEqual(len(_items(record)), 6)

    def test_the_real_record_adds_up_on_every_list(self) -> None:
        record = copy.deepcopy(json.loads(REAL_RECORD.read_text()))
        ledger.add_remainder_rows(record)
        checked = 0
        for account in record["accounts"].values():
            columns = account["item_columns"]
            for ranking in account["rankings"]:
                rows = ranking["items"]
                if ledger._residual_count(rows[-1], columns) is None:
                    continue
                for column, total in account["totals"].items():
                    at = columns.index(column)
                    self.assertEqual(sum(r[at] for r in rows), total,
                                     f"{column} of a {ranking['sorted_by']} list")
                checked += 1
        self.assertGreater(checked, 0, "the sample must exercise a remainder")


class TrimKeepsTheRestTest(unittest.TestCase):
    def test_a_trim_folds_into_the_remainder_with_the_right_count(self) -> None:
        record = _account(_five(), {"debit_movement": 2000, "closing_debit": 400}, 143)
        ledger.add_remainder_rows(record)
        trimmed = ledger.fit_to_budget(record, budget=1)
        rows = _items(trimmed)
        tail = rows[-1]
        self.assertEqual(ledger._residual_count(tail, DEBT_COLUMNS),
                         143 - (len(rows) - 1),
                         "the merged row must count every row it stands for")
        at = DEBT_COLUMNS.index("debit_movement")
        self.assertEqual(sum(r[at] for r in rows), 2000)

    def test_the_remainder_never_wins_a_place_in_the_list(self) -> None:
        # The rest of a 143-row sheet is bigger than any one counterparty, and
        # sorting it with them would put it at the top and fold a real name away.
        record = _account(_five(), {"debit_movement": 9000, "closing_debit": 400}, 143)
        ledger.add_remainder_rows(record)
        # A budget that keeps SOME rows. At budget=1 the trim folds everything
        # into one row, the list above it is empty, and this test asserted
        # nothing while passing — which is how it first missed the bug.
        full = len(ledger.render_record(record))
        trimmed = next(
            candidate for candidate in (
                ledger.fit_to_budget(record, budget) for budget in range(full, 0, -10))
            if 2 <= len(_items(candidate)) - 1 <= 4
        )
        kept = _items(trimmed)[:-1]
        self.assertTrue(kept, "the trim must leave detail rows to inspect")
        for row in kept:
            self.assertIsNone(ledger._residual_count(row, DEBT_COLUMNS))
        at = DEBT_COLUMNS.index("debit_movement")
        self.assertEqual([r[at] for r in kept],
                         [r[at] for r in _five()][:len(kept)],
                         "the kept rows are the largest counterparties")

    def test_an_unknown_remainder_stays_unknown_through_a_trim(self) -> None:
        record = _account(_five(), {"debit_movement": 2000}, 143)
        ledger.add_remainder_rows(record)
        trimmed = ledger.fit_to_budget(record, budget=1)
        self.assertIsNone(_items(trimmed)[-1][DEBT_COLUMNS.index("closing_debit")])


class ThroughThePassTest(unittest.TestCase):
    def test_the_pass_returns_lists_that_end_in_the_remainder(self) -> None:
        """Through extract_ledger_batch, not add_remainder_rows alone: a correct
        function that the pass never calls is invisible to a unit test."""

        import tempfile
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda
        from src.agents.extraction.structured_extraction import build_extraction_chain

        reply = {"accounts": {"131@2025": {
            "item_columns": DEBT_COLUMNS, "item_count": 8,
            "totals": {"debit_movement": 2000, "closing_debit": 400},
            "rankings": [{"sorted_by": "debit_movement", "items": _five()}]}}}
        chain = build_extraction_chain("SYSTEM", RunnableLambda(
            lambda _p: AIMessage(content=json.dumps(reply, ensure_ascii=False),
                                 response_metadata={"finish_reason": "stop"})))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "so_131.csv"
            path.write_text("Năm 2025\nĐối tác,Số tiền\nKH1,100\n", encoding="utf-8")
            record, error = ledger.extract_ledger_batch(
                chain, [(path.name, "(text)", str(path))])[0]
        self.assertEqual(error, "")
        rows = record["accounts"]["131@2025"]["rankings"][0]["items"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(ledger._residual_count(rows[-1], DEBT_COLUMNS), 3)


class GuidanceAgreesTest(unittest.TestCase):
    GUIDANCE = ("src/templates/business-activity-guidance.md",
                "src/templates/financial-analysis-guidance.md")

    def test_no_guidance_still_forbids_the_row(self) -> None:
        for path in self.GUIDANCE:
            text = (ROOT / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn('không thêm dòng "Khác" để bù', text)

    def test_the_guidance_names_the_labels_the_code_writes(self) -> None:
        for path in self.GUIDANCE:
            text = (ROOT / path).read_text(encoding="utf-8")
            for label in (ledger.RESIDUAL_LABEL, ledger.RESIDUAL_ITEM_LABEL):
                with self.subTest(path=path, label=label):
                    self.assertIn(label, text)

    def test_business_activity_tables_end_in_the_total_row(self) -> None:
        """Sections 3 and 4 close with a pre-filled "Tổng" row at 100%, and the
        guidance quotes that row — so the two must be the same row."""

        from src.agents.specialist import BusinessActivityAnalysis

        row = "| Tổng | — |  | 100% | — | — | — |"
        sent = BusinessActivityAnalysis(llm=None).system_prompt.replace(
            "{{", "{").replace("}}", "}")
        for section in ("## 3. Đầu ra", "## 4. Đầu vào"):
            with self.subTest(section=section):
                table = sent[sent.index(section):].split("(Đơn vị")[0]
                rows = [line for line in table.splitlines() if line.startswith("|")]
                self.assertEqual(rows[-1], row)
        guidance = (ROOT / self.GUIDANCE[0]).read_text(encoding="utf-8")
        self.assertIn(f"`{row}`", guidance, "the guidance must quote the skeleton's row")

    def test_the_ledger_block_names_both_labels(self) -> None:
        text = system_prompt("block_ledger",
                             residual_label=ledger.RESIDUAL_LABEL,
                             residual_item_label=ledger.RESIDUAL_ITEM_LABEL)
        self.assertIn(f"{ledger.RESIDUAL_LABEL} (N)", text)
        self.assertIn(f"{ledger.RESIDUAL_ITEM_LABEL} (N)", text)


if __name__ == "__main__":
    unittest.main()
