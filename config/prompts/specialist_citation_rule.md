---
name: specialist_citation_rule
description: Footnote citation rule, given to every specialist except financial analysis
---
CITATION RULE:
- Cite sources as FOOTNOTES. In the sentence itself put only a marker of
the form [^1], [^2] right after the fact (before the comma or full stop).
The full source goes in a definition block at the end of what you write.
NEVER write a filename inline like [filename, page X] — markers only.
- Cite only the KEY FACTS, not every number. The key ones are what you
already bolded under HIGHLIGHT RULE: unusual figures, large movements,
threshold breaches, risk signals. A sensible density is ABOUT 1-2 markers
per section. Citing every clause makes the report noisy and the source
list uselessly long.
- But attach a marker only to bolded text that is A FACT READ FROM THE
DOSSIER. Where the bolded text is YOUR conclusion or recommendation, do
NOT attach one — the dossier does not state that conclusion, and a marker
would tell the reader the opposite.
- Two facts from the same source REUSE the same marker; do not mint a new
one.
- Number [^1], [^2], [^3]... continuously across EVERYTHING you write; do
not restart at [^1] in each section.
- Take the filename from the "Document filename:" line of the matching
document in the evidence. Take the page number or sheet name from the
nearest "--- Page N ---" or "--- Sheet: ... ---" marker to that data in the
document body; if the data came from the
[EXTRACTED FINANCIAL STATEMENTS] block, use that line item's "page" field in
the JSON, and if the line item has no "page", use the "page" of the
statement containing it
(balance_sheet/income_statement/cash_flow_statement).
- For a number taken from the [PRE-COMPUTED FINANCIAL METRICS] block, the
source is the FILENAME listed under "NGUỒN SỐ LIỆU" in that block, for the
year the number belongs to.
- NEVER write an internal block label into the report. The strings
[PRE-COMPUTED FINANCIAL METRICS], [EXTRACTED FINANCIAL STATEMENTS] and
[EXTRACTED CREDIT APPLICATION] are prompt-internal labels; a reader of the
report has no idea what they are. Always cite the original document.
- Never invent a page number, a filename or a marker — cite only what you
can actually establish from the sources provided. Every [^N] used in the
body MUST have a matching definition line.
- End what you write with EXACTLY ONE source-definition block, placed
last, separated from the content above by a blank line, a "---" line, then
another blank line. One marker per line, in ascending numeric order:

  ---

  [^1]: <tên file>, trang X
  [^2]: <tên file>, Sheet Y
  [^3]: <tên file>

If you cannot establish the page or sheet, write the filename alone. Do
not repeat this block anywhere in the middle.

Example of the RIGHT density (one marker for the main fact, not sprayed
across the sentence), laid out in the bullet shape COMMENTARY RULE
requires:

  - Dư nợ đạt đỉnh **56,07 tỷ VNĐ tại 07/2025**[^1] rồi giảm liên tục
    xuống 35,94 tỷ VNĐ tại 02/2026, cho thấy khách hàng đang thu hẹp
    quy mô vay.
  - **Cần theo dõi nguyên nhân thu hẹp quy mô** trước khi cấp hạn mức
    mới.

(The "cho thấy..." clause and the bolded recommendation bullet are YOUR
inference, so they carry no marker.)
