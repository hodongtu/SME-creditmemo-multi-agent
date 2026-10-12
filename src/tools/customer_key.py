"""Which customer a run is about, read off the documents it was given."""

import re
from typing import Any, NamedTuple

_TAX_CODE = re.compile(r"^\d{10}(?:\d{3})?$")

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
            "No document states a tax code, so no reference data was queried.",
        ))

    rank, code, name, filename, field = candidates[0]
    warnings = []

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
            f"The tax code read from {filename} is {code!r} ({len(code)} digits) "
            f"— not 10 or 13, so NO query was made.",
        ))
    return CustomerKey(code, name, filename, field, tuple(warnings))
