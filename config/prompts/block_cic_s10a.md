---
name: block_cic_s10a
description: How to read the extracted CIC S10A report
---
Read from the CIC credit-relationship detail report (form S10A): current balance per
lender, the last 12 months of balances, off-balance-sheet commitments, credit rating and
warning history.

UNITS: the VNĐ fields ("vnd", "du_no_vay", "du_no_the", "tong_du_no") are already in đồng.
The foreign-currency fields ("ngoai_te") are left IN THEIR OWN CURRENCY as printed — do
not convert them, and never add them to a VNĐ column.

"du_no_12_thang" ALREADY includes foreign-currency debt converted; adding the foreign
figures from another block counts them twice.

A null means that period has no reported figure — NOT zero.
