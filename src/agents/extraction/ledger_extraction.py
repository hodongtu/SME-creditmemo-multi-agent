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
# The label columns that must read as text. counterparty_code is left out: plenty
# of accounting packages number their customers, and a code of digits is correct.
NAME_KEYS = ("counterparty_name", "item_name")
# Hyphen, en dash, em dash: how a ledger prints an empty cell.
DASHES = {"-", "–", "—"}
# A group subtotal ("Cộng nhóm") has to be told from a counterparty whose name
# opens with "Công ty", and the two are the SAME STRING once normalize_text
# strips the accents — both become "cong". So this one comparison keeps its
# accents and does its own lowercasing.
TOTAL_ROW_PREFIXES = {"tổng", "cộng", "total", "sum"}

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

YOU DO NOT COPY FIGURES. A program reads them off the sheet itself. Your job is
to say WHAT each sheet is and WHICH PRINTED COLUMN holds which field; it does the
arithmetic and the ranking from there. So never add anything up, and never write
a number that is not a date or a column letter.

RETURN ONE ENTRY FOR EVERY SHEET. Work through the whole array; do not stop early
and do not merge two sheets unless they are the same account AND the same period.
A sheet you cannot place still has to be named in "extraction_notes" — dropping
one in silence is the worst failure here, because the record still looks complete
while half the ledger is gone.

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

Example: "131@20190101-20191231". ALWAYS carries the period, even when the whole
dossier holds only one.

    Full year 2019        -> 131@20190101-20191231
    Year 07/18-06/19      -> 131@20180701-20190630   (any period, not just years)

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

════ EACH ENTRY IN "accounts" — EXACTLY 5 KEYS ════
"category"       one of: cash receivable other_receivable inventory
                 fixed_asset payable other_payable borrowing equity unknown
                 Prefer "unknown" over a guess: an analyst can read an unlabelled
                 sheet, but will TRUST a wrong label.
"code_source"    "printed" or "convention", per the rule above.
"source_sheet_name"
                 the sheet names that fed this entry, EXACTLY as printed on the
                 tab — "P.TRA KHAC", "TK VAY", "NXT". A list, because several
                 sheets of one account and one period merge into a single entry.
                 Write [] for a .csv, which has no sheet. THE PROGRAM READS THE
                 FIGURES OFF THE SHEET YOU NAME HERE, so a wrong name here puts
                 another account's money under this one.
"period"         {{"from","to"}} — see below.
"columns"        {{canonical field: column letter}} — the heart of your answer.
                 The first line of each sheet is its Excel column letters. Say
                 which letter each canonical field is printed in:
                     {{"counterparty_name": "C", "closing_debit": "H"}}
                 A letter, in quotes. Not the heading text, not a number, not a
                 range. Map only the columns this sheet actually prints; leave a
                 field out rather than guess at it.
                 Headings can stack two rows deep, so the same word may head four
                 different columns — read DOWN from the letter to the figures
                 below it to be sure which one you are naming.

════ "period" ════
"from"/"to"    ISO "YYYY-MM-DD", read from the banner line above the table.

Common phrasings and how they expand:
    "Từ ngày 01/01/2019 đến ngày 31/12/2019" -> 2019-01-01 / 2019-12-31
    "Kỳ báo cáo: 01/01/2019 - 31/12/2019"    -> 2019-01-01 / 2019-12-31
    "Năm 2018"                               -> 2018-01-01 / 2018-12-31
    "Quý 4/2018"                             -> 2018-10-01 / 2018-12-31
    "Tháng 12 năm 2018"                      -> 2018-12-01 / 2018-12-31
If no line states a period, leave both "" — do NOT infer one from a file name
that merely contains a year.

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
"Nhập vào" + "Giá trị" -> inflow_value. A stock sheet read correctly comes back
with opening_quantity, opening_value, inflow_quantity … each against its own
letter. Two header rows are a normal layout, not a reason to give up on a sheet.

════ WORKED EXAMPLE (one debt entry, one stock entry) ════
The letters below stand for a sheet whose first line reads "A B C D E F G H I".
Yours will differ — read them off the sheet in front of you.
{{
  "accounts": {{
    "131@20190101-20191231": {{
      "category": "receivable",
      "code_source": "convention",
      "source_sheet_name": ["TK_131"],
      "period": {{"from": "2019-01-01", "to": "2019-12-31"}},
      "columns": {{
        "counterparty_code": "B",
        "counterparty_name": "C",
        "opening_debit": "D",
        "opening_credit": "E",
        "debit_movement": "F",
        "credit_movement": "G",
        "closing_debit": "H",
        "closing_credit": "I"
      }}
    }},
    "156@20190101-20191231": {{
      "category": "inventory",
      "code_source": "convention",
      "source_sheet_name": ["NXT"],
      "period": {{"from": "2019-01-01", "to": "2019-12-31"}},
      "columns": {{
        "item_name": "A",
        "opening_quantity": "B",
        "opening_value": "C",
        "inflow_quantity": "D",
        "inflow_value": "E",
        "outflow_quantity": "F",
        "outflow_value": "G",
        "closing_quantity": "H",
        "closing_value": "I"
      }}
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


TRUNCATION_MARK = "... truncated after"


def _parse_cell(cell: str) -> float | None:
    """One printed cell as a number, or None where the cell holds no figure.

    A ledger mixes both spellings inside one row: sheet PTHU_KHAC prints
    "126.499.849.381" next to a bare "36961296987". Dots are thousands
    separators here, never a decimal point — Vietnamese accounting software
    writes whole đồng.

    A dash is the printed form of NOTHING, not of zero, and is kept apart from
    it: a column of dashes must not read as a column that was summed and came to
    zero. That distinction is what lets the caller tell "no figure on this row"
    from "this row is genuinely nil".
    """

    text = (cell or "").strip().replace(" ", "")
    if not text or text in DASHES:
        return None
    bracketed = text.startswith("(") and text.endswith(")")
    if bracketed:
        text = text[1:-1]
    if not re.fullmatch(r"-?\d{1,3}(\.\d{3})+|-?\d+", text):
        return None
    value = float(text.replace(".", ""))
    return -value if bracketed else value


def _sheet_grid(sheet: dict[str, str]) -> tuple[list[str], list[list[str]], bool]:
    """A sheet's column letters, its remaining rows, and whether it was cut short.

    The letters are the first line of the TSV, put there by ``_grid_to_tsv`` and
    kept in step by ``_without_row_counter`` when it drops an "Stt" column. They
    are the addressing scheme the model maps onto, and the only unambiguous one:
    sheet NXT stacks its headings two deep, so the text "Số lượng" names four
    different columns while the letter names exactly one.
    """

    lines = sheet.get("content", "").splitlines()
    truncated = any(line.lstrip().startswith(TRUNCATION_MARK) for line in lines)
    rows = [line.split("\t") for line in lines
            if not line.lstrip().startswith(TRUNCATION_MARK)]
    if not rows:
        return [], [], truncated
    return [c.strip() for c in rows[0]], rows[1:], truncated


def _detail_rows(
    rows: list[list[str]],
    order: list[str],
    at: dict[str, int],
    label_fields: list[str],
) -> tuple[list[list[Any]], list[float | None], list[str]]:
    """Split a sheet into its detail rows, its printed total row, and suspects.

    A row is a detail row when it names something, is not the printed total, and
    carries at least one figure in a mapped column. Banner and heading rows fall
    out on their own: they hold text but no figures in a money column, so nothing
    has to know what a banner looks like.

    The total row is recognised by an EXACT label match, never by a prefix. One
    of this dossier's own counterparties is "Công ty Cổ phần cộng đồng xe tải
    Việt Nam" — a prefix rule would delete the largest position in the account
    and quietly shrink its total. Rows that merely START with a total word are
    returned as suspects instead, for the caller to name rather than drop.
    """

    money = [f for f in order if f not in label_fields]
    detail: list[list[Any]] = []
    printed: list[float | None] = []
    suspects: list[str] = []
    for row in rows:
        cell = {f: (row[at[f]] if at[f] < len(row) else "") for f in order}
        figures = {f: _parse_cell(cell[f]) for f in money}
        if all(v is None for v in figures.values()):
            continue
        names = [cell[f].strip() for f in label_fields if cell[f].strip()]
        marks = [normalize_text(n) for n in names]
        if any(m in TOTAL_ROW_NAMES for m in marks):
            if not printed:
                printed = [figures[f] for f in money]
            continue
        if not names:
            continue
        if any(n.lower().split()[0] in TOTAL_ROW_PREFIXES for n in names):
            suspects.append(names[-1])
        # A dash or an empty cell prints a nil balance, and the ledger's own
        # total row counts it as one. Writing null here instead would put holes
        # through the report that read as "not available" rather than "zero".
        detail.append([
            cell[f].strip() if f in label_fields else (figures[f] or 0)
            for f in order
        ])
    return detail, printed, suspects


def build_account_rows(record: dict[str, Any], sheets: list[dict[str, str]]) -> None:
    """Fill each account's figures from the sheet, instead of asking the model.

    The model reports which printed column is which canonical field; the
    arithmetic is done here. That split follows what each side can actually be
    held to. Adding 143 rows and returning 5 of them is a claim nothing can
    check, and both ways it fails were seen in production: with no printed
    "Tổng cộng" row the model would not add the rows up at all, and with two
    periods in one call it filled one period's totals from the other file's
    sheet. Naming the column a heading sits in is a claim the printed total row
    can be checked against, column by column.

    The account record keeps the shape the report already reads — "totals",
    "item_count", "item_columns", "rankings" — so nothing downstream changes.
    Only the author does.
    """

    by_name: dict[str, list[dict[str, str]]] = {}
    for sheet in sheets:
        by_name.setdefault(
            normalize_text(sheet.get("sheet_name") or sheet.get("filename", "")), []
        ).append(sheet)
    lone = sheets[0] if len(sheets) == 1 else None

    problems: list[str] = []
    for key, account in (record.get("accounts") or {}).items():
        if not isinstance(account, dict):
            continue
        columns = account.pop("columns", None)
        if not isinstance(columns, dict) or not columns:
            problems.append(f"{key}: no column map came back, so no figure could be read")
            account.update(totals={}, item_count=0, item_columns=[], rankings=[])
            continue

        named = account.get("source_sheet_name") or []
        if isinstance(named, str):
            named = [named]
        found = [s for n in named for s in by_name.get(normalize_text(str(n)), [])]
        # A .csv names no sheet. Only when the account named none at all — an
        # account naming a sheet that matched nothing is a mis-filing, and
        # handing it the one sheet present would read figures off the wrong page.
        if not found and not named and lone is not None:
            found = [lone]
        if not found:
            problems.append(
                f"{key}: sheet {named or '(none named)'} matched nothing that was "
                "read, so no figure could be taken from it"
            )
            account.update(totals={}, item_count=0, item_columns=[], rankings=[])
            continue

        order = list(columns)
        label_fields = [f for f in order if f in LABEL_KEYS]
        # Two fields on one letter is the one wrong map arithmetic can see. A
        # straight SWAP between two money columns cannot be caught here and is
        # not worth pretending about: both sides of the reconciliation below
        # would read the same wrong column and agree with each other. Telling
        # "Phát sinh nợ" from "Dư nợ cuối kỳ" is reading, and reading is the
        # model's half of this job.
        shared: dict[str, list[str]] = {}
        for field in order:
            shared.setdefault(str(columns[field]).strip().upper(), []).append(field)
        for letter, fields in shared.items():
            if len(fields) > 1:
                problems.append(
                    f"{key}: {' and '.join(fields)} are all mapped to column "
                    f"{letter} — at most one field can be printed in one column"
                )
        money = [f for f in order if f not in label_fields]
        detail: list[list[Any]] = []
        printed: list[float | None] = []
        cut = False
        for sheet in found:
            letters, rows, truncated = _sheet_grid(sheet)
            cut = cut or truncated
            index = {letter: position for position, letter in enumerate(letters)}
            at = {f: index.get(str(columns[f]).strip().upper(), -1) for f in order}
            missing = [f for f in order if at[f] < 0]
            if missing:
                problems.append(
                    f"{key}: column letter(s) "
                    + ", ".join(f"{f}={columns[f]!r}" for f in missing)
                    + f" are not in sheet {sheet.get('sheet_name') or sheet.get('filename')}"
                )
                continue
            got, total_row, suspects = _detail_rows(rows, order, at, label_fields)
            # A money field pointed at a column of names, or a name field pointed
            # at a column of figures, shows up as soon as anything is read. Worth
            # catching early because of what the second one does downstream: with
            # the name field on a column of digits, the printed "Tổng cộng" row no
            # longer looks like a total row, joins the detail rows, and doubles
            # every figure in the account.
            #
            # Only NAME fields are held to this. A counterparty_code is often all
            # digits, and grading it the same way would report a correct map.
            # Measured against the column's filled cells, not against the rows
            # kept: a bad map inflates the second number in step with the first,
            # so the test disarms itself just as it is needed.
            for field in order:
                cells = [r[at[field]].strip() for r in rows if at[field] < len(r)]
                # A dash is the printed form of nothing, so it says nothing about
                # whether this column holds names or figures. Counting it as
                # content puts it on the text side of the scale: TK_131's "Dư nợ
                # cuối kỳ" is two figures and two dashes, which is enough to read
                # as a column of words.
                filled = [c for c in cells if c and c not in DASHES]
                numeric = sum(1 for c in filled if _parse_cell(c) is not None)
                if field in NAME_KEYS and numeric * 2 > len(filled):
                    problems.append(
                        f"{key}: {field} is mapped to column {columns[field]}, which "
                        "holds figures rather than names"
                    )
                elif field not in label_fields and got and not numeric:
                    problems.append(
                        f"{key}: {field} is mapped to column {columns[field]}, which "
                        "holds no figure at all"
                    )
            detail += got
            if total_row and not printed:
                printed = total_row
            for name in suspects:
                problems.append(
                    f"{key}: row {name!r} reads like a subtotal but is counted as a "
                    "detail row — if it is a group total its figures are counted twice"
                )

        account["item_columns"] = order
        account["item_count"] = len(detail)

        if cut:
            # The reader stops at max_rows_per_sheet and says so. Summing what
            # survived gives a total that is short by an unknown amount and looks
            # exactly like a correct one, so the printed row is the only figure
            # worth having here.
            account["totals"] = {
                f: printed[i] for i, f in enumerate(money)
                if i < len(printed) and printed[i] is not None
            }
            problems.append(
                f"{key}: the sheet was cut at the reader's row limit, so the rows "
                "here are not all of them — totals are the printed total row only"
                + ("" if printed else ", and the sheet printed none, so they are empty")
            )
        else:
            account["totals"] = {
                f: round(sum(row[order.index(f)] for row in detail
                             if isinstance(row[order.index(f)], (int, float))))
                for f in money
            }
            if printed:
                for i, field in enumerate(money):
                    if i >= len(printed) or printed[i] is None:
                        continue
                    got = account["totals"][field]
                    if abs(printed[i] - got) > 1:
                        problems.append(
                            f"{key}.{field}: the sheet prints {printed[i]:,.0f} but its "
                            f"rows add up to {got:,.0f} — one of them is not this "
                            "column, so check the column letter"
                        )
            else:
                problems.append(
                    f"{key}: the sheet prints no total row, so the column map behind "
                    "these totals was never checked against one"
                )

        rankings = []
        for criterion in TOP_ROW_CRITERIA.get(account.get("category") or "", ()):
            if criterion not in order:
                continue
            position = order.index(criterion)
            rankings.append({
                "sorted_by": criterion,
                "items": sorted(detail, key=lambda r: _row_value(r, position),
                                reverse=True)[:TOP_ROWS],
            })
        account["rankings"] = rankings

    if problems:
        record.setdefault("extraction_notes", []).append(
            "WARNING: reading the sheets turned up problems — "
            + "; ".join(problems[:10])
            + "."
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

    build_account_rows(record, sheets)
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
