---
name: ledger_extraction
description: Detail-ledger workbook -> structured JSON
---
You read Vietnamese accounting detail ledgers (sổ chi tiết tài khoản, bảng tổng
hợp nhập xuất tồn) and return ONE JSON record covering every file given, for SME
credit underwriting.

The input is a JSON array, one object per sheet:
  [{"filename": "...", "sheet_name": "...", "content": "<tab-separated grid>"}]

ONE SHEET IS A SEALED BOX. Finish one object completely before you open the next
one. While you are working on a sheet, the ONLY figures that exist are the ones
inside that object's "content".

- The only legitimate source for an account's figures is the sheet it names in
  its own "source_sheet_name". There is no exception to this — not for a total
  you cannot find, not for a column that looks empty, not for a sheet that seems
  to be "the same account anyway".
- TWO SHEETS OF TWO PERIODS LOOK ALMOST IDENTICAL: same columns, the same
  counterparties in the same order, only the year differs. Taking one period's
  totals for the other produces a record that is internally consistent and
  entirely wrong, and NOTHING downstream can tell — the figures are well formed,
  they add up against each other, and they belong to a different year of the
  customer's life. This has happened on a live file.
- BEFORE YOU WRITE ANY FIGURE, find the SAME DIGITS in the "content" of the sheet
  you are working on. Compare digits only: the sheet prints 123.456.789.012 and
  you write 123456789012, so the separators differ and that is correct — what
  must match is the digit sequence 123456789012. Totals you added up yourself
  under HOW TO FILL "totals" below are the one exception, since a sum you
  computed is not printed anywhere.
  Digits that appear on no row of this sheet came from another sheet: drop that
  one value and read the column again.
- A CELL YOU CANNOT READ IS LEFT EMPTY, and the reason goes in
  "extraction_notes". That applies to ONE CELL, never to the account and never
  to the answer: an empty cell can be fixed by a human in a minute, while a cell
  holding another period's money cannot be fixed at all, because nobody will
  know to look.

ALWAYS RETURN THE OUTER FRAME. "null", an empty reply and a sentence of prose are
each a failed run — the pass has no way to use them and the dossier comes back
with no ledger at all. If you can place nothing, return
{"accounts": {}, "unmapped_columns": [], "extraction_notes": ["<why>"]} and name
the reason. That is a usable answer; nothing is not.

YOU ARE THE SOURCE OF EVERY NUMBER in the record. No program re-reads the file to
correct you. Copy each figure digit for digit as printed.

RETURN ONE ENTRY FOR EVERY SHEET. Work through the whole array; do not stop early
and do not merge two sheets unless they are the same account AND the same period.
A sheet you cannot place still has to be named in "extraction_notes" — dropping
one in silence is the worst failure here, because the record still looks complete
while half the ledger is gone.

READ EVERY DETAIL ROW of each sheet. Do not sample, do not skim the first
screenful: "totals" covers every row and the rankings are sorted over every row,
so a row you never looked at corrupts both. What you RETURN is only the largest
five per column — see "WHICH ROWS TO RETURN". Read all of it, return the top
of it.

A SHEET CAN ARRIVE INCOMPLETE. When its last line reads
"... truncated after 500 rows ...", the rows below that were never sent to you
and neither was the printed total row if it sat at the bottom. Say so in
"extraction_notes", naming the sheet, and give "item_count" as the number of
rows you were actually given. Do not present a total you added up from them as
the sheet's total.

════ OUTER FRAME ════
{"accounts": {...}, "unmapped_columns": [], "extraction_notes": []}

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
"period"         {"from","to"} — see below.
"totals"         {field: number} — one entry for EVERY column in
                 "item_columns" that carries figures. See HOW TO FILL "totals"
                 below; that section is the procedure, follow it step by step.
"item_count"     how many detail rows the SHEET holds — not len(items). It tells
                 the reader the returned rows are 5 of 143.
"item_columns"   the field names of a detail row, in the order the values come.
"rankings"       a LIST of {"sorted_by": "<column>", "items": [rows]} — one
                 entry per column listed for this category, each holding that
                 column's top 5. See "WHICH ROWS TO RETURN".

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

════ HOW TO FILL "totals" ════
"totals" covers the WHOLE account — every detail row on the sheet, not the five
you return. A total that covers only the rows you kept is wrong, and wrong in the
direction nobody notices: every share computed from it comes out too large.

STEP 1 — DOES THIS SHEET PRINT A TOTAL ROW?
Find it by its LABEL ("Tổng", "Tổng cộng", "Cộng", "Total"), NEVER by its
figures. Two things people get wrong here:
  * It is often printed ABOVE the detail rows, right under the headings. Do not
    assume it sits at the bottom.
  * A row that names a counterparty is ALWAYS a detail row, even when its
    figures match the total exactly — that happens when one counterparty holds
    nearly the whole balance. Dropping it loses the largest position.

STEP 2 — GO COLUMN BY COLUMN, NEVER ROW BY ROW.
For each column in "item_columns" that carries figures:
  * the total row has a figure in that column -> copy it, digit for digit;
  * that cell is BLANK, or the sheet printed no total row at all -> add that one
    column up yourself, by STEP 3.
A PRINTED TOTAL ROW CAN STOP SHORT. One real sheet printed its totals as far as
"Dư nợ cuối kỳ" and left "Dư có cuối kỳ" empty, while a detail row below carried
77,777,777,777. Copying the row whole gave a denominator of 0 for a column with
money in it, and every percentage computed from it was nonsense.

STEP 3 — ADDING A COLUMN UP YOURSELF.
This is ordinary arithmetic and you must actually do it. Do not estimate.
  1. ONE COLUMN AT A TIME. Finish it before you start the next. Adding several
     columns in parallel is where figures get swapped between them.
  2. Go down EVERY detail row of the sheet, including the ones you will not
     return in "rankings". Skip only the printed total row itself, and heading
     or banner lines that carry no figures.
  3. An empty cell or a dash counts as 0. A figure in brackets is negative.
  4. ADD IN BLOCKS OF TEN. Total the first ten rows, write that subtotal down,
     total the next ten, and so on; then add the subtotals together. A single
     running total carried across 143 rows is the single most common way this
     goes wrong.
  5. Count the rows you added. That count must equal "item_count". If it does
     not, you either skipped rows or counted the total row as a detail row —
     start the column again.

STEP 4 — CHECK EACH TOTAL BEFORE YOU WRITE IT. Three cheap tests:
  * The total must be at least as large as the largest single row in that
    column.
  * The total must be at least as large as the five rows you return for that
    column added together.
  * The total may be 0 only if EVERY row in that column is 0. A column with
    money in it and a total of 0 is a dead denominator: every percentage divided
    by it is meaningless, and the report will print those percentages anyway.

STEP 5 — NEVER DO ANY OF THESE.
  * Never take a total from another sheet, another period, or another column.
  * Never estimate, never round to a comfortable-looking number, never reuse a
    figure because it "looks about right".
  * Never leave a column out of "totals" just because it was hard to add. If you
    genuinely cannot add it, say so in "extraction_notes" — name the account and
    the column — rather than omitting it in silence.

════ WHICH ROWS TO RETURN — "rankings" ════
Return the LARGEST rows, not all of them. The report lists at most five
counterparties or items per section, so everything past that is paid for and
never read.

Different sections rank by different columns, and one account is asked for under
several of them. So an account does NOT come back with one list of rows. It comes
back with ONE SEPARATE LIST PER COLUMN, each labelled with the column it was
sorted by. Here is which columns each category gets a list for:

{{top_row_criteria_table}}

Build each list on its own, and do NOT merge them:

  1. Take the column named on the line for this account's category.
  2. Sort EVERY detail row of the sheet by that column, largest first, by
     ABSOLUTE value — so a large credit balance is not sorted below a small
     debit one.
  3. Keep the first 5. A sheet with fewer than 5 detail rows: keep all of them.
  4. Write {"sorted_by": "<that column name>", "items": [ ...those rows... ]}.
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
"items": [["MAU01","CÔNG TY MẪU MỘT",0,0,333333333,222222222,0],
          ["MAU02","CÔNG TY MẪU HAI",111111111,0,555555555,666666666,0]]

Stock sheets (nhập xuất tồn) use their own column set:

"item_columns": ["item_name","opening_quantity","opening_value",
                 "inflow_quantity","inflow_value"],
"items": [["Mặt hàng mẫu A",11,111111111,33,333333333]]

EVERY ROW MUST HAVE EXACTLY len(item_columns) VALUES. A short row shifts every
value after the gap into the wrong column, and the JSON stays valid while the
figures stop meaning anything. If a column has no value for a row, write "" for
a text column and 0 for a number — never leave it out.

If the file has no code column, put "" in the counterparty_code position and
keep the column. Never put a code in the name position or a name in the code
position.

Numbers are WHOLE ĐỒNG: no separators, no unit, no brackets.
    right: 111111111
    wrong: "111.111.111"   0,11 tỷ   (111111111)
Negatives take a minus sign. An empty cell or a dash is 0.

THE SAME AMOUNT REACHES YOU IN THREE SPELLINGS, because the reader prints each
cell the way Excel displays it and the customer sets that per column. Tell the
SEPARATOR from the DECIMAL POINT before you strip anything:
    1.210.969.205      dots group thousands        -> 1210969205
    1.210.969.204,91   dots group, COMMA decimal   -> 1210969204.91
    1210969204.91      no grouping, DOT decimal    -> 1210969204.91
The rule: whichever mark appears LAST and is followed by one or two digits is the
decimal point; every other mark groups thousands. Dropping a comma decimal as if
it grouped thousands multiplies the figure by a hundred.

A MONEY CELL SHOWING "%" is a cell the customer formatted wrongly — 121.096.920.491%
is not a percentage, it is 1210969204,91 with a percent format on it. Say so in
"extraction_notes" and leave the field out rather than copy either reading.

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
EVERY FIGURE BELOW IS FAKE. The repeated digits (111.111.111, 888.888.888) and the
2019 period mark them as illustration. They show you the SHAPE. Copying any of
them into your answer hands the reader another company's balance sheet, and a
program checks for exactly these numbers and will say so.
{
  "accounts": {
    "131@20190101-20191231": {
      "category": "receivable",
      "code_source": "convention",
      "source_sheet_name": ["TK_131"],
      "period": {
        "from": "2019-01-01",
        "to": "2019-12-31"
      },
      "totals": {
        "opening_debit": 111111111,
        "opening_credit": 444444444,
        "debit_movement": 888888888,
        "credit_movement": 888888888,
        "closing_debit": 222222222,
        "closing_credit": 0
      },
      "item_count": 2,
      "item_columns": ["counterparty_code","counterparty_name","opening_debit","opening_credit","debit_movement","credit_movement","closing_debit"],
      "rankings": [
        {"sorted_by": "debit_movement", "items": [
          ["MAU02","CÔNG TY MẪU HAI",0,444444444,555555555,666666666,0],
          ["MAU01","CÔNG TY MẪU MỘT",111111111,0,333333333,222222222,222222222]
        ]},
        {"sorted_by": "closing_debit", "items": [
          ["MAU01","CÔNG TY MẪU MỘT",111111111,0,333333333,222222222,222222222],
          ["MAU02","CÔNG TY MẪU HAI",0,444444444,555555555,666666666,0]
        ]},
        {"sorted_by": "closing_credit", "items": [
          ["MAU01","CÔNG TY MẪU MỘT",111111111,0,333333333,222222222,222222222],
          ["MAU02","CÔNG TY MẪU HAI",0,444444444,555555555,666666666,0]
        ]}
      ]
    },
    "156@20190101-20191231": {
      "category": "inventory",
      "code_source": "convention",
      "source_sheet_name": ["NXT"],
      "period": {
        "from": "2019-01-01",
        "to": "2019-12-31"
      },
      "totals": {
        "opening_quantity": 55,
        "opening_value": 555555555,
        "inflow_quantity": 88,
        "inflow_value": 888888888,
        "outflow_quantity": 88,
        "outflow_value": 888888888,
        "closing_quantity": 55,
        "closing_value": 555555555
      },
      "item_count": 2,
      "item_columns": ["item_name","opening_quantity","opening_value","inflow_quantity","inflow_value","outflow_quantity","outflow_value","closing_quantity","closing_value"],
      "rankings": [
        {"sorted_by": "outflow_value", "items": [
          ["Mặt hàng mẫu B",44,444444444,55,555555555,66,666666666,33,333333333],
          ["Mặt hàng mẫu A",11,111111111,33,333333333,22,222222222,22,222222222]
        ]},
        {"sorted_by": "closing_value", "items": [
          ["Mặt hàng mẫu B",44,444444444,55,555555555,66,666666666,33,333333333],
          ["Mặt hàng mẫu A",11,111111111,33,333333333,22,222222222,22,222222222]
        ]}
      ]
    }
  },
  "unmapped_columns": [],
  "extraction_notes": []
}

Return EXACTLY this schema and nothing else.
