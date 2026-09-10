"""Detail-ledger spreadsheets (sổ chi tiết) into one JSON keyed by account."""

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

LEDGER_BLOCK_CHAR_BUDGET = 40_000
RESIDUAL_LABEL = "Các đối tác còn lại"
LABEL_KEYS = ("counterparty_name", "counterparty_code", "item_name")

def _label_positions(columns: list[str]) -> list[int]:
    """Indexes of the text columns, in the order they appear."""

    return [i for i, c in enumerate(columns) if c in LABEL_KEYS]

TOTAL_ROW_NAMES = {"tong", "tong cong", "cong", "total", "sum"}
TOP_ROWS = 5
TOP_ROW_CRITERIA: dict[str, tuple[str, ...]] = {
    "receivable": ("debit_movement", "closing_debit", "closing_credit"),
    "payable": ("credit_movement", "closing_debit", "closing_credit"),
    "inventory": ("outflow_value", "closing_value"),
    "borrowing": ("closing_credit",),
    "other_payable": ("closing_credit",),
    "other_receivable": ("closing_debit",),
    "cash": ("closing_debit",),
    "fixed_asset": ("closing_debit",),
    "equity": ("closing_credit",),
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
    Year 07/24-06/25      -> 131@20240701-20250630   (any period, not just years)

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
   value and take that one.

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

════ EACH ENTRY IN "accounts" — EXACTLY 8 KEYS ════
"category"       one of: cash receivable other_receivable inventory
                 fixed_asset payable other_payable borrowing equity unknown
                 Prefer "unknown" over a guess: an analyst can read an unlabelled
                 sheet, but will TRUST a wrong label.
"code_source"    "printed" or "convention", per the rule above.
"source_sheet_name"
                 the sheet names that fed this entry, EXACTLY as printed on the
                 tab — "P.TRA KHAC", "TK VAY", "NXT". A list, because several
                 sheets of one account and one period merge into a single entry.
                 Write [] for a .csv, which has no sheet.
"period"         {{"from","to"}} — see below.
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

Common phrasings and how they expand:
    "Từ ngày 01/01/2025 đến ngày 31/12/2025" -> 2025-01-01 / 2025-12-31
    "Kỳ báo cáo: 01/01/2025 - 31/12/2025"    -> 2025-01-01 / 2025-12-31
    "Năm 2024"                               -> 2024-01-01 / 2024-12-31
    "Quý 4/2024"                             -> 2024-10-01 / 2024-12-31
    "Tháng 12 năm 2024"                      -> 2024-12-01 / 2024-12-31
If no line states a period, leave both "" — do NOT infer one from a file name
that merely contains a year.

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
"Nhập vào" + "Giá trị" -> inflow_value. The joined result shows up as the field
name in "item_columns" — a stock sheet whose headers were read correctly ends up
with opening_quantity, opening_value, inflow_quantity … in there. Two header rows
are a normal layout, not a reason to give up on the sheet.

════ WORKED EXAMPLE (one debt entry, one stock entry) ════
{{
  "accounts": {{
    "131@20250101-20251231": {{
      "category": "receivable",
      "code_source": "convention",
      "source_sheet_name": ["TK_131"],
      "period": {{
        "from": "2025-01-01",
        "to": "2025-12-31"
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
      "source_sheet_name": ["NXT"],
      "period": {{
        "from": "2025-01-01",
        "to": "2025-12-31"
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
    """The record as the agent sees it: framed by indent, one detail row per line."""

    def encode(value: Any, depth: int) -> str:
        pad = " " * (indent * depth)
        inner = pad + " " * indent
        if isinstance(value, list) and value and all(isinstance(v, list) for v in value):
            rows = ",\n".join(
                inner + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in value
            )
            return f"[\n{rows}\n{pad}]"

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
    """One row summing every column of the rows it stands in for."""

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
    """The record trimmed to ``budget`` characters, losing no figures."""

    def shrink(keep: int) -> dict[str, Any]:
        accounts = {}
        for key, account in (record.get("accounts") or {}).items():
            columns = account.get("item_columns") or []
            label_at = set(_label_positions(columns))

            rankings = []
            for ranking in account.get("rankings") or []:
                rows = ranking.get("items") or []
                if len(rows) <= keep:
                    rankings.append(ranking)
                    continue

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


def fill_source_files(record: dict[str, Any], documents) -> None:
    """Put back the per-account ``source_files`` the model no longer writes.

    The model used to name the file on every account, and on a one-file dossier
    that was the same string seven times. It is derivable instead: the reader
    already knows which file each sheet came from, and the account names its
    sheet. So the model writes the part only it knows, and the program fills in
    the part it can look up.

    A sheet name that matches nothing leaves the list EMPTY and says so. The
    model does get sheet names wrong — one live run filed a "P.THU KHAC" account
    under sheet "P.TRA KHAC" — and guessing a filename from a name that matched
    nothing would dress that mistake up as provenance.
    """

    by_sheet: dict[str, list[str]] = {}
    for entry in sheet_inventory(documents):
        filename, _, sheet = entry.partition(" › ")
        by_sheet.setdefault(normalize_text(sheet or filename), []).append(filename)

    unmatched = []
    for key, account in (record.get("accounts") or {}).items():
        if not isinstance(account, dict):
            continue
        sheets = account.get("source_sheet_name") or []
        if isinstance(sheets, str):
            sheets = [sheets]
        files: list[str] = []
        for sheet in sheets:
            for name in by_sheet.get(normalize_text(str(sheet)), []):
                if name not in files:
                    files.append(name)
            if normalize_text(str(sheet)) not in by_sheet:
                unmatched.append(f"{key} → {sheet!r}")
        # A .csv names no sheet at all; on a one-file dossier that is unambiguous.
        # Only when the account named NO sheet, never when it named one that
        # matched nothing — falling back there would hand a filename to exactly
        # the case the warning exists to flag.
        all_files = {n for names in by_sheet.values() for n in names}
        if not sheets and len(all_files) == 1:
            files = sorted(all_files)
        account["source_files"] = files
    if unmatched:
        record.setdefault("extraction_notes", []).append(
            "WARNING: source_sheet_name did not match any sheet that was read, so "
            "the source file could not be filled in for: "
            + "; ".join(unmatched[:6])
            + ". The sheet name is the model's, and a wrong one here means the "
            "account may have been read off a different sheet than it claims."
        )


def extract_ledger_batch(
    chain: Any,
    documents: list[tuple[str, str, str]],
) -> list[tuple[dict[str, Any] | None, str]]:
    """Read every ledger file in one call, then share one record between them."""

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

    _note_slice_problems(record)
    _drop_total_rows(record)
    fill_source_files(record, documents)

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
