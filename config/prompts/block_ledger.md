---
name: block_ledger
description: How to read the extracted detail-ledger record
---
Read from detail ledgers / debt trial balances the customer supplied as Excel, every file
merged into one record. A key under "accounts" is always <account
number>@<YYYYMMDD>-<YYYYMMDD>, e.g. "131@20250101-20251231". One key is ONE period; two
keys with the same account number and different periods are separate entries and their
balances must NEVER be added together. Read the period from "period" — "from"/"to" are the
boundary dates — and use it to place figures in the right year column. Do not read the
year off the key.

THESE FIGURES WERE READ FROM THE EXCEL CONTENT, not from OCR of an image. It is a
transcription of the original table rather than a cell-by-cell extraction, so when a
figure decides a credit conclusion, say which file ("source_files"), which sheet
("source_sheet_name") and which account it came from, so the reviewer can check it against
the original.

UNITS COME FROM THE FIELD NAME; there is no separate key. A field ending in "_quantity" is
a COUNT (units, pieces — not money); every other numeric field is đồng. Never write "tỷ
VNĐ" after a "_quantity" figure, and never add a count to a value.

THIS IS DATA TO FILL THE REPORT WITH, not a layout. "category" says which section it
belongs to: "receivable" → Phải thu khách hàng; "payable" → Phải trả người bán (a DEBIT
balance on this account is Trả trước cho người bán); "inventory" → Hàng tồn kho;
"fixed_asset" → Tài sản cố định, tài sản dở dang dài hạn; "cash" → Tiền và các khoản tương
đương tiền; "borrowing" → Vay nợ ngắn hạn và dài hạn; "equity" → Vốn chủ sở hữu;
"other_receivable" → Các khoản mục tài sản khác; "other_payable" → Các khoản mục nguồn vốn
khác.

Field names: "opening_debit"/"opening_credit" are the opening debit and credit balances,
"debit_movement"/"credit_movement" the movements, "closing_debit"/"closing_credit" the
closing balances; stock ledgers use "opening_/inflow_/outflow_/closing_" with "_quantity"
or "_value".

"rankings" holds one ranked list per account, each declaring the column it was sorted by
in "sorted_by", with "items" the five largest rows BY THAT COLUMN. The same counterparty
appearing in several lists is normal. "item_count" is how many rows the source sheet
holds; "totals" is the figure for the WHOLE account, listed rows and unlisted alike. The
analysis guidance says which list each report section reads.

WHEN THE SHEET HAS MORE ROWS THAN A LIST SHOWS, THE LIST ENDS IN ONE MORE ROW, named
"{{residual_label}} (N)" on a debt ledger or "{{residual_item_label}} (N)" on a stock
ledger. It is everything that list leaves out — N rows — and the program computed it as
"totals" minus the rows listed above it, so the listed rows plus this one add up to
"totals" exactly. It is not a counterparty or an item: never rank it, never cite it as a
name, and never count it among the five. A figure in it that is null means the total for
that column was not available, so the remainder is UNKNOWN — not zero.

Each row is an ARRAY of values in exactly the order declared in "item_columns" for that
account — one "item_columns" governs every list of that account. Value i belongs to column
name i. Two accounts may declare different column orders, so read the "item_columns" of
the account in front of you rather than reusing the previous one.

Debt ledgers keep the counterparty in TWO columns: "counterparty_code" is the code,
"counterparty_name" the name — cite by NAME, and fall back to the code only when there is
no name. Stock ledgers use "item_name".

"code_source": "printed" means the account number was printed in the file; "convention"
means the file printed none and the program assigned one from the chart of accounts. Never
cite a "convention" number as though the customer had written it.
