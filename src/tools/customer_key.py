"""Which customer a run is about, read off the documents it was given.

A reference-data query needs a key, and the pipeline has no field for one — the
screen supplies the flow and the loan programme, not the customer. So the key is
read from the extraction results, in a fixed order of preference.

Everything here is deliberately unforgiving. The key selects whose credit history
gets pulled into a report: a tax code misread by one digit does not fail, it
quietly returns another company's debts. So a code that is not exactly the right
shape is refused rather than sent, and every accepted key carries the file and
field it came from.
"""

import re
from typing import Any, NamedTuple

# Vietnamese tax codes are 10 digits, or 13 when a 3-digit branch suffix is
# appended. Anything else is not a tax code, whatever the model wrote.
_TAX_CODE = re.compile(r"^\d{10}(?:\d{3})?$")

# The order is a business decision, not a technical one: the financial statements
# first, then the site-visit report, then the credit application.
#
# The CIC reports are deliberately NOT here even though they also print the code.
# They are the document the query exists to replace, so a folder that has one
# does not need the query; and reading the key from the very report being
# superseded would make the two paths depend on each other.
KEY_SOURCES: tuple[tuple[str, str], ...] = (
    ("financial_statement_extraction", "customer"),
    ("sitevisit_extraction", "customer"),
    ("proposal_extraction", "customer"),
)


class CustomerKey(NamedTuple):
    """A tax code, and the evidence for it."""

    tax_code: str
    name: str
    source_file: str
    source_field: str
    warnings: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return bool(self.tax_code)


def _clean(raw: Any) -> str:
    """Digits only. Forms print "0104498100-001" and "0104 498 100"."""

    return re.sub(r"\D", "", str(raw or ""))


def _candidates(documents: list[Any]) -> list[tuple[int, str, str, str, str]]:
    """(rank, tax_code, name, filename, field) for every document that names one."""

    found = []
    for rank, (attr, block) in enumerate(KEY_SOURCES):
        for doc in documents:
            record = getattr(doc, attr, None)
            if not isinstance(record, dict):
                continue
            holder = record.get(block)
            if not isinstance(holder, dict):
                continue
            code = _clean(holder.get("ma_so_thue"))
            if code:
                found.append((rank, code, str(holder.get("ten") or ""),
                              doc.filename, f"{attr}.{block}.ma_so_thue"))
    return sorted(found, key=lambda row: row[0])


def resolve_customer_key(documents: list[Any]) -> CustomerKey:
    """The customer this run is about, or an empty key with the reason why."""

    candidates = _candidates(documents)
    if not candidates:
        return CustomerKey("", "", "", "", (
            "Không tài liệu nào nêu mã số thuế, nên không truy vấn dữ liệu tham chiếu.",
        ))

    rank, code, name, filename, field = candidates[0]
    warnings = []

    # Say it before choosing, not after: two different codes in one folder is a
    # sign of a file from the wrong customer, and picking the higher-ranked one
    # silently would bury exactly the case worth stopping for.
    distinct = {row[1] for row in candidates}
    if len(distinct) > 1:
        listed = ", ".join(
            f"{row[1]} ({row[3]})" for row in candidates if row[1] in distinct
        )
        warnings.append(
            f"The dossier states {len(distinct)} different tax codes: {listed}. "
            f"Using {code} by source precedence — check whether another "
            f"customer's documents got mixed in."
        )

    if not _TAX_CODE.match(code):
        return CustomerKey("", name, filename, field, tuple(warnings) + (
            f"Mã số thuế đọc được từ {filename} là {code!r} ({len(code)} chữ số) — "
            f"không phải 10 hoặc 13 chữ số, nên KHÔNG truy vấn.",
        ))
    return CustomerKey(code, name, filename, field, tuple(warnings))
