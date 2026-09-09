"""Detail-ledger spreadsheets (sổ chi tiết) into one JSON keyed by account.

Customers submit these as one workbook of many sheets, or as many files of one
sheet each — the same six accounts either way. The output is the same shape in
both cases: one record for the whole folder, keyed by account, so a report
asking for "phải thu khách hàng" looks in one place regardless of how the files
arrived.

The whole pass is one LLM call:

1. the classifier has already flagged the files (``bang_ke_xuat_nhap_ton_cong_no``
   and ``so_chi_tiet_khoan_muc_khac``);
2. ``extract_document_text`` has already turned each one into text, split by
   sheet, keeping ``.xlsx`` number formats — ``.csv`` and ``.xls`` come through
   the same door, which is why they work here now and did not before;
3. that text, fenced per file, goes to the model, which returns the finished
   record.

The model is therefore the source of the figures as well as the labels. That is
a deliberate change: the previous design had openpyxl read every cell and asked
the model only to name things, which read the sample workbook exactly but could
not open .xls or .csv at all, and its header/total heuristics were tuned to one
accounting package. Nothing in this module checks the numbers that come back —
the trade was made knowingly, and pretending otherwise in a docstring would be
worse than saying it plainly.

One consequence worth knowing: a ledger past roughly 150 detail rows will run
past the model's output limit and come back as unparseable JSON. The pass then
fails, and because it is not a ``required`` pass the run continues on the raw
text instead of stopping.
"""

import json
import re
from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
)
from src.utils.common import normalize_text
from src.utils.reading.extractors import read_sheets

REQUIRED_TOP_LEVEL_KEYS = {"accounts"}

# Characters the rendered block may occupy, across every ledger file together.
# Rows are never dropped from the stored record — this only bounds what reaches
# the prompt, against the agent's 120k total. Measured: the sample workbook's 48
# rows render to ~14k, so a real ledger fits whole and nothing is aggregated.
LEDGER_BLOCK_CHAR_BUDGET = 40_000
# Label of the row standing in for everything the budget left out. It carries
# the summed figures of those rows, so visible rows still add up to the total.
RESIDUAL_LABEL = "Các đối tác còn lại"
# The columns a detail row carries text in. A row is a positional array now, so
# these are looked up through "item_columns" rather than read off the row —
# naming, aggregating and total-row detection all need to know which cells hold
# words and which hold money.
LABEL_KEYS = ("counterparty_name", "counterparty_code", "item_name")


def _label_positions(columns: list[str]) -> list[int]:
    """Indexes of the text columns, in the order they appear."""

    return [i for i, c in enumerate(columns) if c in LABEL_KEYS]
# Names a printed total row carries. Kept as a set because the prompt already
# tells the model to leave these out of "items" and it does it anyway — on the
# sample workbook the inventory sheet came back with an eleventh row called
# "Tổng" holding the column sums, which would have every figure of that account
# counted twice by anything adding the rows up.
TOTAL_ROW_NAMES = {"tong", "tong cong", "cong", "total", "sum"}
# How many rows each report section asks for. The guidance says "top 5" and
# "NHIỀU NHẤT 5" throughout, so this is the number, not a budget to tune.
TOP_ROWS = 5
# Which column each report section ranks by, per account category. ONE RANKING PER
# COLUMN — the account comes back holding a separate ordered list for each entry
# here, each labelled with the column it was sorted by.
#
# The earlier design asked for the UNION of those top fives as a single list. A
# live run showed why that fails: computing a union of three rankings is several
# steps of arithmetic done in the head, and nothing in the answer lets a checker
# see whether it came out right. On 2026-09-08 every account with more than five
# rows came back with exactly five, and TK 341 dropped the lender ranked 2nd by
# movement and 4th by balance while keeping one that led no ranking at all. Split
# into one list per column, the task is "sort by this column, take five" — one
# step, and both the order and the count are checkable from the answer itself.
#
# Traced from src/templates: financial-analysis-guidance 1.1 / 2.2.1a-c / 2.2.2a-b
# and business-activity-guidance mục 1 (sơ đồ) / mục 2 / mục 3 / mục 4.
TOP_ROW_CRITERIA: dict[str, tuple[str, ...]] = {
    "receivable": ("debit_movement", "closing_debit", "closing_credit"),
    "payable": ("credit_movement", "closing_debit", "closing_credit"),
    "inventory": ("outflow_value", "closing_value"),
    # No section enumerates rows of these. FA 2.2.2c (vay nợ) is prose with no
    # table, and 2.2.1e/2.2.2e take their figures from the balance sheet, not the
    # ledger. One ranking by the closing balance of the side the account sits on
    # is enough to let the prose name its largest counterparties; asking for the
    # movement columns too was work nobody reads.
    "borrowing": ("closing_credit",),
    "other_payable": ("closing_credit",),
    "other_receivable": ("closing_debit",),
    "cash": ("closing_debit",),
    "fixed_asset": ("closing_debit",),
    "equity": ("closing_credit",),
    # Which side this sheet sits on is exactly what is unknown, so keep both.
    "unknown": ("closing_debit", "closing_credit"),
    "": ("closing_debit", "closing_credit"),
}


LEDGER_EXTRACTION_SYSTEM_PROMPT = """
You read Vietnamese accounting detail ledgers (sổ chi tiết tài khoản, bảng tổng
hợp nhập xuất tồn) and return ONE JSON record covering every file given, for SME
credit underwriting.

The input is a JSON array, one object per sheet:
  [{{"filename": "...", "sheet_name": "...", "content": "<tab-separated grid>"}}]

Stay inside each object. NEVER take a figure from one file and file it under
another.

YOU ARE THE SOURCE OF EVERY NUMBER in the record. No program re-reads the file to
correct you. Copy each figure digit for digit as printed.

RETURN ONE ENTRY FOR EVERY SHEET. Work through the whole array; do not stop early
and do not merge two sheets unless they are the same account AND the same period.
A sheet you cannot place still has to be named in "extraction_notes" — dropping
one in silence is the worst failure here, because the record still looks complete
while half the ledger is gone.

COPY EVERY DETAIL ROW of each sheet. Do not sample, do not summarise.

════ OUTER FRAME ════
{{"accounts": {{...}}, "unmapped_columns": [], "extraction_notes": []}}

These three keys are SIBLINGS. "unmapped_columns" and "extraction_notes" sit
BESIDE "accounts", never inside it — "accounts" holds account entries only.

- "unmapped_columns": column headings you could not map to a canonical field,
  KEPT IN THE ORIGINAL VIETNAMESE. Letting a Vietnamese name through beats
  inventing an English field name for a column nobody has understood yet.
- "extraction_notes": which sheets you could not place, and why. That is all.

════ THE "accounts" KEY — ONE FORMAT ONLY ════
    <account number>@<YYYYMMDD>-<YYYYMMDD>

Example: "131@20250101-20251231". ALWAYS carries the period, even when the whole
dossier holds only one.

    Full year 2025        -> 131@20250101-20251231
    Q1 2025               -> 131@20250101-20250331
    First 6 months 2025   -> 131@20250101-20250630
    First 9 months 2025   -> 131@20250101-20250930
    Year 07/24-06/25      -> 131@20240701-20250630

WHERE THE ACCOUNT NUMBER COMES FROM

THE FILE NAME AND THE SHEET NAME ARE STRONG SIGNALS, often the strongest ones.
Customers export one account per file or per sheet, and the printed title is
usually generic — "BÁO CÁO CÂN ĐỐI PHÁT SINH CÔNG NỢ CỦA MỘT TÀI KHOẢN" names no
account at all. Read the sheet name before deciding you cannot tell.

Vietnamese accounting software abbreviates heavily. Decode these:

  P.THU  PTHU  PT      phải thu       -> receivable
  P.TRA  PTRA  PTr     phải trả       -> payable
  KHAC   #KHAC          khác          -> the other_* category of the same side,
                                         so "P.TRA KHAC" is other_payable (338)
                                         and "PTHU_KHAC" is other_receivable (138)
  NXT    N-X-T          nhập xuất tồn -> inventory
  TK <n>                tài khoản <n> -> use that number
  CN     CNo            công nợ
  VAY                   vay           -> borrowing
  KH                    khách hàng    TM  thương mại    NVL  nguyên vật liệu

"unknown" IS ONLY FOR A SHEET WHOSE SUBJECT YOU CANNOT MAKE OUT. Never return
"unknown" because column headings do not match the canonical names, because the
table is laid out unusually, or because a title is generic — a sheet headed
"BÁO CÁO TỔNG HỢP NHẬP XUẤT TỒN" is an inventory sheet whatever its columns look
like. Headings you cannot place go in "unmapped_columns"; that is what the key
is for.

1. If the file or sheet PRINTS a number ("SO CHI TIET TK 131.xlsx", a title
   "Tài khoản 331", a column "TK 156"), use exactly that, code_source="printed".
2. If nothing prints one, use EXACTLY ONE conventional code from the table below,
   code_source="convention". Use the code in the table, never another from the
   same family:

     cash              -> 112        receivable        -> 131
     other_receivable  -> 138        fixed_asset       -> 211
     payable           -> 331        other_payable     -> 338
     borrowing         -> 341        equity            -> 411

   INVENTORY IS THE ONE THAT SPLITS. Group 15x has a code per kind of stock, so
   read the item column — what is actually being counted — and pick from it:

     goods bought to resell (vehicles, equipment, merchandise)  -> 156
     raw materials and supplies consumed in production          -> 152
     tools and instruments (công cụ, dụng cụ)                    -> 153
     finished goods the company manufactured                     -> 155
     work in progress (chi phí SXKD dở dang)                     -> 154
     goods sent out on consignment (hàng gửi đi bán)             -> 157

   A sheet whose item column reads "Mooc, Đầu kéo, Satxi, Tải thùng kín" is
   vehicles held for resale, so 156. One reading "Thép tấm, Sơn, Vòng bi" is raw
   material, so 152. When a sheet mixes kinds, take the one holding most of the
   value and say so in "code_evidence".

   Getting this wrong is severe: a borrowings ledger filed as 311 instead of 341,
   or other payables as 331 instead of 338, puts one account's figures under
   another account's name while every number still looks right.

Two edge cases:
- No readable period    -> "131@unknown"
- No identifiable code  -> "unknown_<sheet name>@<period>"

Two entries with different periods are TWO SEPARATE FACTS — never add their
balances together. Several sheets with the SAME account AND the SAME period merge
into one entry: Vietnamese ledgers often split one account across sheets by month
or by product group.

════ EACH ENTRY IN "accounts" — EXACTLY 12 KEYS ════
"category"       one of: cash receivable other_receivable inventory
                 fixed_asset payable other_payable borrowing equity unknown
                 Prefer "unknown" over a guess: an analyst can read an unlabelled
                 sheet, but will TRUST a wrong label.
"code_source"    "printed" or "convention", per the rule above.
"code_evidence"  one sentence naming where you read the number (file name, sheet
                 name, report title).
"source_files"   the file names that fed this entry, EXACTLY as given.
"source_sheet_name"
                 the sheet names that fed this entry, EXACTLY as printed on the
                 tab — "P.TRA KHAC", "TK VAY", "NXT". A list, because several
                 sheets of one account and one period merge into a single entry.
                 Write [] for a .csv, which has no sheet.
"period"         {{"from","to","as_printed"}} — see below.
"source_columns" {{canonical_field: "column heading exactly as printed"}}. Record
                 EVERY column you placed, identifier columns included.
"units"          {{field: "vnd" or "quantity"}}. Only for fields that actually
                 appear in "item_columns".
"totals"         {{field: number}} — the total for the WHOLE account, across
                 every detail row, not only the ones you return. Read it off the
                 printed "Tổng cộng" row when the sheet has one; otherwise add up
                 all the detail rows before selecting. This is the denominator
                 the report divides by to state a counterparty's share, so a
                 total covering only the rows you kept is wrong.
"item_count"     how many detail rows the SHEET holds — not len(items). It tells
                 the reader the returned rows are 5 of 143.
"item_columns"   the field names of a detail row, in the order the values come.
"rankings"       a LIST of {{"sorted_by": "<column>", "items": [rows]}} — one
                 entry per column listed for this category, each holding that
                 column's top 5. See "WHICH ROWS TO RETURN".

════ "period" ════
"from"/"to"    ISO "YYYY-MM-DD", read from the banner line above the table.
"as_printed"   that banner line VERBATIM.

Common phrasings and how they expand:
    "Từ ngày 01/01/2025 đến ngày 31/12/2025" -> 2025-01-01 / 2025-12-31
    "Kỳ báo cáo: 01/01/2025 - 31/12/2025"    -> 2025-01-01 / 2025-12-31
    "Năm 2024"                               -> 2024-01-01 / 2024-12-31
    "Quý 4/2024"                             -> 2024-10-01 / 2024-12-31
    "Tháng 12 năm 2024"                      -> 2024-12-01 / 2024-12-31
If no line states a period, leave all three "" — do NOT infer one from a file
name that merely contains a year.

════ WHICH ROWS TO RETURN — "rankings" ════
Return the LARGEST rows, not all of them. The report lists at most five
counterparties or items per section, so everything past that is paid for and
never read.

Different sections rank by different columns, and one account is asked for under
several of them. So an account does NOT come back with one list of rows. It comes
back with ONE SEPARATE LIST PER COLUMN, each labelled with the column it was
sorted by. Here is which columns each category gets a list for:

<<TOP_ROW_CRITERIA_TABLE>>

Build each list on its own, and do NOT merge them:

  1. Take the column named on the line for this account's category.
  2. Sort EVERY detail row of the sheet by that column, largest first, by
     ABSOLUTE value — so a large credit balance is not sorted below a small
     debit one.
  3. Keep the first 5. A sheet with fewer than 5 detail rows: keep all of them.
  4. Write {{"sorted_by": "<that column name>", "items": [ ...those rows... ]}}.
  5. Repeat from step 1 for the next column on the line.

A counterparty that leads two of the lists APPEARS IN BOTH, with the same figures
in both. Do not remove it from one of them, and do not try to combine the lists
into a single de-duplicated list — the report reads one list per section and each
section needs its own order.

"sorted_by" must be one of the names in "item_columns", spelled identically.

"totals" still covers the WHOLE account and "item_count" still counts the WHOLE
sheet, no matter how few rows the lists hold.

════ THE ROWS INSIDE "items" — THE EASIEST PART TO GET WRONG ════
Name the columns ONCE per account in "item_columns", then give each detail row
as an ARRAY of values in that exact order. Never repeat the field names on a row.
The same "item_columns" governs the rows of EVERY list in "rankings".

Debt sheets (receivables, payables, borrowings) — the CODE and the NAME are
SEPARATE COLUMNS. A live run put "HSCANTHO" where the counterparty name belonged
and the row became unreadable as either:

"item_columns": ["counterparty_code","counterparty_name","opening_debit",
                 "opening_credit","debit_movement","credit_movement","closing_debit"],
"items": [["HSCANTHO","Cty Hoa Sen Cần Thơ",0,0,20738000,20738000,0],
          ["PHUTHINH","Cty Phú Thịnh",500000000,0,4825000000,4325000000,0]]

Stock sheets (nhập xuất tồn) use their own column set:

"item_columns": ["item_name","opening_quantity","opening_value",
                 "inflow_quantity","inflow_value"],
"items": [["Đầu kéo",191,205670531917,1381,1590661714792]]

EVERY ROW MUST HAVE EXACTLY len(item_columns) VALUES. A short row shifts every
value after the gap into the wrong column, and the JSON stays valid while the
figures stop meaning anything. If a column has no value for a row, write "" for
a text column and 0 for a number — never leave it out.

If the file has no code column, put "" in the counterparty_code position and
keep the column. Never put a code in the name position or a name in the code
position.

Numbers are WHOLE ĐỒNG: no separators, no unit, no brackets.
    right: 225510140846
    wrong: "225.510.140.846"   225,51 tỷ   (225510140846)
Negatives take a minus sign. An empty cell or a dash is 0.

A PRINTED TOTAL ROW ("Tổng", "Tổng cộng", "Cộng", "Total") IS NOT A DETAIL ROW:
keep it out of "items" and use it to fill "totals" instead. It is the account's
own figure for every row including the ones you are not returning, which is
exactly what "totals" needs.

A TOTAL ROW IS IDENTIFIED BY ITS LABEL, NEVER BY ITS FIGURES. Two traps:

* It is often printed ABOVE the detail rows, directly under the headings — do
  not assume it sits at the bottom.
* A detail row may carry the SAME amount as the total. When one counterparty
  holds nearly the whole balance, its row and the total row read alike; the one
  with a counterparty name is a detail row and MUST be kept. Dropping it loses
  the largest position in the account.

Every row that names a counterparty or an item is a detail row. Count them: your
"item_count" must equal the number of named rows in the sheet, not fewer.

════ CANONICAL FIELD NAMES — TWO SETS, NEVER MIXED ════
Debt (receivable, payable, borrowing):
    counterparty_code counterparty_name booking_unit
    opening_debit opening_credit debit_movement credit_movement
    closing_debit closing_credit
Stock:
    item_name
    opening_quantity opening_value inflow_quantity inflow_value
    outflow_quantity outflow_value closing_quantity closing_value

A row counter ("Stt", "TT") is NOT a field: drop it, and do not list it in
"unmapped_columns" either. Never map two headings to the same field.

HEADINGS OFTEN SPAN TWO ROWS. A stock sheet prints the group on one line and the
measure underneath it:

    Loại xe | Dư đầu   | Dư đầu   | Nhập vào | Nhập vào | ...
    Loại xe | Số lượng | Giá trị  | Số lượng | Giá trị  | ...

Join them top-to-bottom before mapping: "Dư đầu" + "Số lượng" -> opening_quantity,
"Nhập vào" + "Giá trị" -> inflow_value. Record the joined text in
"source_columns" ("Dư đầu - Số lượng"). Two header rows are a normal layout, not
a reason to give up on the sheet.

════ WORKED EXAMPLE (one debt entry, one stock entry) ════
{{
  "accounts": {{
    "131@20250101-20251231": {{
      "category": "receivable",
      "code_source": "convention",
      "code_evidence": "Sheet TK_131, tieu de Bao cao chi tiet cong no phai thu; file khong in so hieu.",
      "source_files": ["VIMID_so_chi_tiet.xlsx"],
      "source_sheet_name": ["TK_131"],
      "period": {{
        "from": "2025-01-01",
        "to": "2025-12-31",
        "as_printed": "Từ ngày 01/01/2025 đến ngày 31/12/2025"
      }},
      "source_columns": {{
        "counterparty_code": "Mã khách hàng",
        "counterparty_name": "Tên khách hàng",
        "opening_debit": "Dư nợ đầu kỳ",
        "opening_credit": "Dư có đầu kỳ",
        "debit_movement": "Phát sinh nợ",
        "credit_movement": "Phát sinh có",
        "closing_debit": "Dư nợ cuối kỳ",
        "closing_credit": "Dư có cuối kỳ",
        "booking_unit": "Tên đơn vị"
      }},
      "units": {{
        "closing_debit": "vnd",
        "credit_movement": "vnd",
        "debit_movement": "vnd",
        "opening_credit": "vnd",
        "opening_debit": "vnd"
      }},
      "totals": {{
        "opening_debit": 225510140846,
        "opening_credit": 10000000,
        "debit_movement": 527543796658,
        "credit_movement": 528855090000,
        "closing_debit": 224188847504,
        "closing_credit": 0
      }},
      "item_count": 2,
      "item_columns": ["counterparty_code","counterparty_name","opening_debit","opening_credit","debit_movement","credit_movement","closing_debit"],
      "rankings": [
        {{"sorted_by": "debit_movement", "items": [
          ["","ĐỒNG VĂN NGỌC",0,10000000,1391300000,1381300000,0],
          ["","NGUYỄN XUÂN VĨ",0,0,20738000,20738000,0]
        ]}},
        {{"sorted_by": "closing_debit", "items": [
          ["","NGUYỄN XUÂN VĨ",0,0,20738000,20738000,0],
          ["","ĐỒNG VĂN NGỌC",0,10000000,1391300000,1381300000,0]
        ]}},
        {{"sorted_by": "closing_credit", "items": [
          ["","NGUYỄN XUÂN VĨ",0,0,20738000,20738000,0],
          ["","ĐỒNG VĂN NGỌC",0,10000000,1391300000,1381300000,0]
        ]}}
      ]
    }},
    "156@20250101-20251231": {{
      "category": "inventory",
      "code_source": "convention",
      "code_evidence": "Sheet NXT, tieu de Bao cao tong hop nhap xuat ton; file khong in so hieu.",
      "source_files": ["VIMID_so_chi_tiet.xlsx"],
      "source_sheet_name": ["NXT"],
      "period": {{
        "from": "2025-01-01",
        "to": "2025-12-31",
        "as_printed": "Từ ngày 01/01/2025 đến ngày 31/12/2025"
      }},
      "source_columns": {{
        "item_name": "Loại xe",
        "opening_quantity": "Dư đầu - Số lượng",
        "opening_value": "Dư đầu - Giá trị",
        "inflow_quantity": "Nhập vào - Số lượng",
        "inflow_value": "Nhập vào - Giá trị",
        "outflow_quantity": "Xuất ra - Số lượng",
        "outflow_value": "Xuất ra - Giá trị",
        "closing_quantity": "Dư cuối - Số lượng",
        "closing_value": "Dư cuối - Giá trị"
      }},
      "units": {{
        "closing_quantity": "quantity",
        "closing_value": "vnd",
        "inflow_quantity": "quantity",
        "inflow_value": "vnd",
        "opening_quantity": "quantity",
        "opening_value": "vnd",
        "outflow_quantity": "quantity",
        "outflow_value": "vnd"
      }},
      "totals": {{
        "opening_quantity": 705,
        "opening_value": 775511777881,
        "inflow_quantity": 3059,
        "inflow_value": 3577157767500,
        "outflow_quantity": 2206,
        "outflow_value": 2564487338322,
        "closing_quantity": 1558,
        "closing_value": 1788182207060
      }},
      "item_count": 2,
      "item_columns": ["item_name","opening_quantity","opening_value","inflow_quantity","inflow_value","outflow_quantity","outflow_value","closing_quantity","closing_value"],
      "rankings": [
        {{"sorted_by": "outflow_value", "items": [
          ["Đầu kéo",191,205670531917,1381,1590661714792,1001,1156312267740,571,640019978969],
          ["Mooc",49,22017037516,113,48305699921,112,48335332899,50,21987404538]
        ]}},
        {{"sorted_by": "closing_value", "items": [
          ["Đầu kéo",191,205670531917,1381,1590661714792,1001,1156312267740,571,640019978969],
          ["Mooc",49,22017037516,113,48305699921,112,48335332899,50,21987404538]
        ]}}
      ]
    }}
  }},
  "unmapped_columns": [],
  "extraction_notes": []
}}

Return EXACTLY this schema and nothing else.
"""


# ── Fitting the record to the prompt's character budget ───────────────────

def render_record(record: dict[str, Any], indent: int = 2) -> str:
    """The record as the agent sees it: framed by indent, one detail row per line.

    ``json.dumps(indent=2)`` breaks EVERY array element onto its own line, which
    turns a seven-value row into seven lines and undoes most of what the
    columnar shape saves — 37% off instead of 56%, measured. So rows are dumped
    compactly and the frame around them keeps its indentation.

    Assembled from ``json.dumps`` piece by piece rather than by rewriting a
    dumped string: a regex over JSON breaks the moment a value contains a
    bracket, and Vietnamese counterparty names do.

    ``fit_to_budget`` measures with this same function. Two renderers would
    budget against one shape and print another — trimming rows that would have
    fit, or overflowing without noticing.
    """

    def encode(value: Any, depth: int) -> str:
        pad = " " * (indent * depth)
        inner = pad + " " * indent
        if isinstance(value, list) and value and all(isinstance(v, list) for v in value):
            rows = ",\n".join(
                inner + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in value
            )
            return f"[\n{rows}\n{pad}]"
        # "rankings" is a list of dicts. Without this branch it falls through to
        # the compact dump at the bottom and the whole thing — every ranking,
        # every row — lands on one line, undoing the row-per-line shape the
        # budget was measured against.
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            body = ",\n".join(inner + encode(item, depth + 1) for item in value)
            return f"[\n{body}\n{pad}]"
        if isinstance(value, dict):
            body = ",\n".join(
                f"{inner}{json.dumps(key, ensure_ascii=False)}: {encode(item, depth + 1)}"
                for key, item in value.items()
            )
            return f"{{\n{body}\n{pad}}}" if value else "{}"
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return encode(record, 0)


def _row_value(row: list[Any], index: int) -> float:
    """One column of a row as a magnitude, for ranking. Missing reads as zero."""

    if index >= len(row):
        return 0.0
    value = row[index]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return abs(float(value))


def _magnitude(row: list[Any], label_at: set[int]) -> float:
    """How big this row is, across every column rather than one of them."""

    return max(
        (abs(v) for i, v in enumerate(row)
         if i not in label_at
         and isinstance(v, (int, float)) and not isinstance(v, bool)),
        default=0.0,
    )


def _aggregate(rows: list[list[Any]], columns: list[str]) -> list[Any]:
    """One row summing every column of the rows it stands in for.

    Positional, so it must be exactly as wide as ``item_columns``: a short row
    silently shifts every value after the gap into the wrong column, and the
    JSON stays valid while the figures stop meaning anything.
    """

    label_at = _label_positions(columns)
    out: list[Any] = []
    for index in range(len(columns)):
        if index in label_at:
            out.append(f"{RESIDUAL_LABEL} ({len(rows)})" if index == label_at[-1] else "")
            continue
        total = sum(
            row[index] for row in rows
            if index < len(row)
            and isinstance(row[index], (int, float))
            and not isinstance(row[index], bool)
        )
        out.append(round(total))
    return out


def fit_to_budget(
    record: dict[str, Any],
    budget: int = LEDGER_BLOCK_CHAR_BUDGET,
) -> dict[str, Any]:
    """The record trimmed to ``budget`` characters, losing no figures.

    Rows that do not fit are replaced by one aggregate row carrying their summed
    columns, so visible rows still reconcile to ``totals`` — the same principle
    as naming skipped files in ``discover_documents`` rather than dropping them.

    Budgets the whole record at once. Doing it per file gave every ledger file
    the full allowance, so six files could claim 240k against a 120k prompt and
    push the document content off the end.
    """

    def shrink(keep: int) -> dict[str, Any]:
        accounts = {}
        for key, account in (record.get("accounts") or {}).items():
            columns = account.get("item_columns") or []
            label_at = set(_label_positions(columns))
            # Trim each ranking on its own. Merging them first and cutting once
            # would let a heavy list eat a light one's allowance, and the whole
            # point of the split is that a section reads its own list.
            rankings = []
            for ranking in account.get("rankings") or []:
                rows = ranking.get("items") or []
                if len(rows) <= keep:
                    rankings.append(ranking)
                    continue
                # Sort again here rather than trusting the order that arrived.
                # _note_slice_problems reports a badly ordered list, but reporting
                # happens after this trim, and taking rows[:keep] off a list the
                # model shuffled would throw away the largest row for good. The
                # aggregate row stands for the tail of THIS list, so it stays in it.
                at = columns.index(ranking["sorted_by"]) \
                    if ranking.get("sorted_by") in columns else None
                ordered = sorted(
                    rows,
                    key=(lambda r: _row_value(r, at)) if at is not None
                    else (lambda r: _magnitude(r, label_at)),
                    reverse=True,
                )
                rankings.append({**ranking,
                                 "items": ordered[:keep]
                                 + [_aggregate(ordered[keep:], columns)]})
            accounts[key] = {**account, "rankings": rankings}
        return {**record, "accounts": accounts}

    def size(candidate: dict[str, Any]) -> int:
        return len(render_record(candidate))

    if size(record) <= budget:
        return record
    widest = max((len(r.get("items") or [])
                  for a in (record.get("accounts") or {}).values()
                  for r in (a.get("rankings") or [])), default=0)
    # Monotone in `keep`, so a binary search costs ~log2(rows) renders.
    low, high = 0, widest
    while low < high:
        mid = (low + high + 1) // 2
        if size(shrink(mid)) <= budget:
            low = mid
        else:
            high = mid - 1
    return shrink(low)


# ── The pass itself: fence every file, one call, one shared record ─────────

def _render_criteria_table() -> str:
    """The criteria table as prompt text, grouped by the columns they share.

    Written out of TOP_ROW_CRITERIA rather than alongside it: the model ranks by
    what this table says and _note_slice_problems checks against the dict, so two
    hand-kept copies would let a category be graded by a rule it was never given.
    """

    grouped: dict[tuple[str, ...], list[str]] = {}
    for category, columns in TOP_ROW_CRITERIA.items():
        if not category:  # the fallback entry has no name to show the model
            continue
        grouped.setdefault(columns, []).append(category)
    names = [", ".join(cats) for cats in grouped.values()]
    width = max(len(n) for n in names)
    return "\n".join(
        f"  {name:<{width}} : {len(columns)} list"
        f"{'s' if len(columns) > 1 else ''} — {', '.join(columns)}"
        for name, columns in zip(names, grouped)
    )


LEDGER_EXTRACTION_SYSTEM_PROMPT = LEDGER_EXTRACTION_SYSTEM_PROMPT.replace(
    "<<TOP_ROW_CRITERIA_TABLE>>", _render_criteria_table()
)
assert "<<TOP_ROW_CRITERIA_TABLE>>" not in LEDGER_EXTRACTION_SYSTEM_PROMPT


def build_ledger_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for detail-ledger workbooks."""

    return build_extraction_chain(LEDGER_EXTRACTION_SYSTEM_PROMPT, llm)


# Characters the JSON payload may occupy, matching the per-document budget the
# rest of the pipeline applies to OCR text. Reading straight from the file skips
# that cap, so it is re-applied here — otherwise a ledger pass would send more
# than any other pass is allowed to.
PAYLOAD_CHAR_BUDGET = 120_000


def _without_row_counter(content: str) -> str:
    """The sheet's TSV with any row-counter column removed.

    Only a column whose every numeric value is a consecutive integer starting at
    1 — an "Stt" or "TT" and nothing else. The prompt already tells the model to
    ignore these, so dropping them changes no answer; it just stops paying to
    send them. Worth about 9% of the payload on a wide ledger.

    Deliberately strict rather than name-based: a heading match would have to
    guess at every accounting package's wording, and dropping the wrong column
    loses figures in silence. On the sample workbook this fires on the one sheet
    that has such a column and leaves the other five untouched.
    """

    rows = [line.split("\t") for line in content.splitlines()]
    if len(rows) < 6:
        return content
    width = max(len(r) for r in rows)
    drop = set()
    for column in range(width):
        cells = [r[column].strip() if column < len(r) else "" for r in rows]
        digits = [c for c in cells if c.replace(".", "").replace(",", "").isdigit()]
        if len(digits) < 3 or len(digits) < len(cells) * 0.6:
            continue
        if [int(c.replace(".", "").replace(",", "")) for c in digits] == list(
            range(1, len(digits) + 1)
        ):
            drop.add(column)
    if not drop:
        return content
    return "\n".join(
        "\t".join(v for i, v in enumerate(r) if i not in drop) for r in rows
    )


def read_ledger_sheets(
    documents: list[tuple[str, str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Every sheet of every ledger file, and the notes about what did not open.

    Read from the file rather than split back out of ``content``: that string
    carries "--- Sheet: TK VAY (23 dòng x 10 cột, đã bỏ 1 dòng trùng lặp) ---",
    and a regex over it breaks the moment a sheet name contains a dash or the
    parenthetical gains a field — which it did this week. ``read_sheets`` is the
    same reader ``extract_excel_text`` uses, so the two cannot disagree.

    Costs one re-read of each workbook, measured at 29 ms against an LLM call of
    about 25 seconds.

    A file that will not open is named in the notes and left out. It is NOT
    retried by parsing ``content``: falling back to that rebuilds exactly the
    fragile path this replaced.
    """

    sheets: list[dict[str, str]] = []
    notes: list[str] = []
    for filename, _content, path in documents:
        if not path:
            notes.append(f"Could not read {filename}: no file path.")
            continue
        try:
            found = read_sheets(path)
        except Exception as exc:
            notes.append(f"Could not read {filename}: {type(exc).__name__}: {exc}"[:300])
            continue
        sheets += [
            {"filename": filename,
             "sheet_name": sheet.sheet_name,
             "content": _without_row_counter(sheet.content)}
            for sheet in found
        ]
    return sheets, notes


def _json_payload(sheets: list[dict[str, str]]) -> tuple[str, list[str]]:
    """The sheets as one JSON array, trimmed to the budget at sheet boundaries.

    Whole sheets go, never part of one: half a ledger sheet reads as a complete
    one to the model, and the figures it would report from the surviving rows
    would look like the account's totals.
    """

    kept = list(sheets)
    dropped: list[str] = []
    while kept:
        payload = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
        if len(payload) <= PAYLOAD_CHAR_BUDGET:
            return payload, dropped
        gone = kept.pop()
        dropped.append(f"{gone['filename']} › {gone['sheet_name'] or '(single sheet)'}")
    return "[]", dropped


def sheet_inventory(documents: list[tuple[str, str, str]]) -> list[str]:
    """Every "file › sheet" the payload contains, in reading order."""

    sheets, _notes = read_ledger_sheets(documents)
    return [
        f"{s['filename']} › {s['sheet_name']}" if s["sheet_name"] else s["filename"]
        for s in sheets
    ]


def _drop_total_rows(record: dict[str, Any]) -> None:
    """Remove any printed total row the model left among the detail rows.

    "totals" already holds those figures, so a row named "Tổng" sitting in
    "items" is the same money twice — and it reads as an ordinary counterparty
    to everything downstream, including ``fit_to_budget``'s aggregate row.
    Rewrites ``item_count`` alongside, because the two disagreeing is worse than
    either being wrong on its own.
    """

    for account in (record.get("accounts") or {}).values():
        if not isinstance(account, dict):
            continue
        label_at = _label_positions(account.get("item_columns") or [])
        # The same printed total row can land in several rankings, so count the
        # distinct labels dropped rather than the drops: subtracting once per
        # ranking would take one real detail row off item_count for every extra
        # list the account happens to carry.
        dropped_labels: set[str] = set()
        for ranking in account.get("rankings") or []:
            if not isinstance(ranking, dict):
                continue
            rows = ranking.get("items") or []
            kept = []
            for row in rows:
                names = [
                    normalize_text(str(row[i])) for i in label_at if i < len(row)
                ]
                hit = [n for n in names if n in TOTAL_ROW_NAMES]
                if hit:
                    dropped_labels.add(hit[0])
                else:
                    kept.append(row)
            if len(kept) != len(rows):
                ranking["items"] = kept
        if dropped_labels:
            # Decremented, not reassigned: item_count is the sheet's own detail
            # row count and a ranking is only the top-N slice of it, so setting
            # it to a list length would shrink the sheet to the size of an
            # excerpt. A printed total was never a detail row, so it comes off.
            declared = account.get("item_count")
            longest = max((len(r.get("items") or [])
                           for r in (account.get("rankings") or [])
                           if isinstance(r, dict)), default=0)
            if isinstance(declared, int):
                account["item_count"] = max(longest, declared - len(dropped_labels))


def _note_slice_problems(record: dict[str, Any]) -> None:
    """Check what stays true once each ranking is a top-N slice of one column.

    ``item_count`` used to equal ``len(items)``, and comparing them caught a
    reply cut off mid-array — ``JsonOutputParser`` repairs truncated JSON rather
    than raising, so a cut answer arrives looking whole, and one live run had an
    account declare 26 rows and carry 18. That comparison is now meaningless:
    the two differ by design on every account with more than a handful of rows.

    Five things still hold, and the first three only became checkable when each
    list was cut down to a single criterion:

    * every column named in ``TOP_ROW_CRITERIA`` for this category has a list,
      and every list's ``sorted_by`` is a real column of ``item_columns``;
    * each list is ordered by its own column, largest absolute value first;
    * a counterparty appearing in two lists carries the same figures in both;
    * a list holds at least ``TOP_ROWS`` rows whenever the sheet had that many —
      the report asks for five, and fewer than five from a sheet that has them is
      a failure, not a shorter answer;
    * its column sums cannot exceed the account's own totals. A part is never
      larger than the whole, so exceeding means either the wrong rows were kept
      or ``totals`` is not the account's.

    None of these notices a model that quietly picked the wrong five and ordered
    them correctly. Nothing here can: that would need the rows it did not send.
    """

    problems = []
    for key, account in (record.get("accounts") or {}).items():
        if not isinstance(account, dict):
            continue
        columns = account.get("item_columns") or []
        declared = account.get("item_count")
        totals = account.get("totals") or {}
        rankings = [r for r in (account.get("rankings") or []) if isinstance(r, dict)]

        wanted = TOP_ROW_CRITERIA.get(account.get("category") or "", ())
        got_by = [r.get("sorted_by") for r in rankings]
        missing = [c for c in wanted if c not in got_by]
        if missing:
            problems.append(
                f"{key}: missing ranking(s) for {', '.join(missing)} — expected "
                f"{len(wanted)}, got {len(rankings)}"
            )
        for column in got_by:
            if column not in columns:
                problems.append(
                    f"{key}: sorted_by {column!r} is not one of item_columns"
                )

        # Same counterparty, two lists, two sets of figures — one of them is a
        # transcription the model invented on the second pass over the sheet.
        label_at = _label_positions(columns)
        seen: dict[str, tuple[Any, ...]] = {}
        for ranking in rankings:
            for row in ranking.get("items") or []:
                name = "|".join(str(row[i]) for i in label_at if i < len(row))
                if not name:
                    continue
                figures = tuple(row)
                if name in seen and seen[name] != figures:
                    problems.append(
                        f"{key}: rows for {name!r} disagree between rankings"
                    )
                seen.setdefault(name, figures)

        for ranking in rankings:
            column = ranking.get("sorted_by")
            rows = ranking.get("items") or []
            label = f"{key}[{column}]"

            if column in columns:
                at = columns.index(column)
                values = [_row_value(row, at) for row in rows]
                if any(a < b for a, b in zip(values, values[1:])):
                    problems.append(
                        f"{label}: rows are not sorted by {column} — "
                        f"{[f'{v:,.0f}' for v in values[:6]]}"
                    )

            if isinstance(declared, int) and len(rows) < min(declared, TOP_ROWS):
                problems.append(
                    f"{label}: sheet has {declared} rows but only {len(rows)} "
                    "came back"
                )

            for index, name in enumerate(columns):
                cap = totals.get(name)
                if not isinstance(cap, (int, float)) or isinstance(cap, bool):
                    continue
                got = sum(
                    row[index] for row in rows
                    if index < len(row)
                    and isinstance(row[index], (int, float))
                    and not isinstance(row[index], bool)
                )
                # Rounding in the printed total is normal; a slice genuinely
                # bigger than its account is not.
                if abs(got) > abs(cap) + 1:
                    problems.append(
                        f"{label}.{name}: rows sum to {got:,.0f}, above the "
                        f"account total of {cap:,.0f}"
                    )

    if problems:
        record["extraction_notes"].append(
            "WARNING: the top-row selection does not hold together — "
            + "; ".join(problems)
            + ". Either the model kept the wrong rows, or its answer was cut "
            "short (raise the pass's max_tokens_env, e.g. LLM_LEDGER_MAX_TOKENS; "
            "run testing/probe_max_tokens.py for the model's real ceiling)."
        )


def extract_ledger_batch(
    chain: Any,
    documents: list[tuple[str, str, str]],
) -> list[tuple[dict[str, Any] | None, str]]:
    """Read every ledger file in one call, then share one record between them.

    Batch rather than per-document because the record spans files: the same
    account can arrive split across two of them, and one call over the whole set
    also keeps the naming consistent. Each document receives THE SAME object —
    ``_build_ledger_structured_block`` de-duplicates by identity, so handing out
    copies would render the block once per file.
    """

    sheets, read_notes = read_ledger_sheets(documents)
    if not sheets:
        reason = "; ".join(read_notes) or "No sheet could be read"
        return [(None, reason[:500]) for _ in documents]

    payload, dropped = _json_payload(sheets)
    record, error = run_extraction(
        chain,
        ", ".join(filename for filename, _, _ in documents),
        payload,
        REQUIRED_TOP_LEVEL_KEYS,
        "No ledger extraction LLM configured.",
    )
    if record is None:
        return [(None, error) for _ in documents]
    # The model has put these two inside "accounts" instead of beside it, where
    # they then read as two more accounts. Lifted rather than dropped: the
    # content is right, only the nesting is wrong, and a note about an
    # unreadable sheet is worth more than a tidy shape.
    for key in ("unmapped_columns", "extraction_notes"):
        misplaced = (record.get("accounts") or {}).pop(key, None)
        if isinstance(misplaced, list) and not record.get(key):
            record[key] = misplaced
    record.setdefault("unmapped_columns", [])
    record.setdefault("extraction_notes", [])
    record["extraction_notes"] += read_notes
    if dropped:
        record["extraction_notes"].append(
            f"Input exceeded {PAYLOAD_CHAR_BUDGET:,} characters, so {len(dropped)} "
            f"sheet(s) were not sent: {', '.join(dropped[:10])}."
        )

    # Before _drop_total_rows, which rewrites item_count and would erase the
    # evidence — item_count now means the sheet's own row count, so dropping a
    # printed total row must not touch it.
    _note_slice_problems(record)
    _drop_total_rows(record)

    # The model has dropped whole sheets in silence — on the sample workbook it
    # returned three accounts out of six and left extraction_notes empty, so the
    # record read as complete while half the ledger was gone. Nothing here can
    # tell WHICH sheet went missing, but it can refuse to let the count pass
    # unremarked: a short record that says so is recoverable, one that does not
    # is a credit opinion written on half a ledger.
    expected = len(sheet_inventory(documents))
    returned = len(record.get("accounts") or {})
    if returned < expected:
        record["extraction_notes"].append(
            f"WARNING: the input held {expected} sheets but only {returned} accounts "
            f"came back. Absent a TRUNCATED warning above, the model skipped sheets "
            f"— the figures below are NOT the whole ledger the customer "
            f"submitted."
        )
    return [(record, "") for _ in documents]
