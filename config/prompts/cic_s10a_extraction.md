---
name: cic_s10a_extraction
description: CIC S10A report (credit relationships) -> structured JSON
---
You turn a CREDIT RELATIONSHIP DETAIL REPORT (form S10A) from Vietnam's National
Credit Information Centre (CIC) into JSON, for SME credit underwriting.

The text is OCR of a scan, so lines may be skewed and columns run together.
Extract only what the page actually says. Do not infer, do not invent a figure.

READING NUMBERS — the easiest thing to get wrong, read carefully:

1. A "," here is a THOUSANDS SEPARATOR, not a decimal point: "12,345" -> 12345.
   Reading it as 12.345 is wrong by a factor of a thousand.

2. WRITE THE NUMBER AS PRINTED, with only the thousands separators removed.
   Never multiply into millions, never convert units — the program does that
   afterwards.

3. "(-)" means "Thiếu kỳ báo cáo số liệu" (no data for that period) -> null. An
   EMPTY cell -> null. Never substitute 0: "no data" and "a balance of zero" are
   opposite statements to anyone reading the debt chart.

4. The VND column and the foreign-currency (USD) column are separate. Do not add
   them together and do not convert between them — the report prints no exchange
   rate.

5. Table "2.6. Diễn biến dư nợ 12 tháng gần nhất" carries a "(*)" note: those
   balances ALREADY INCLUDE foreign-currency debt converted to VND. Copy the
   column as it stands; adding USD from another table would count it twice.

6. Keep the rows in the page's order (table 2.6 runs newest month first). Do not
   re-sort.

7. No section 2.6 at all — for instance the document is really a loan security
   report, form R20 — set "du_no_12_thang": [] and record why in
   "extraction_notes". Never invent a twelve-month series.

Write every month as "MM/YYYY", two digits for the month, e.g. "03/2026".

"page" is the page the table sits on, read from the "--- Page N ---" markers in
the OCR text; null when it cannot be determined. Do not invent a page number.

Return EXACTLY this JSON schema and no other text:
{
  "bao_cao": {
    "so_hieu": "e.g. 2026/S10A, or ''",
    "ngay_gui": "dd/mm/yyyy or ''",
    "don_vi_tra_cuu": "name of the enquiring institution, or ''",
    "page": <integer or null>
  },
  "khach_hang": {
    "ten": "...", "ma_cic": "...", "ma_so_thue": "...",
    "nguoi_dai_dien": "...", "dia_chi": "...",
    "page": <integer or null>
  },
  "du_no_hien_tai": [
    {"tctd": "lender name and branch",
      "ngay_bao_cao": "dd/mm/yyyy or ''",
      "khoan_muc": "e.g. Dư nợ cho vay ngắn hạn | Dư nợ cho vay trung hạn | Tổng cộng",
      "nhom_no": "e.g. Nợ đủ tiêu chuẩn, or ''",
      "vnd": <number exactly as printed, or null>,
      "ngoai_te": <number exactly as printed, or null>,
      "loai_ngoai_te": "e.g. USD, or ''",
      "page": <integer or null>}
  ],
  "du_no_12_thang": [
    {"thang": "MM/YYYY",
      "du_no_vay": <number exactly as printed, or null>,
      "du_no_the": <number exactly as printed, or null>,
      "tong_du_no": <number exactly as printed, or null>,
      "page": <integer or null>}
  ],
  "cam_ket_ngoai_bang": [
    {"tctd": "...", "gia_tri": <number exactly as printed, or null>,
      "loai_tien": "VND | USD | ...", "nhom_no": "...",
      "ngay_bao_cao": "dd/mm/yyyy or ''", "page": <integer or null>}
  ],
  "xep_hang_tin_dung": [
    {"nam": "YYYY", "hang": "e.g. T3, B2", "pd_phan_tram": <number or null>,
      "dien_giai": "...", "page": <integer or null>}
  ],
  "canh_bao": {
    "no_xau_5_nam": "the conclusion of section 2.7 verbatim, or ''",
    "no_can_chu_y_12_thang": "the conclusion of section 2.9 verbatim, or ''",
    "cham_thanh_toan_the": "the conclusion of section 2.8 verbatim, or ''",
    "du_no_the_tin_dung": "the conclusion of section 2.2 verbatim, or ''",
    "du_no_ban_vamc": "the conclusion of section 2.3 verbatim, or ''"
  },
  "extraction_notes": ["notes on missing sections, uncertainty, or poor OCR"]
}
