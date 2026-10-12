"""The keyword verdict must not vouch for itself on one accidental hit."""

import unittest

from src.agents.documents.document_classification import (
    document_type_scores,
    routing_is_unambiguous,
    rule_classify_document,
)

# A ledger of account 131 whose header puts a "Nợ" column next to a
# "Phải trả…" label. Separators are flattened before matching, so the two cells
# read as the substring "no phai tra" and score a balance sheet.
ACCIDENTAL_HIT = "Ngày\tSố dư Nợ\tPhải trả\n05/01/2025\t1.210.969.204\n"

# The shape BCTC_VVS_2024.pdf really produced: two types well clear of the
# ratio, both feeding the same two specialists.
AGREEING_CANDIDATES = {
    "bao_cao_tai_chinh": 9,
    "bao_cao_ket_qua_kinh_doanh": 5,
    "bang_ke_xuat_nhap_ton_cong_no": 3,
}


class RoutingUnambiguousTest(unittest.TestCase):
    def test_one_accidental_hit_does_not_vouch_for_itself(self) -> None:
        # A scanner's default filename, which is the normal case and not the
        # edge one: nothing in the name to outvote the accident.
        verdict = rule_classify_document(
            "scan001.xls", ACCIDENTAL_HIT, "ho_so_tai_chinh"
        )
        self.assertEqual(verdict["document_type"], "bang_can_doi_ke_toan")
        self.assertEqual(round(verdict["confidence"], 3), 0.6,
                         "the formula caps itself here for distrusting the signal")
        self.assertFalse(verdict["routing_unambiguous"],
                         "one point, nothing else scoring, is not an answer")
        self.assertFalse(verdict["filename_decisive"])

    def test_several_agreeing_candidates_still_skip_the_llm(self) -> None:
        self.assertTrue(routing_is_unambiguous(AGREEING_CANDIDATES))

    def test_a_lone_candidate_is_not_agreement(self) -> None:
        self.assertFalse(routing_is_unambiguous({"bao_cao_tai_chinh": 9}))

    def test_weak_evidence_is_refused_however_it_agrees(self) -> None:
        # Both feed the same specialists, so the signatures agree — but two
        # points is below the floor this project acts on anywhere.
        self.assertFalse(routing_is_unambiguous(
            {"bao_cao_tai_chinh": 2, "bang_can_doi_ke_toan": 2}
        ))


class FilenameOnlyKeywordTest(unittest.TestCase):
    """An account number in the filename is the ledger's own declaration."""

    def test_the_account_number_alone_identifies_the_ledger(self) -> None:
        verdict = rule_classify_document("131.2025.xls", "", "ho_so_tai_chinh")
        self.assertEqual(verdict["document_type"], "so_chi_tiet_khoan_muc_khac")
        self.assertTrue(verdict["filename_decisive"])

    def test_it_outvotes_an_accidental_body_hit(self) -> None:
        verdict = rule_classify_document(
            "331.2025.xls", ACCIDENTAL_HIT, "ho_so_tai_chinh"
        )
        self.assertEqual(verdict["document_type"], "so_chi_tiet_khoan_muc_khac")
        self.assertEqual(verdict["scores"]["so_chi_tiet_khoan_muc_khac"], 3)
        self.assertEqual(verdict["scores"]["bang_can_doi_ke_toan"], 1)

    def test_it_matches_whole_words_only(self) -> None:
        # "131" is a substring of the date 20131231 and must not count.
        scores = document_type_scores("BCTC_20131231.xls", "", "ho_so_tai_chinh")
        self.assertEqual(scores["so_chi_tiet_khoan_muc_khac"], 0)

    def test_it_never_scores_the_body(self) -> None:
        scores = document_type_scores(
            "tong hop.xlsx", "So du 131 dong", "ho_so_tai_chinh"
        )
        self.assertEqual(scores["so_chi_tiet_khoan_muc_khac"], 0)


if __name__ == "__main__":
    unittest.main()
