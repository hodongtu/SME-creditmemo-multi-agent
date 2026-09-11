"""LLM extraction of the CIC R20 collateral report into structured JSON"""

from typing import Any

from src.agents.extraction.structured_extraction import (
    TRIEU_VND,
    build_extraction_chain,
    run_extraction,
    scale_amount,
)

REQUIRED_TOP_LEVEL_KEYS = {
    "bao_cao",
    "khach_hang",
    "danh_sach_tctd",
    "tai_san_bao_dam",
}

CIC_R20_EXTRACTION_SYSTEM_PROMPT = """
You extract a LOAN SECURITY INFORMATION REPORT (form R20, sometimes labelled
R20) from Vietnam's National Credit Information Centre (CIC), for SME credit
underwriting. This is a DIFFERENT report from the "Báo cáo chi tiết quan hệ tín
dụng" (form S10A) — S10A is about outstanding debt, this one is only about
COLLATERAL.

The text is OCR of a scan, so lines may be skewed and columns run together.
Extract only what the page actually says. Do not infer, do not invent a figure.

READING NUMBERS — the easiest thing to get wrong, read carefully:

1. A "," here is a THOUSANDS SEPARATOR, not a decimal point: "12,345" -> 12345.

2. WRITE "Giá trị TS (Triệu VNĐ)" AS PRINTED, with only the thousands separators
   removed. Never multiply into millions, never convert units — the program does
   that afterwards.

3. "Loại tài sản" is the two-digit CODE printed on the page ("01", "08", "21"),
   NOT a row number. Copy it verbatim AS A STRING, keeping any leading zero
   ("08" stays "08", not "8"). Never translate what the code means — the report
   prints no legend, and a wrong meaning is worse than none.

4. An empty "Mã số tài sản" or "Ngày giải chấp" cell -> null. An empty "Ngày
   giải chấp" means the asset HAS NOT BEEN RELEASED (still pledged), not that
   data is missing — a meaningful state, not an OCR failure.

5. A "Mô tả tài sản" reading "Không có bảo đảm tiền vay bằng tài sản" (or an
   equivalent confirming the lender holds no collateral) STILL GETS RECORDED in
   full, with its numeric and date fields null: a lender confirming it has no
   security is real information. It usually sits at the END of the list. Do not
   confuse it with the legal notes under "3. THÔNG TIN KHÁC VỀ KHÁCH HÀNG VAY" —
   an asset block always comes BEFORE that heading.

6. COUNT THE ASSET BLOCKS BY THE PHRASE "Danh sách Tài sản bảo đảm:", never by
   the row numbers. OCR misreads those routinely (reading "3." as "5.", or
   repeating one "3." on two blocks), and two blocks that look alike — same
   number, same lender — then get merged away, whereas that label appears
   exactly once per real block. Count its occurrences in the whole text:
   "tai_san_bao_dam" must have EXACTLY that many elements, no more and no fewer.

   Two CONSECUTIVE blocks from the SAME lender may carry a "Mã số tài sản"
   differing only in the last few characters — "MD01234567" against
   "MD01234BDS" — and still be TWO COMPLETELY DIFFERENT ASSETS. Tell them apart
   by each block's "Mô tả tài sản"; never drop one for looking like a duplicate.

   Only then copy each printed row number into "stt" in order of appearance, or
   null when it cannot be read rather than a guess. The numbering runs
   CONTINUOUSLY through the report — it does not restart at 1 for each lender.

7. No "THÔNG TIN ĐẢM BẢO TIỀN VAY" section at all — for instance the document is
   really an S10A or another CIC report — set "tai_san_bao_dam": [] and record
   why in "extraction_notes". Never invent a list of assets.

"page" is the page the fact sits on, read from the "--- Page N ---" markers in
the OCR text; null when it cannot be determined. Do not invent a page number.

Return EXACTLY this JSON schema and no other text:
{{
  "bao_cao": {{
    "so_hieu": "e.g. 2026/R20, or ''",
    "ngay_gui": "dd/mm/yyyy or ''",
    "don_vi_tra_cuu": "name of the enquiring institution, or ''",
    "page": <integer or null>
  }},
  "khach_hang": {{
    "ten": "...", "ma_cic": "...", "ma_so_thue": "...",
    "nguoi_dai_dien": "...", "dia_chi": "...",
    "page": <integer or null>
  }},
  "danh_sach_tctd": [
    {{"stt": <integer or null>, "tctd": "institution/branch name",
      "ma_tctd": "...", "ngay_bao_cao_du_no": "dd/mm/yyyy or ''",
      "page": <integer or null>}}
  ],
  "tai_san_bao_dam": [
    {{"stt": <the printed number when legible, otherwise null>,
      "ma_tctd": "...", "tctd": "institution/branch name",
      "ngay_bao_cao_tai_san": "dd/mm/yyyy or ''",
      "ma_so_tai_san": "... or null",
      "loai_tai_san": "two-digit code as a string, e.g. '08', or null",
      "mo_ta_tai_san": "the description verbatim from the page",
      "chu_so_huu": "... or ''",
      "gia_tri_trieu_vnd": <number exactly as printed, or null>,
      "ngay_the_chap": "dd/mm/yyyy or ''",
      "ngay_giai_chap": "dd/mm/yyyy, or null when empty",
      "page": <integer or null>}}
  ],
  "thong_tin_khac": "section 3 verbatim when legible, or ''",
  "extraction_notes": ["notes on missing sections, uncertainty, or poor OCR"]
}}
"""


def build_cic_r20_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for the CIC R20 report."""

    return build_extraction_chain(CIC_R20_EXTRACTION_SYSTEM_PROMPT, llm)


def normalize_amounts(result: dict[str, Any]) -> dict[str, Any]:
    """Scale every collateral value from triệu đồng to đồng, in place."""

    for row in result.get("tai_san_bao_dam") or []:
        if isinstance(row, dict):
            row["gia_tri_trieu_vnd"] = scale_amount(
                row.get("gia_tri_trieu_vnd"), TRIEU_VND
            )
    return result


def extract_cic_r20_structured_data(
    chain: Any,
    filename: str,
    content: str,
    path: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Run the extraction chain and validate its shape. Never raises."""

    return run_extraction(
        chain,
        filename,
        content,
        REQUIRED_TOP_LEVEL_KEYS,
        "No CIC R20 extraction LLM configured.",
        normalize_amounts,
    )
