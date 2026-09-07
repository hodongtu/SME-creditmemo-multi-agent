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

REQUIRED_TOP_LEVEL_KEYS = {"accounts"}

# Characters the rendered block may occupy, across every ledger file together.
# Rows are never dropped from the stored record — this only bounds what reaches
# the prompt, against the agent's 120k total. Measured: the sample workbook's 48
# rows render to ~14k, so a real ledger fits whole and nothing is aggregated.
LEDGER_BLOCK_CHAR_BUDGET = 40_000
# Label of the row standing in for everything the budget left out. It carries
# the summed figures of those rows, so visible rows still add up to the total.
RESIDUAL_LABEL = "Các đối tác còn lại"
# Names a printed total row carries. Kept as a set because the prompt already
# tells the model to leave these out of "items" and it does it anyway — on the
# sample workbook the inventory sheet came back with an eleventh row called
# "Tổng" holding the column sums, which would have every figure of that account
# counted twice by anything adding the rows up.
TOTAL_ROW_NAMES = {"tong", "tong cong", "cong", "total", "sum"}


LEDGER_EXTRACTION_SYSTEM_PROMPT = """
Bạn đọc sổ chi tiết / bảng cân đối phát sinh công nợ của doanh nghiệp Việt Nam
(sổ chi tiết tài khoản, bảng tổng hợp nhập xuất tồn) và trả về MỘT bản ghi JSON
duy nhất cho toàn bộ số file được đưa vào, phục vụ thẩm định tín dụng SME.

Đầu vào là văn bản đã đọc từ file Excel/CSV, ngăn theo file bằng dòng
"=== FILE i/N: <tên file> ===" và ngăn theo sheet bằng dòng
"--- Sheet: <tên sheet> ---". Chỉ đọc trong phạm vi từng file; KHÔNG lấy số của
file này gán cho file khác.

PHẢI TRẢ VỀ MỘT MỤC CHO MỌI SHEET. Dòng "SHEET PHẢI XỬ LÝ:" ở đầu đầu vào liệt
kê đủ danh sách — đi hết danh sách đó, đừng dừng sớm và đừng gộp hai sheet làm
một trừ khi chúng cùng tài khoản VÀ cùng kỳ. Sheet nào không xếp được thì vẫn
phải nói ra trong "extraction_notes"; bỏ im lặng một sheet là hỏng nặng nhất,
vì bản ghi trông vẫn đầy đủ trong khi một nửa sổ đã biến mất.

CHÉP ĐỦ MỌI DÒNG CHI TIẾT của từng sheet, không lấy mẫu, không rút gọn.

BẠN LÀ NGUỒN CỦA MỌI CON SỐ trong bản ghi. Không có chương trình nào đọc lại
file để sửa cho bạn. Chép số đúng từng chữ số như in trong văn bản.

════ KHUNG NGOÀI CÙNG ════
{{"accounts": {{...}}, "unmapped_columns": [], "extraction_notes": []}}

BA KHOÁ NÀY NGANG HÀNG NHAU. "unmapped_columns" và "extraction_notes" nằm CÙNG
CẤP với "accounts", TUYỆT ĐỐI không đặt chúng vào bên trong "accounts" — trong
"accounts" chỉ có các mục tài khoản.

- "unmapped_columns": tiêu đề cột không xếp được vào tên trường chuẩn, GIỮ
  NGUYÊN VĂN TIẾNG VIỆT. Thà để lộ tên tiếng Việt còn hơn bịa một tên trường
  tiếng Anh cho cột chưa ai hiểu.
- "extraction_notes": sheet nào không xếp được và vì sao. Chỉ vậy.

════ KHOÁ CỦA "accounts" — MỘT FORMAT DUY NHẤT ════
    <số hiệu tài khoản>@<YYYYMMDD>-<YYYYMMDD>

Ví dụ: "131@20250101-20251231". LUÔN mang kỳ, kể cả khi cả hồ sơ chỉ có một kỳ.

    Cả năm 2025          -> 131@20250101-20251231
    Quý 1/2025           -> 131@20250101-20250331
    6 tháng đầu 2025     -> 131@20250101-20250630
    9 tháng đầu 2025     -> 131@20250101-20250930
    Niên độ 07/24-06/25  -> 131@20240701-20250630

SỐ HIỆU TÀI KHOẢN LẤY Ở ĐÂU
1. Nếu file/sheet CÓ IN số hiệu (tên file "SO CHI TIET TK 131.xlsx", tiêu đề
   "Tài khoản 331", cột "TK 156"...) thì dùng đúng số đó, code_source="printed".
2. Nếu KHÔNG in ở đâu cả thì dùng ĐÚNG MỘT mã quy ước theo category dưới đây,
   code_source="convention". Dùng chính xác mã trong bảng, KHÔNG chọn mã khác
   cùng nhóm:

     cash              -> 112        receivable        -> 131
     other_receivable  -> 138        inventory         -> 156
     fixed_asset       -> 211        payable           -> 331
     other_payable     -> 338        borrowing         -> 341
     equity            -> 411

   Sai ở đây là hỏng nặng: "sổ vay" gán 311 thay vì 341, hay "phải trả khác"
   gán 331 thay vì 338, sẽ đưa toàn bộ số liệu của một tài khoản vào chỗ của
   tài khoản khác, mà các con số thì vẫn trông đúng.

Hai trường hợp biên:
- Không đọc được kỳ            -> "131@unknown"
- Không xác định được số hiệu  -> "unknown_<tên sheet>@<kỳ>"

Hai mục khác kỳ là HAI SỰ THẬT RIÊNG — tuyệt đối không cộng số dư của chúng vào
nhau. Nhiều sheet CÙNG tài khoản VÀ CÙNG kỳ thì gộp làm một mục: sổ Việt Nam hay
tách một tài khoản ra nhiều sheet theo tháng hoặc theo nhóm hàng.

════ MỖI MỤC TRONG "accounts" — ĐÚNG 10 KHOÁ ════
"category"       một trong: cash receivable other_receivable inventory
                 fixed_asset payable other_payable borrowing equity unknown
                 Thà "unknown" còn hơn đoán: người phân tích đọc được một sheet
                 chưa đặt tên, nhưng sẽ TIN một cái tên sai.
"code_source"    "printed" khi số hiệu tài khoản có in trong file;
                 "convention" khi file không in và bạn xếp theo quy ước hệ
                 thống tài khoản Việt Nam.
"code_evidence"  một câu tiếng Việt: đã đọc số hiệu từ đâu (tên file, tên
                 sheet, tiêu đề báo cáo).
"source_files"   danh sách tên file đã đóng góp, ĐÚNG NGUYÊN VĂN tên đã cho.
"period"         {{"from","to","as_printed"}} — xem dưới.
"source_columns" {{tên_trường_chuẩn: "tiêu đề cột nguyên văn"}}. Ghi MỌI cột đã
                 xếp được, kể cả cột định danh.
"units"          {{tên_trường: "vnd" hoặc "quantity"}}. CHỈ ghi cho trường thực
                 sự có mặt trong "items".
"totals"         {{tên_trường: số}} — tổng của cả tài khoản, ghi cả trường bằng 0.
"item_count"     đúng bằng số phần tử của "items".
"items"          toàn bộ dòng chi tiết — xem dưới.

════ "period" ════
"from"/"to"    ngày ISO "YYYY-MM-DD", đọc từ dòng banner in phía trên bảng.
"as_printed"   dòng banner NGUYÊN VĂN.

Các cách viết thường gặp và cách mở rộng:
    "Từ ngày 01/01/2025 đến ngày 31/12/2025" -> 2025-01-01 / 2025-12-31
    "Kỳ báo cáo: 01/01/2025 - 31/12/2025"    -> 2025-01-01 / 2025-12-31
    "Năm 2024"                               -> 2024-01-01 / 2024-12-31
    "Quý 4/2024"                             -> 2024-10-01 / 2024-12-31
    "Tháng 12 năm 2024"                      -> 2024-12-01 / 2024-12-31
Không dòng nào nêu kỳ thì để "" cả ba trường — KHÔNG suy từ tên file có chứa năm.

════ "items" — CHỖ DỄ SAI NHẤT ════
Mỗi phần tử: khoá "name" + CHỈ các trường số. Cột định danh
("counterparty_code", "booking_unit") có trong "source_columns" nhưng KHÔNG vào
"items"; tên đối tác hoặc tên mặt hàng đi vào "name".

Số ghi NGUYÊN ĐỒNG, không dấu phân cách, không đơn vị, không ngoặc:
    đúng: 225510140846
    sai : "225.510.140.846"   225,51 tỷ   (225510140846)
Số âm dùng dấu trừ. Ô trống hoặc gạch ngang ghi 0. Không lược bỏ trường có giá
trị 0.

DÒNG TỔNG IN TRÊN FILE ("Tổng", "Tổng cộng", "Cộng", "Total") KHÔNG PHẢI MỘT
DÒNG CHI TIẾT: đưa nó vào "totals", tuyệt đối không thêm vào "items". Để lọt
vào "items" thì mọi con số của tài khoản đó bị cộng đôi.

"totals" lấy từ dòng tổng in trên file nếu có; nếu file không in dòng tổng thì
tự cộng các dòng chi tiết.

════ TÊN TRƯỜNG CHUẨN — HAI BỘ, KHÔNG TRỘN ════
Công nợ (phải thu, phải trả, vay):
    counterparty_code counterparty_name booking_unit
    opening_debit opening_credit debit_movement credit_movement
    closing_debit closing_credit
Nhập xuất tồn:
    item_name
    opening_quantity opening_value inflow_quantity inflow_value
    outflow_quantity outflow_value closing_quantity closing_value

Cột đếm dòng ("Stt", "TT") KHÔNG phải một trường: bỏ hẳn, cũng đừng đưa vào
"unmapped_columns". Không ánh xạ hai tiêu đề vào cùng một trường.

════ VÍ DỤ ĐẦY ĐỦ (một mục công nợ, một mục nhập xuất tồn) ════
{{
 "accounts": {{
  "131@20250101-20251231": {{
   "category": "receivable",
   "code_source": "convention",
   "code_evidence": "Sheet TK_131, tiêu đề Báo cáo chi tiết công nợ phải thu; file không in số hiệu.",
   "source_files": [
    "VIMID_so_chi_tiet.xlsx"
   ],
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
   "items": [
    {{
     "name": "NGUYỄN VĂN A",
     "opening_debit": 0,
     "opening_credit": 0,
     "debit_movement": 20738000,
     "credit_movement": 20738000,
     "closing_debit": 0
    }},
    {{
     "name": "NGUYỄN VĂN B",
     "opening_debit": 0,
     "opening_credit": 10000000,
     "debit_movement": 1391300000,
     "credit_movement": 1381300000,
     "closing_debit": 0
    }}
   ]
  }},
  "156@20250101-20251231": {{
   "category": "inventory",
   "code_source": "convention",
   "code_evidence": "Sheet NXT, tiêu đề Báo cáo tổng hợp nhập xuất tồn; file không in số hiệu.",
   "source_files": [
    "VIMID_so_chi_tiet.xlsx"
   ],
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
   "items": [
    {{
     "name": "Mooc",
     "opening_quantity": 49,
     "opening_value": 22017037516,
     "inflow_quantity": 113,
     "inflow_value": 48305699921,
     "outflow_quantity": 112,
     "outflow_value": 48335332899,
     "closing_quantity": 50,
     "closing_value": 21987404538
    }},
    {{
     "name": "Đầu kéo",
     "opening_quantity": 191,
     "opening_value": 205670531917,
     "inflow_quantity": 1381,
     "inflow_value": 1590661714792,
     "outflow_quantity": 1001,
     "outflow_value": 1156312267740,
     "closing_quantity": 571,
     "closing_value": 640019978969
    }}
   ]
  }}
 }},
 "unmapped_columns": [],
 "extraction_notes": []
}}

Trả về ĐÚNG schema trên, không kèm chữ nào khác.
"""


# ── Fitting the record to the prompt's character budget ───────────────────

def _magnitude(row: dict[str, Any]) -> float:
    """How big this row is, across every column rather than one of them."""

    return max(
        (abs(v) for k, v in row.items()
         if k != "name"
         and isinstance(v, (int, float)) and not isinstance(v, bool)),
        default=0.0,
    )


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One row summing every field of the rows it stands in for."""

    totals: dict[str, float] = {}
    for row in rows:
        for key, value in row.items():
            if (key != "name"
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                totals[key] = totals.get(key, 0) + value
    return {"name": f"{RESIDUAL_LABEL} ({len(rows)})",
            **{k: round(v) for k, v in totals.items()}}


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
            rows = account.get("items") or []
            if len(rows) <= keep:
                accounts[key] = account
                continue
            ordered = sorted(rows, key=_magnitude, reverse=True)
            accounts[key] = {**account,
                             "items": ordered[:keep] + [_aggregate(ordered[keep:])]}
        return {**record, "accounts": accounts}

    def size(candidate: dict[str, Any]) -> int:
        return len(json.dumps(candidate, ensure_ascii=False, indent=2))

    if size(record) <= budget:
        return record
    widest = max((len(a.get("items") or [])
                  for a in (record.get("accounts") or {}).values()), default=0)
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


def _raw_text_payload(documents: list[tuple[str, str, str]]) -> str:
    """Every file's text under a fence naming it, in one string.

    The text is already split by sheet — ``extract_excel_text`` writes a
    "--- Sheet: … ---" line before each one and keeps the .xlsx number formats,
    and ``extract_csv_text`` covers the flat case. So the only thing missing is
    the file boundary, which matters because the record spans files and a figure
    borrowed from the wrong one is invisible once it is in the JSON.

    Numbered i/N rather than named alone: a fence that only carries a name
    cannot tell the model that a file it should have seen is absent.
    """

    total = len(documents)
    body = "\n\n".join(
        f"=== FILE {index}/{total}: {filename} ===\n{content or '(không đọc được nội dung)'}"
        for index, (filename, content, _path) in enumerate(documents, start=1)
    )
    sheets = sheet_inventory(documents)
    listed = "\n".join(f"  {i}. {name}" for i, name in enumerate(sheets, start=1))
    return f"SHEET PHẢI XỬ LÝ ({len(sheets)}):\n{listed}\n\n{body}"


def sheet_inventory(documents: list[tuple[str, str, str]]) -> list[str]:
    """Every "file › sheet" the payload contains, in reading order.

    Read back out of the text rather than tracked while building it, so the list
    describes what the model is actually looking at. A file with no sheet marker
    — a .csv — counts as one sheet named after the file.
    """

    found: list[str] = []
    for filename, content, _path in documents:
        names = re.findall(r"^--- Sheet: (.+?) ---$", content or "", re.M)
        found += [f"{filename} › {n.strip()}" for n in names] or [filename]
    return found


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
        rows = account.get("items") or []
        kept = [
            row for row in rows
            if normalize_text(str(row.get("name", ""))) not in TOTAL_ROW_NAMES
        ]
        if len(kept) != len(rows):
            account["items"] = kept
            account["item_count"] = len(kept)


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

    record, error = run_extraction(
        chain,
        ", ".join(filename for filename, _, _ in documents),
        _raw_text_payload(documents),
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
            f"CẢNH BÁO: đầu vào có {expected} sheet nhưng chỉ trích xuất được "
            f"{returned} tài khoản. Nhiều sheet đã bị bỏ qua — số liệu dưới đây "
            f"KHÔNG phải toàn bộ sổ chi tiết khách hàng nộp."
        )
    return [(record, "") for _ in documents]
