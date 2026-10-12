"""The data blocks that go into a specialist's prompt, lifted out of Supervisor."""

from dataclasses import asdict
from typing import Any

from src.agents.extraction.cic_s10a_extraction import merge_debt_series
from src.agents.calculator.credit_need_calculator import build_credit_need_table
from src.agents.calculator.financial_ratio_calculator import (
    FinancialRatioCalculator,
    _format_number,
)
from src.agents.documents.document_matrix import get_type, is_catch_all_box
from src.agents.extraction import financial_statement_extraction
from src.agents.extraction import ledger_extraction
from src.prompts import system_prompt
from src.types import ClassifiedDocument
from src.agents.extraction.vat_revenue import (
    merge_vat_series,
    parse_vat_revenue_block,
    read_vat_revenue_xml,
)
from src.utils.report.visualization.charts import build_linechart_block, pick_unit
from src.utils.report.formatting import format_vn_number
from src.utils.report.source_list import build_source_lines


def _vat_revenue_from_xml(
    documents: list[ClassifiedDocument],
) -> dict[str, tuple[float, bool]]:
    """Monthly VAT revenue read straight out of any e-tax XML in the dossier."""

    series: dict[str, tuple[float, bool]] = {}
    for doc in documents:
        if not doc.path.lower().endswith(".xml"):
            continue
        monthly, _notes, error = read_vat_revenue_xml(doc.path)
        if not error:
            series.update(monthly)
    return series


def _format_credit_need_value(value: float | None, unit: str) -> str:
    """One cell of the credit-need table. """

    if value is None:
        return ""
    if unit == "VNĐ":
        return _format_number(value, "value")
    if unit == "%":
        return format_vn_number(value, 1)
    return format_vn_number(value, 0)

METRICS_BLOCK_AGENTS = (
    "FINANCIAL_ANALYSIS_AGENT",
    "CREDIT_PROPOSAL_AGENT",
)

CREDIT_NEED_BLOCK_AGENTS = ("CREDIT_PROPOSAL_AGENT",)
FINANCIAL_STATEMENT_BLOCK_HEADING = "[EXTRACTED FINANCIAL STATEMENTS]"
PROPOSAL_BLOCK_HEADING = "[EXTRACTED CREDIT APPLICATION]"
CREDIT_NEED_BLOCK_HEADING = "[CREDIT NEED CALCULATION]"
CIC_S10A_BLOCK_HEADING = "[EXTRACTED CIC S10A REPORT]"
CIC_R20_BLOCK_HEADING = "[EXTRACTED CIC R20 REPORT]"
SITEVISIT_BLOCK_HEADING = "[EXTRACTED SITE VISIT REPORT]"
LEDGER_BLOCK_HEADING = "[EXTRACTED DETAIL LEDGER]"
DEBT_CHART_TITLE = "Diễn biến dư nợ và doanh thu VAT 12 tháng gần nhất"
DEBT_CHART_TITLE_DEBT_ONLY = "Diễn biến dư nợ 12 tháng gần nhất"
DEBT_CHART_COLUMNS = ("Tổng dư nợ (CIC)", "Doanh thu VAT")
VAT_ESTIMATE_NOTE = ("Một số tháng là số ước lượng, chia đều từ doanh thu khai theo quý.")
SOURCE_LIST_BLOCK_HEADING = "[SOURCE LIST — COPY VERBATIM]"


def _document_block_header(
    doc: ClassifiedDocument,
    target_agent: str,
) -> str:
    """The metadata lines that precede a document's content in the prompt."""

    level = doc.agent_relevance.get(target_agent)
    if level == "R":
        relevance = system_prompt("block_relevance_required").strip()
    elif level == "O":
        relevance = system_prompt("block_relevance_optional").strip()
    elif is_catch_all_box(doc.declared_group):
        relevance = system_prompt("block_relevance_other_box").strip()
    else:
        relevance = system_prompt("block_relevance_general").strip()
    matched_type = get_type(doc.document_type)
    return "\n".join(
        [
            f"Document filename: {doc.filename}",
            "Document type: "
            + (
                f"{doc.document_type} — {matched_type.label}"
                if matched_type
                else "không xác định"
            ),
            relevance,
            f"Classification reason: {doc.reasoning}",
            f"Extraction status: {doc.extraction_status}",
            f"Extraction error: {doc.extraction_error}",
            "",
        ]
    )


def _build_source_list_block(
    documents: list[ClassifiedDocument],
) -> str:
    """The finished "Nguồn thông tin" list, for the agent to copy verbatim."""

    lines = build_source_lines(
        [
            (doc.filename, doc.document_type, doc.source_description)
            for doc in documents
        ]
    )
    if not lines:
        return ""
    return "\n".join(
        [
            SOURCE_LIST_BLOCK_HEADING,
            system_prompt("block_source_list").strip(),
            "",
            *(f"- {line}" for line in lines),
        ]
    )


def _build_financial_metrics_block(
    documents: list[ClassifiedDocument],
    target_agent: str,
) -> str:
    """Deterministic ratio block for the agents that reason about figures."""

    if target_agent not in METRICS_BLOCK_AGENTS:
        return ""
    usable = [
        doc for doc in documents if doc.extraction_status == "success"
    ]
    if not usable:
        return ""
    try:
        return FinancialRatioCalculator().build_analysis_block(
            [asdict(doc) for doc in usable]
        )
    except Exception as exc:
        return f"[PRE-COMPUTED FINANCIAL METRICS unavailable: {exc}]"


def _build_credit_need_block(
    documents: list[ClassifiedDocument],
    target_agent: str,
) -> str:
    """Render the computed credit-need table for the credit proposal prompt."""

    if target_agent not in CREDIT_NEED_BLOCK_AGENTS:
        return ""
    usable = [doc for doc in documents if doc.extraction_status == "success"]
    if not usable:
        return ""
    try:
        calculator = FinancialRatioCalculator()
        yearly_metrics = calculator.metrics_from_documents(usable)
        if not yearly_metrics:
            return ""
        table = build_credit_need_table(
            yearly_metrics,
            calculator.compute_ratios(yearly_metrics),
            next(
                (d.proposal_extraction for d in usable
                 if d.is_proposal and d.proposal_extraction),
                None,
            ),
            next(
                (d.sitevisit_extraction for d in usable
                 if d.is_sitevisit and d.sitevisit_extraction),
                None,
            ),
            [d.cic_s10a_extraction for d in usable
             if d.is_cic_s10a and d.cic_s10a_extraction],
        )
    except Exception as exc:
        return f"{CREDIT_NEED_BLOCK_HEADING} unavailable: {exc}"

    if not table.rows:
        return ""
    lines = [
        CREDIT_NEED_BLOCK_HEADING,
        system_prompt("block_credit_need").strip(),
        "",
        f"| Chỉ tiêu | {table.latest_year} | {table.plan_year} | Đơn vị | Nguồn | Ghi chú |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in table.rows:
        lines.append(
            f"| {row.label} | {_format_credit_need_value(row.latest, row.unit)} "
            f"| {_format_credit_need_value(row.plan, row.unit)} "
            f"| {'tỷ VNĐ' if row.unit == 'VNĐ' else row.unit} "
            f"| {row.source} | {row.note} |"
        )
    if table.warnings:
        lines.append("")
        lines.extend(f"WARNING: {w}" for w in table.warnings)
    return "\n".join(lines)


def _render_structured_records(
    docs: list[ClassifiedDocument],
    flag_attr: str,
    extraction_attr: str,
    heading: str,
    notes: list[str],
) -> str:
    """Shared shape of the four "extracted JSON" blocks below: filter to the
    documents this report type actually produced, then dump each one's JSON
    under the heading and the reading rules specific to that report."""

    matched = [doc for doc in docs if getattr(doc, flag_attr) and getattr(doc, extraction_attr)]
    if not matched:
        return ""
    parts = [heading, *notes]
    for doc in matched:
        parts.append(
            f"--- {doc.filename} ---\n"
            + ledger_extraction.render_record(getattr(doc, extraction_attr))
        )
    return "\n\n".join(parts)


def _build_proposal_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted credit application records."""

    return _render_structured_records(
        selected, "is_proposal", "proposal_extraction",
        PROPOSAL_BLOCK_HEADING,
        [system_prompt("block_proposal").strip()],
    )


def _build_debt_chart_block(
    documents: list[ClassifiedDocument],
    sub_agent_outputs: dict[str, str],
) -> tuple[str, str]:
    """Build the ```linechart block from extracted CIC data."""

    if "CREDIT_RELATIONSHIP_AGENT" not in sub_agent_outputs:
        return "", ""

    series = merge_debt_series(
        (doc.filename, doc.cic_s10a_extraction)
        for doc in documents
        if doc.is_cic_s10a and doc.cic_s10a_extraction
    )
    if len(series) < 2:
        return "", ""

    months = [row["thang"] for row in series]
    debt = [row["du_no"] for row in series]

    vat_series = merge_vat_series(
        parse_vat_revenue_block(sub_agent_outputs.get("CREDIT_RELATIONSHIP_AGENT", "")),
        _vat_revenue_from_xml(documents),
    )
    revenue = [vat_series.get(month, (None, False))[0] for month in months]
    estimated = any(vat_series.get(month, (None, False))[1] for month in months)

    has_revenue = any(value is not None for value in revenue)
    values_for_unit = list(debt)
    if has_revenue:
        values_for_unit += [value for value in revenue if value is not None]
    divisor, unit = pick_unit(values_for_unit)

    columns = [DEBT_CHART_COLUMNS[0]]
    series_values = [[value / divisor for value in debt]]
    if has_revenue:
        columns.append(DEBT_CHART_COLUMNS[1])
        series_values.append(
            [None if value is None else value / divisor for value in revenue]
        )

    title = (
        DEBT_CHART_TITLE if has_revenue else DEBT_CHART_TITLE_DEBT_ONLY
    )
    return (
        build_linechart_block(
            title=title,
            unit=unit,
            columns=columns,
            labels=months,
            series=series_values,
            note=VAT_ESTIMATE_NOTE if estimated else "",
        ),
        title,
    )


def _build_cic_s10a_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted CIC S10A records for the prompt."""

    return _render_structured_records(
        selected, "is_cic_s10a", "cic_s10a_extraction",
        CIC_S10A_BLOCK_HEADING,
        [system_prompt("block_cic_s10a").strip()],
    )


def _build_cic_r20_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted CIC R20 collateral records for the prompt."""

    return _render_structured_records(
        selected, "is_cic_r20", "cic_r20_extraction",
        CIC_R20_BLOCK_HEADING,
        [system_prompt("block_cic_r20").strip()],
    )


def _build_ledger_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the detail-ledger record for the prompt."""

    records = []
    for doc in selected:
        if doc.is_ledger and doc.ledger_extraction and not any(
            record is doc.ledger_extraction for record in records
        ):
            records.append(doc.ledger_extraction)
    if not records:
        return ""

    parts = [
        LEDGER_BLOCK_HEADING,
        system_prompt(
            "block_ledger", residual_label=ledger_extraction.RESIDUAL_LABEL
        ).strip(),
    ]
    for record in records:
        parts.append(
            ledger_extraction.render_record(
                ledger_extraction.fit_to_budget(record)
            )
        )
    return "\n\n".join(parts)


def _build_tool_result_block(
    query_tool: Any,
    record: dict[str, Any],
    provenance: str = "",
) -> str:
    """Render one reference-data tool's result for the prompt."""

    if not record:
        return ""
    extras = getattr(query_tool, "extras", None) or {}
    parts = [extras.get("heading") or f"[{query_tool.name}]"]
    if provenance:
        parts.append(provenance)
    if query_tool.description:
        parts.append(query_tool.description.strip())
    parts.append(ledger_extraction.render_record(record))
    return "\n\n".join(parts)


def _build_sitevisit_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted site-visit report for the prompt."""

    return _render_structured_records(
        selected, "is_sitevisit", "sitevisit_extraction",
        SITEVISIT_BLOCK_HEADING,
        [system_prompt("block_sitevisit").strip()],
    )


def _build_financial_statement_block(
    selected: list[ClassifiedDocument],
    statements: tuple[str, ...] | None = None,
) -> str:
    """Render the extracted BCTC records, optionally trimmed to some statements."""

    financial_statement_docs = [
        doc for doc in selected if doc.is_financial_statement and doc.financial_statement_extraction
    ]
    if not financial_statement_docs:
        return ""
    parts = [FINANCIAL_STATEMENT_BLOCK_HEADING]
    for doc in financial_statement_docs:
        extraction = {
            key: value
            for key, value in (doc.financial_statement_extraction or {}).items()
            if key in financial_statement_extraction.BLOCK_KEYS
        }
        if statements is not None:
            extraction = {
                key: value
                for key, value in extraction.items()
                if key in statements
            }
        if not extraction:
            continue
        parts.append(
            f"--- {doc.filename} ---\n"
            + ledger_extraction.render_record(extraction)
        )
    return "\n\n".join(parts) if len(parts) > 1 else ""
