"""Deterministic check that facts and inference stay separated in a report.

The specialist prompt requires every "Nhận xét" to be split into a **Căn cứ**
line (only what the documents state) and a **Nhận định** line (the analyst's
inference). Prompts alone are not enforcement, so this module re-reads the
finished report and flags the two ways the boundary breaks in practice:

1. an inference connective ("cho thấy", "chứng tỏ", …) inside a **Căn cứ** line —
   a judgement dressed up as a fact;
2. a source citation on a **Nhận định** line — which makes the reader believe an
   inference was read out of the document. Observed in a real report, where a
   ratio figure and the conclusion drawn from it shared one sentence and a
   single trailing citation: "ROE tăng từ X% lên Y%, cho thấy hiệu quả sử dụng
   vốn chủ sở hữu đã được cải thiện [<báo cáo>.pdf, trang N]".

Pure text analysis — no LLM call.
"""

from __future__ import annotations

import re
import unicodedata

EVIDENCE_LABEL = "Căn cứ"
INFERENCE_LABEL = "Nhận định"

# The prompt asks for "Căn cứ"/"Nhận định", but a model that paraphrases the
# label must not make the whole check silently pass. Accent-insensitive.
EVIDENCE_LABEL_VARIANTS = (
    "căn cứ",
    "cơ sở",
    "dẫn chứng",
    "bằng chứng",
    "evidence",
    "facts",
)
INFERENCE_LABEL_VARIANTS = (
    "nhận định",
    "đánh giá",
    "suy luận",
    "nhận xét của chuyên viên",
    "assessment",
    "inference",
)

# Headings/leads that introduce commentary, i.e. places where the split is due.
COMMENTARY_MARKERS = (
    "nhận xét",
    "kết luận",
    "đánh giá chung",
)

# Kept deliberately high-precision: each of these introduces a judgement, not a
# reading. Bare "có thể" is excluded — it appears too often in factual phrasing.
INFERENCE_MARKERS = (
    "cho thấy",
    "chứng tỏ",
    "phản ánh",
    "điều này",
    "cho nên",
    "dẫn đến việc",
    "dự kiến",
    "nhiều khả năng",
    "dường như",
    "có thể cho thấy",
    "có thể thấy",
    "cần theo dõi",
    "đáng lo ngại",
    "tích cực hơn",
)

# [tên file.pdf, trang 12] / [tên file.xlsx, Sheet X] / [tên file.pdf]
CITATION = re.compile(r"\[[^\]\[]*\.(?:pdf|xlsx|xls|csv|txt|md)[^\]\[]*\]", re.IGNORECASE)
_LABEL_LINE = re.compile(r"^\s*[*_>#\s]{0,6}([^:*_\n]{1,40}?)\s*[*_]{0,2}\s*:")


def _strip_accents(text: str) -> str:
    lowered = (text or "").lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _label_of(line: str) -> str | None:
    """Classify a line's leading label as evidence, inference, or neither."""

    match = _LABEL_LINE.match(line)
    if not match:
        return None
    label = _strip_accents(match.group(1)).strip()
    if not label:
        return None
    if any(_strip_accents(v) == label for v in EVIDENCE_LABEL_VARIANTS):
        return EVIDENCE_LABEL
    if any(_strip_accents(v) == label for v in INFERENCE_LABEL_VARIANTS):
        return INFERENCE_LABEL
    return None


def _excerpt(line: str, limit: int = 110) -> str:
    text = " ".join(line.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def check_assertion_separation(report: str) -> list[str]:
    """Return human-readable findings; empty when the report is well separated."""

    findings: list[str] = []
    for raw_line in (report or "").splitlines():
        label = _label_of(raw_line)
        if label is None:
            continue
        normalized = _strip_accents(raw_line)

        if label == EVIDENCE_LABEL:
            hits = sorted(
                {
                    marker
                    for marker in INFERENCE_MARKERS
                    if _strip_accents(marker) in normalized
                }
            )
            if hits:
                findings.append(
                    f'Dòng "Căn cứ" chứa từ ngữ suy luận ({", ".join(hits)}) '
                    f"— nên chuyển sang Nhận định: {_excerpt(raw_line)}"
                )
        elif CITATION.search(raw_line):
            findings.append(
                'Dòng "Nhận định" có gắn citation nguồn — suy luận không được '
                f"trích dẫn như dữ kiện trong hồ sơ: {_excerpt(raw_line)}"
            )
    return findings


def check_assertion_labelling(report: str) -> list[str]:
    """Flag a report that skipped the Căn cứ / Nhận định split altogether.

    ``check_assertion_separation`` can only audit lines that carry a label, so a
    model that ignores the rule entirely would pass silently. This catches that:
    commentary is present, labels are not.
    """

    text = report or ""
    evidence, inference = count_assertion_pairs(text)
    if evidence or inference:
        # The split is in use; per-line auditing takes over from here.
        if evidence and not inference:
            return [
                'Báo cáo có dòng "Căn cứ" nhưng thiếu dòng "Nhận định" tương ứng '
                "— mỗi mục nhận xét cần đủ cả hai."
            ]
        if inference and not evidence:
            return [
                'Báo cáo có dòng "Nhận định" nhưng thiếu dòng "Căn cứ" tương ứng '
                "— nhận định phải đi kèm dữ kiện."
            ]
        return []

    normalized = _strip_accents(text)
    if not any(_strip_accents(marker) in normalized for marker in COMMENTARY_MARKERS):
        return []
    return [
        "Báo cáo có phần nhận xét/kết luận nhưng không tách "
        '"**Căn cứ**" và "**Nhận định**" — chưa tuân thủ quy tắc phân định '
        "dữ kiện và suy luận, không thể kiểm tra ranh giới tự động."
    ]


def count_assertion_pairs(report: str) -> tuple[int, int]:
    """Return (số dòng Căn cứ, số dòng Nhận định) for coverage reporting."""

    labels = [
        label
        for label in (_label_of(line) for line in (report or "").splitlines())
        if label is not None
    ]
    return labels.count(EVIDENCE_LABEL), labels.count(INFERENCE_LABEL)
