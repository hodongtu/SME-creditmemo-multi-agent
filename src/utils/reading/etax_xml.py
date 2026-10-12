"""The envelope every Vietnamese e-tax XML filing shares (eTax/HTKK `HSoThueDTu`).

What a filing is and whose it is. The body of each form is read elsewhere:
VAT 01/GTGT in src/agents/extraction/vat_revenue.py, BCTC B01a-DNN in
src/agents/extraction/financial_statement_extraction.py.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

NAMESPACE = "http://kekhaithue.gdt.gov.vn/TKhaiThue"
_NS = {"n": NAMESPACE}
_CODE = re.compile(r"ct\d+\w*")

FORM_VAT_01GTGT = "842"
FORM_FINANCIAL_STATEMENT_B01A_DNN = "683"
KIND_BY_FORM = {
    FORM_VAT_01GTGT: "vat",
    FORM_FINANCIAL_STATEMENT_B01A_DNN: "financial_statement",
}


@dataclass
class FilingHeader:
    """Which form a filing is and whose it is, or why that could not be read."""

    kind: str = ""                       # "vat" | "financial_statement" | "" (unsupported)
    form_id: str = ""
    form_name: str = ""
    taxpayer_id: str = ""
    taxpayer_name: str = ""
    error: str = ""


def _root(path: str) -> ET.Element:
    """Parse from the XML declaration, ignoring anything before it."""

    raw = open(path, encoding="utf-8-sig").read()
    start = raw.find("<?xml")
    if start > 0:
        raw = raw[start:]
    return ET.fromstring(raw)


def find(root: ET.Element | None, tag: str) -> ET.Element | None:
    return root.find(f".//n:{tag}", _NS) if root is not None else None


def text(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    found = node.find(f"n:{tag}", _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def indicator_codes(node: ET.Element | None) -> dict[str, float]:
    """Every ct* code under a node, as numbers."""

    codes: dict[str, float] = {}
    for element in (node.iter() if node is not None else []):
        tag = element.tag.split("}")[-1]
        if _CODE.fullmatch(tag) and (element.text or "").strip():
            try:
                codes[tag] = float(element.text)
            except ValueError:
                continue
    return codes


def column_codes(parent: ET.Element | None, name: str) -> dict[str, float]:
    """Every ct* code inside the direct child block called `name` (SoCuoiNam, NamNay…)."""

    if parent is None:
        return {}
    for child in parent:
        if child.tag.split("}")[-1] == name:
            return indicator_codes(child)
    return {}


def year_of(date_text: str) -> str:
    """A date like "31/12/2025" or "2025-12-31" -> the report's year label."""

    match = re.search(r"(19|20)\d{2}", date_text or "")
    return f"Năm {match.group(0)}" if match else ""


def filing_period(root: ET.Element) -> tuple[str, str, str]:
    """(period type "M"/"Q"/"Y", period label as filed, period end date text)."""

    period = find(root, "KyKKhaiThue")
    return (
        (text(period, "kieuKy") or "").upper(),
        text(period, "kyKKhai"),
        text(period, "kyKKhaiDenNgay"),
    )


def main_block(root: ET.Element) -> ET.Element | None:
    return find(root, "CTieuTKhaiChinh")


def load_filing(path: str) -> tuple[ET.Element | None, FilingHeader]:
    """The parsed root and the filing's identity.

    The root is None when the file is not a readable e-tax XML, and an
    unsupported form comes back with a root but no kind: an indicator code only
    means something once the form is known, so the caller falls back to reading
    the document as ordinary text rather than guessing.
    """

    header = FilingHeader()
    try:
        root = _root(path)
    except Exception as exc:
        header.error = f"Could not parse the XML: {type(exc).__name__}: {exc}"
        return None, header
    if not root.tag.endswith("HSoThueDTu"):
        header.error = "Not an e-tax filing: the root element is not HSoThueDTu."
        return None, header
    declaration = find(root, "TKhaiThue")
    header.form_id = text(declaration, "maTKhai")
    header.form_name = text(declaration, "tenTKhai")
    taxpayer = find(root, "NNT")
    header.taxpayer_id = text(taxpayer, "mst")
    header.taxpayer_name = text(taxpayer, "tenNNT")
    header.kind = KIND_BY_FORM.get(header.form_id, "")
    if not header.kind:
        header.error = (
            f"Form maTKhai={header.form_id!r} ({header.form_name!r}) is not supported. "
            "An indicator code only means something once the form is known, so this "
            "document is read as ordinary text instead of guessed at."
        )
    return root, header
