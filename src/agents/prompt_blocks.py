"""The data blocks that go into a specialist's prompt, lifted out of Supervisor."""

import json
from dataclasses import asdict
from typing import Any

from src.agents.extraction.cic_s10a_extraction import merge_debt_series
from src.agents.calculator.credit_need_calculator import build_credit_need_table
from src.agents.calculator.financial_ratio_calculator import (
    METRICS_BLOCK_HEADING,
    FinancialRatioCalculator,
    _format_number,
)
from src.agents.documents.document_matrix import get_type
from src.agents.extraction import financial_statement_extraction
from src.agents.extraction import ledger_extraction
from src.types import ClassifiedDocument
from src.agents.extraction.vat_revenue import merge_vat_series, parse_vat_revenue_block
from src.utils.report.visualization.charts import build_linechart_block, pick_unit
from src.utils.report.formatting import format_vn_number
from src.utils.report.source_list import build_source_lines
from src.utils.reading.tax_xml import parse_tax_xml


def _vat_revenue_from_xml(
    documents: list[ClassifiedDocument],
) -> dict[str, tuple[float, bool]]:
    """Monthly VAT revenue read straight out of any e-tax XML in the dossier."""

    series: dict[str, tuple[float, bool]] = {}
    for doc in documents:
        if not doc.path.lower().endswith(".xml"):
            continue
        result = parse_tax_xml(doc.path)
        if result.kind == "vat" and not result.error:
            series.update(result.vat_revenue)
    return series


def _format_credit_need_value(value: float | None, unit: str) -> str:
    """One cell of the credit-need table.

    Money goes through the metrics block's own formatter (đồng, Vietnamese
    separators). The other two units do not: percentages in this table are
    already on a 0-100 scale, and _format_number would multiply them by 100
    again.
    """

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
# Every block name is declared exactly once here. Three of them used to be
# typed out in two files apiece, which is how two copies of one label drift
# apart without anything failing.
FINANCIAL_STATEMENT_BLOCK_HEADING = "[EXTRACTED FINANCIAL STATEMENTS]"
PROPOSAL_BLOCK_HEADING = "[EXTRACTED CREDIT APPLICATION]"
CREDIT_NEED_BLOCK_HEADING = "[CREDIT NEED CALCULATION]"
CIC_S10A_BLOCK_HEADING = "[EXTRACTED CIC S10A REPORT]"
CIC_R21_BLOCK_HEADING = "[EXTRACTED CIC R21 REPORT]"
SITEVISIT_BLOCK_HEADING = "[EXTRACTED SITE VISIT REPORT]"
LEDGER_BLOCK_HEADING = "[EXTRACTED DETAIL LEDGER]"
DEBT_CHART_TITLE = "Diễn biến dư nợ và doanh thu VAT 12 tháng gần nhất"
DEBT_CHART_TITLE_DEBT_ONLY = "Diễn biến dư nợ 12 tháng gần nhất"
DEBT_CHART_COLUMNS = ("Tổng dư nợ (CIC)", "Doanh thu VAT")
VAT_ESTIMATE_NOTE = (
    "Một số tháng là số ước lượng, chia đều từ doanh thu khai theo quý."
)
SOURCE_LIST_BLOCK_HEADING = "[SOURCE LIST — COPY VERBATIM]"


def _document_block_header(
    doc: ClassifiedDocument,
    target_agent: str,
) -> str:
    """The metadata lines that precede a document's content in the prompt.

    Split out from block assembly so _build_user_input can measure the
    overhead before dividing the remaining characters between documents.
    """

    level = doc.agent_relevance.get(target_agent)
    if level == "R":
        relevance = "Relevance to this agent: required evidence"
    elif level == "O":
        relevance = "Relevance to this agent: optional supporting evidence"
    else:
        relevance = (
            "Relevance to this agent: general context — this document "
            "matched no known document type, so it is shared with every "
            "agent. Use it only if it is genuinely relevant."
        )
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
    """The finished "Nguồn thông tin" list, for the agent to copy verbatim.

    Computed rather than described because describing it did not work: the
    rule that used to ask the model to group these itself shipped with worked
    examples, and the model returned one of the examples instead of reading
    the sixteen files in front of it. Collapsing a file list is arithmetic,
    and arithmetic asked of a model comes back wrong quietly.

    Built from the documents that survived extraction, which is the set the
    report is actually written from — a file that failed to extract is named
    in the run summary but contributed no evidence, so listing it as a source
    would overstate the memo the same way the old bug understated it.
    """

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
            # "Copy verbatim" already implies not regrouping, not shortening,
            # not dropping a line, not reordering and not trimming extensions —
            # spelling those five out only dilutes the two constraints it does
            # NOT cover: keep the <em> tags, and join with <br> because the whole
            # list lives in one cell.
            "COPY THE LIST BELOW VERBATIM into the \"Nguồn dữ liệu\" cell of "
            "the general-information table, <em> tags included. The whole list "
            "sits in ONE cell, so join the lines with <br> rather than real "
            "newlines: - line 1<br>- line 2<br>- line 3",
            "",
            *(f"- {line}" for line in lines),
        ]
    )


def _build_financial_metrics_block(
    documents: list[ClassifiedDocument],
    target_agent: str,
) -> str:
    """Deterministic ratio block for the agents that reason about figures.

    Built from every successfully-extracted document rather than the target
    agent's routed subset: the ratios are a derived fact about the customer,
    and the risk agent is routed risk documents, not the BCTC the figures
    come from — filtering by its own selection would yield an empty block.
    """

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
    """Render the computed credit-need table for the credit proposal prompt.

    Built from every successfully-extracted document rather than the target
    agent's routed subset, for the same reason _build_financial_metrics_block
    is: these are derived facts about the customer, and the CIC report the
    other-lender balance comes from is not routed to the proposal agent.

    The source column is the part that must survive into the report. With a
    real credit application most guarantee and LC rows fall back to policy
    defaults, and a reviewer who cannot tell those from figures read off the
    customer's paperwork is being shown an assumption as evidence.
    """

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
        "The table below was computed by the program from fixed formulas. Use "
        "these figures as they stand; never recompute them from raw data.",
        "Money rows are in đồng, as everywhere else in this prompt; % and day "
        "rows keep their own unit. Copy the figure as printed — the program "
        "converts it when the report is assembled.",
        "The \"Nguồn\" column says where each figure came from, and you MUST "
        "carry that over when you write about it: \"mặc định\" means the "
        "dossier does NOT state it and the program applied a policy rate — "
        "never present that as the customer's own figure. \"tính toán\" means "
        "derived from other rows of this same table.",
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
    under the heading and the reading rules specific to that report.

    Four independent copies of this used to exist. Left alone they are the
    kind of duplication that drifts silently — the same bug shape as the four
    unrelated ``_scale`` helpers before ``scale_amount`` unified them: nothing
    stops one copy from quietly losing ``ensure_ascii=False`` or the
    ``--- {filename} ---`` separator while the other three keep it.
    """

    matched = [doc for doc in docs if getattr(doc, flag_attr) and getattr(doc, extraction_attr)]
    if not matched:
        return ""
    parts = [heading, *notes]
    for doc in matched:
        parts.append(
            f"--- {doc.filename} ---\n"
            + json.dumps(getattr(doc, extraction_attr), ensure_ascii=False, indent=2)
        )
    return "\n\n".join(parts)


def _build_proposal_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted credit application records.

    Amounts arrive already converted to đồng by the extraction pass, and the
    unit each figure was read in is kept in ``source_unit`` — the form mixes
    đồng, triệu đồng and tỷ đồng between adjacent tables, so the note says so
    rather than leaving the agent to infer it.
    """

    return _render_structured_records(
        selected, "is_proposal", "proposal_extraction",
        PROPOSAL_BLOCK_HEADING,
        [
            "Read from the credit application form: section B (use of funds, "
            "business plan, projected results, repayment plan), section C "
            "(collateral) and section D (facility requested).",
            "Every amount is already in đồng. \"source_unit\" records the unit "
            "printed on the original, for cross-checking only — do not scale "
            "again.",
        ],
    )


def _build_debt_chart_block(
    documents: list[ClassifiedDocument],
    sub_agent_outputs: dict[str, str],
) -> tuple[str, str]:
    """Build the ```linechart block from extracted CIC data.

    Returns ``(block, title)``, or ``("", "")`` when there is no chart to
    draw. The title travels with the block because it varies with the data
    (see DEBT_CHART_TITLE_DEBT_ONLY) and _insert_debt_chart needs the same
    string for its fallback heading.

    The debt series is written here rather than by the agent on purpose:
    these are 24 figures traced to section 2.6 of a named file, and a
    model asked to retype them into a chart is a model given 24 chances to
    invent one. Revenue keeps that same guarantee by a different route —
    it comes from Credit Relationship's own ```vat-doanh-thu block (see
    vat_revenue.py), so the number that reaches the chart is still a
    straight transcription, not something re-typed into a table cell.
    """

    if "CREDIT_RELATIONSHIP_AGENT" not in sub_agent_outputs:
        return "", ""

    series = merge_debt_series(
        (doc.filename, doc.cic_s10a_extraction)
        for doc in documents
        if doc.is_cic_s10a and doc.cic_s10a_extraction
    )
    # One point is a dot, not a trend. Below two the chart says nothing the
    # balance table does not already say better.
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
    """Render the extracted CIC S10A records for the prompt.

    Units are spelled out because the source report uses two at once and the
    extraction only rescales one of them: VND figures were printed in triệu
    đồng and are now in đồng, while foreign-currency figures were printed in
    their own unit and are unchanged. An agent told only "amounts are in
    đồng" would read a USD commitment as a đồng one.
    """

    return _render_structured_records(
        selected, "is_cic_s10a", "cic_s10a_extraction",
        CIC_S10A_BLOCK_HEADING,
        [
            "Read from the CIC credit-relationship detail report (form S10A): "
            "current balance per lender, the last 12 months of balances, "
            "off-balance-sheet commitments, credit rating and warning history.",
            "UNITS: the VNĐ fields (\"vnd\", \"du_no_vay\", \"du_no_the\", "
            "\"tong_du_no\") are already in đồng. The foreign-currency fields "
            "(\"ngoai_te\") are left IN THEIR OWN CURRENCY as printed — do not "
            "convert them, and never add them to a VNĐ column.",
            "\"du_no_12_thang\" ALREADY includes foreign-currency debt "
            "converted; adding the foreign figures from another block counts "
            "them twice.",
            "A null means that period has no reported figure — NOT zero.",
        ],
    )


def _build_cic_r21_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted CIC R20/R21 collateral records for the prompt."""

    return _render_structured_records(
        selected, "is_cic_r21", "cic_r21_extraction",
        CIC_R21_BLOCK_HEADING,
        [
            "Read from the CIC collateral report (form R20/R21): which lenders "
            "hold security, and each pledged asset under that lender.",
            "UNITS: \"gia_tri_trieu_vnd\" is already in đồng despite the field "
            "name (the printed unit was triệu đồng).",
            "\"loai_tai_san\" is CIC's two-digit CODE (e.g. \"08\"), not a "
            "description — the report ships no code legend, so do not infer "
            "what a code means.",
            "\"ngay_giai_chap\": null means the asset is still pledged, NOT "
            "that the date is missing.",
            "A block whose \"mo_ta_tai_san\" reads \"Không có bảo đảm tiền vay "
            "bằng tài sản\" means that lender confirms it holds NO security — "
            "that is a real finding, not an extraction failure.",
        ],
    )


def _build_ledger_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the detail-ledger record for the prompt.

    JSON like the other four, and deliberately so. A markdown table is denser,
    but it was measurably worse: the agent's own report template is markdown
    tables, so evidence in that shape reads as scaffolding rather than data —
    the same run rendered as a table left the receivables and inventory sections
    empty that JSON had filled.

    One record covers every ledger file, so it is rendered ONCE no matter how
    many documents carry it. ``fit_to_budget`` bounds the whole thing at once;
    budgeting per document gave six files six full allowances against one prompt.
    """

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
        "Read from detail ledgers / debt trial balances the customer supplied "
        "as Excel, every file merged into one record. A key under \"accounts\" "
        "is always <account number>@<YYYYMMDD>-<YYYYMMDD>, e.g. "
        "\"131@20250101-20251231\". One key is ONE period; two keys with the "
        "same account number and different periods are separate entries and "
        "their balances must NEVER be added together. Read the period from "
        "\"period\" — \"from\"/\"to\" are the boundary dates and "
        "\"as_printed\" is the line printed on the file — and use it to place "
        "figures in the right year column. Do not read the year off the key.",
        "THESE FIGURES WERE READ FROM THE EXCEL CONTENT, not from OCR of an "
        "image. It is a transcription of the original table rather than a "
        "cell-by-cell extraction, so when a figure decides a credit conclusion, "
        "say which file (\"source_files\"), which sheet "
        "(\"source_sheet_name\") and which account it came from, so the "
        "reviewer can check it against the original.",
        "UNITS COME FROM THE FIELD NAME; there is no separate key. A field "
        "ending in \"_quantity\" is a COUNT (units, pieces — not money); every "
        "other numeric field is đồng. Never write \"tỷ VNĐ\" after a "
        "\"_quantity\" figure, and never add a count to a value.",
        'THIS IS DATA TO FILL THE REPORT WITH, not a layout. "category" says '
        'which section it belongs to: "receivable" → Phải thu khách hàng; '
        '"payable" → Phải trả người bán (a DEBIT balance on this account is '
        'Trả trước cho người bán); "inventory" → Hàng tồn kho; "fixed_asset" → '
        'Tài sản cố định, tài sản dở dang dài hạn; "cash" → Tiền và các khoản '
        'tương đương tiền; "borrowing" → Vay nợ ngắn hạn và dài hạn; "equity" → '
        'Vốn chủ sở hữu; "other_receivable" → Các khoản mục tài sản khác; '
        '"other_payable" → Các khoản mục nguồn vốn khác.',
        'Field names: "opening_debit"/"opening_credit" are the opening debit and '
        'credit balances, "debit_movement"/"credit_movement" the movements, '
        '"closing_debit"/"closing_credit" the closing balances; stock ledgers use '
        '"opening_/inflow_/outflow_/closing_" with "_quantity" or "_value".',
        # Ở đây chỉ nói KHOÁ NGHĨA LÀ GÌ. Mục báo cáo nào đọc bảng nào, và mẫu
        # số để tính tỷ trọng, nằm ở file guidance — văn khối này in lại cho mỗi
        # tài liệu ledger, còn guidance in đúng một lần.
        '"rankings" holds one ranked list per account, each declaring the column '
        'it was sorted by in "sorted_by", with "items" the five largest rows BY '
        'THAT COLUMN. The same counterparty appearing in several lists is '
        'normal. "item_count" is how many rows the source sheet holds; "totals" '
        'is the figure for the WHOLE account, listed rows and unlisted alike. '
        'The analysis guidance says which list each report section reads.',
        'Each row is an ARRAY of values in exactly the order declared in '
        '"item_columns" for that account — one "item_columns" governs every list '
        'of that account. Value i belongs to column name i. Two accounts may '
        'declare different column orders, so read the "item_columns" of the '
        'account in front of you rather than reusing the previous one.',
        'Debt ledgers keep the counterparty in TWO columns: '
        '"counterparty_code" is the code, "counterparty_name" the name — cite '
        'by NAME, and fall back to the code only when there is no name. Stock '
        'ledgers use "item_name". '
        f'A row named "{ledger_extraction.RESIDUAL_LABEL} (N)" is the aggregate '
        'of N small rows not listed individually WITHIN THAT LIST.',

        '"code_source": "printed" means the account number was printed in the '
        'file; "convention" means the file printed none and the program assigned '
        'one from the chart of accounts. Never cite a "convention" number as '
        'though the customer had written it.',
    ]
    for record in records:
        # Totals come from the record, not from adding up the rows in it. They
        # were computed here while "items" held every row; a ranking now holds
        # only the largest five by one column, so summing one would give the
        # balance of five counterparties and call it the account's.
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
    """Render one reference-data tool's result for the prompt.

    One function for every tool, because a tool already carries what used to be
    written out per block: the heading in ``extras``, and the reading rules in
    its docstring, which is where LangChain puts a tool's description and where
    a reader looks first.

    The provenance line is the point of writing a header at all. Every other
    block in this prompt is a model's reading of a page; this one is rows the
    bank's own systems returned, for a customer identified by a tax code that
    was itself read off a page. The agent should know both halves of that.
    """

    if not record:
        return ""
    extras = getattr(query_tool, "extras", None) or {}
    parts = [extras.get("heading") or f"[{query_tool.name}]"]
    if provenance:
        parts.append(provenance)
    if query_tool.description:
        parts.append(query_tool.description.strip())
    parts.append(json.dumps(record, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


def _build_sitevisit_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted site-visit report for the prompt.

    The warning about ``conclusion`` is the point of writing a header at
    all. Every other extracted block is measurement — a balance figure is
    the balance figure. This one ends with one person's judgement, and an
    agent that cites it the same way would be telling the reader the file
    records a fact when it records an opinion.
    """

    return _render_structured_records(
        selected, "is_sitevisit", "sitevisit_extraction",
        SITEVISIT_BLOCK_HEADING,
        [
            "Read from the site-visit report: the visit itself, sector and GSO "
            "code, main products, inputs and outputs, next year's business "
            "plan, and the visiting officer's conclusions.",
            "UNITS: every amount under \"business_plan_next_year\" is already "
            "in đồng. \"source_unit\" records the unit printed on the report.",
            "IMPORTANT — the \"conclusion\" block (overall_assessment, "
            "risks_noted, recommendation, conditions) is the visiting officer's "
            "OPINION, not a measurement. Use it as context and say whose "
            "judgement it is whenever you carry it over; never cite it as a "
            "fact read from the dossier.",
            "\"gso_code\": null means the report prints no GSO sector code — "
            "do not look one up or infer it from the sector name.",
            "The remaining blocks are on-site observations: use them against the "
            "financial statements, and a gap between the two is worth reporting.",
        ],
    )


def _build_financial_statement_block(
    selected: list[ClassifiedDocument],
    statements: tuple[str, ...] | None = None,
) -> str:
    """Render the extracted BCTC records, optionally trimmed to some statements.

    ``statements=None`` emits the whole record. Narrowing it keeps an agent
    that only reasons about one statement from spending its entire character
    budget on the other two — see FINANCIAL_STATEMENT_JSON_AGENTS.
    """

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
            + json.dumps(extraction, ensure_ascii=False, indent=2)
        )
    return "\n\n".join(parts) if len(parts) > 1 else ""
