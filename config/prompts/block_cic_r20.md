---
name: block_cic_r20
description: How to read the extracted CIC R20 collateral report
---
Read from the CIC collateral report (form R20): which lenders hold security, and each
pledged asset under that lender.

UNITS: "gia_tri_trieu_vnd" is already in đồng despite the field name (the printed unit was
triệu đồng).

"loai_tai_san" is CIC's two-digit CODE (e.g. "08"), not a description — the report ships
no code legend, so do not infer what a code means.

"ngay_giai_chap": null means the asset is still pledged, NOT that the date is missing.

A block whose "mo_ta_tai_san" reads "Không có bảo đảm tiền vay bằng tài sản" means that
lender confirms it holds NO security — that is a real finding, not an extraction failure.
