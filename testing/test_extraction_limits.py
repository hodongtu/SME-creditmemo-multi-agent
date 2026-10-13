"""What happens at the ceilings must be visible, not repaired into silence."""

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from src.agents.extraction import ledger_extraction as ledger
from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
)
from src.utils.reading.extractors import SHEET_ROW_CAP, row_cap_marker

# Cut mid-object, the way a reply that runs out of tokens arrives.
CUT_REPLY = ('{"accounts":{"131@2025":{"item_count":60,"item_columns":["a"],'
             '"rankings":[{"sorted_by":"a","items":[[1]]}]},"331@2025":{"item_count":50')
WHOLE_REPLY = '{"accounts":{"131@2025":{"item_count":3}},"extraction_notes":[]}'


def _chain(text: str, finish: str | None):
    metadata = {"finish_reason": finish} if finish else {}
    return build_extraction_chain("SYSTEM", RunnableLambda(
        lambda _payload: AIMessage(content=text, response_metadata=metadata)))


def _run(text: str, finish: str | None):
    return run_extraction(
        _chain(text, finish), "so.xlsx", "[]", {"accounts"}, "no llm")


class TruncatedReplyTest(unittest.TestCase):
    def test_a_reply_cut_at_the_ceiling_is_an_error(self) -> None:
        # JsonOutputParser repairs this string into two usable accounts, so
        # without finish_reason the record arrives looking complete.
        for reason in ("length", "max_tokens"):
            with self.subTest(finish_reason=reason):
                record, error = _run(CUT_REPLY, reason)
                self.assertIsNone(record)
                self.assertIn("ran out of output tokens", error)

    def test_a_finished_reply_is_still_parsed(self) -> None:
        for reason in ("stop", None):
            with self.subTest(finish_reason=reason):
                record, error = _run(WHOLE_REPLY, reason)
                self.assertEqual(error, "")
                self.assertEqual(list(record["accounts"]), ["131@2025"])

    def test_prose_instead_of_json_is_an_error(self) -> None:
        record, error = _run("I could not read the ledger.", "stop")
        self.assertIsNone(record)
        self.assertIn("not JSON", error)


class RowCapTest(unittest.TestCase):
    def test_a_sheet_read_to_the_cap_is_named_in_the_notes(self) -> None:
        sheets = [
            {"filename": "so.xlsx", "sheet_name": "TK_131",
             "content": f"A\tB\n1\t2\n{row_cap_marker(SHEET_ROW_CAP)}"},
            {"filename": "so.xlsx", "sheet_name": "TK_331", "content": "A\tB\n1\t2"},
        ]
        notes = ledger.truncation_notes(sheets)
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("TK_131", notes[0])
        self.assertIn(f"{SHEET_ROW_CAP:,}", notes[0])

    def test_the_note_reaches_the_record(self) -> None:
        """A real file deeper than the cap, read through the real reader.

        The note being correct is worth nothing if nothing collects it, and a
        unit test on truncation_notes alone cannot tell the difference.
        """

        rows = "\n".join(f"KH {n},{n * 1000}" for n in range(SHEET_ROW_CAP + 100))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "so_chi_tiet.csv"
            path.write_text(f"Doi tac,So tien\n{rows}\n", encoding="utf-8")
            reply = AIMessage(
                content=json.dumps({"accounts": {"131@2025": {"item_count": 500}}}),
                response_metadata={"finish_reason": "stop"},
            )
            chain = build_extraction_chain("SYSTEM", RunnableLambda(lambda _p: reply))
            results = ledger.extract_ledger_batch(
                chain, [("so_chi_tiet.csv", "(text)", str(path))])
        record, error = results[0]
        self.assertEqual(error, "")
        incomplete = [n for n in record["extraction_notes"]
                      if "INCOMPLETE SHEET" in n]
        self.assertEqual(len(incomplete), 1, record["extraction_notes"])
        self.assertIn("so_chi_tiet.csv", incomplete[0])

    def test_the_prompt_quotes_the_reader_s_own_marker(self) -> None:
        # The prompt tells the model what that line means. If the cap moves and
        # the prompt still names the old number, the model is told to look for
        # a line it will never see.
        self.assertIn(row_cap_marker(SHEET_ROW_CAP),
                      ledger.LEDGER_EXTRACTION_SYSTEM_PROMPT)


class PromptConsistencyTest(unittest.TestCase):
    def test_the_prompt_asks_for_the_top_rows_only_once(self) -> None:
        """One instruction about how many rows to return, not two.

        The prompt carried "COPY EVERY DETAIL ROW of each sheet. Do not sample,
        do not summarise." alongside "Return the LARGEST rows, not all of
        them." A model obeying the first wrote every row of every sheet and ran
        out of output tokens; measured at 24,638 of 32,000 with a reply that
        parsed anyway. This is the second contradiction of its kind in this
        prompt — the first made "null" a compliant answer.
        """

        text = ledger.LEDGER_EXTRACTION_SYSTEM_PROMPT
        self.assertIn("Return the LARGEST rows, not all of them", text)
        for forbidden in ("COPY EVERY DETAIL ROW", "RETURN EVERY DETAIL ROW",
                          "return every detail row"):
            self.assertNotIn(forbidden, text, f"contradicts the top-5 rule: {forbidden}")


if __name__ == "__main__":
    unittest.main()
