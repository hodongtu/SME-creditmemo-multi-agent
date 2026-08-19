"""The data blocks that go into a specialist's prompt, lifted out of Supervisor.

The eleven functions here take documents and return a piece of text. None reads
Supervisor state and none calls back into it, which is why this group could move
while the rest of supervisor.py could not: the graph nodes, the routing and the
extraction dispatch all share ``self.config``, the extraction chains and
``self.guardrails``, so splitting those would only produce modules that exist to
pass ``self`` back and forth.

The routing tables (``BCTC_JSON_STATEMENTS``, ``CIC_S10A_JSON_AGENTS``, …) stay
on Supervisor, because they decide which agent reads what; ``_build_user_input``
passes the relevant slice in as an argument. Only the constants these blocks
alone use came along.

Supervisor rebinds each function under its original name, so every existing call
site — ``self._build_...``, ``Supervisor._build_...`` — still works untouched.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.agents.cic_s10a_extraction import merge_debt_series
from src.agents.credit_need_calculator import build_credit_need_table
from src.agents.financial_ratio_calculator import (
    FinancialRatioCalculator,
    # Money in the credit-need table must read exactly like money in the
    # metrics block — same scale, same separators. Sharing the formatter is
    # what stops the two drifting apart again.
    _format_number,
)
from src.agents.industry_knowledge import (
    load_industry_manifest,
    load_industry_reference_text,
    select_industry,
)
from src.matrix.document_matrix import get_type
from src.types import ClassifiedDocument, truncate_text
from src.agents.vat_revenue import parse_vat_revenue_block
from src.utils.charts import build_linechart_block, pick_unit
from src.utils.source_list import build_source_lines



def _format_credit_need_value(value: float | None, unit: str) -> str:
    """One cell of the credit-need table.

    Money goes through the metrics block's own formatter (đồng -> tỷ VNĐ,
    Vietnamese separators). The other two units do not: percentages in this
    table are already on a 0-100 scale, and _format_number would multiply them
    by 100 again.
    """

    if value is None:
        return ""
    if unit == "VNĐ":
        return _format_number(value, "value")
    if unit == "%":
        return f"{value:,.1f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{value:,.0f}".replace(",", ".")

# The deterministic ratio block goes to the agents that reason about the
# figures: the financial agent computes from them, the risk agent judges
# leverage and debt-service against them.
# Credit proposal is here because its guidance asks it to tie the limit and
# the repayment source to working-capital turnover — the cash conversion
# cycle and inventory days the calculator already produces. Without the
# block it would be deriving those from raw line items, which is the
# arithmetic the calculator exists to take off it.
METRICS_BLOCK_AGENTS = (
    "FINANCIAL_ANALYSIS_AGENT",
    "RISK_ASSESSMENT_AGENT",
    "CREDIT_PROPOSAL_AGENT",
)

# Who gets the computed credit-need table. Credit proposal alone: the table
# *is* its report, while for anyone else it would be a second opinion on
# figures they were not asked to produce.
CREDIT_NEED_BLOCK_AGENTS = ("CREDIT_PROPOSAL_AGENT",)
CREDIT_NEED_BLOCK_HEADING = "[BẢNG TÍNH NHU CẦU TÍN DỤNG]"
# Heading of the CIC blocks. Named constants because two things have to
# agree on the exact string: the block itself, and the pointer that replaces
# the document's raw OCR.
CIC_S10A_BLOCK_HEADING = "[EXTRACTED CIC S10A REPORT]"
CIC_R21_BLOCK_HEADING = "[EXTRACTED CIC R21 REPORT]"
SITEVISIT_BLOCK_HEADING = "[EXTRACTED SITE VISIT REPORT]"
# The debt/revenue chart inserted into the credit-relationship section.
DEBT_CHART_TITLE = "Diễn biến dư nợ và doanh thu VAT 12 tháng gần nhất"
# Used when the credit-relationship agent produced no VAT revenue block, so
# the chart carries the debt column alone. A title naming a series the chart
# does not draw reads as missing data rather than data that was never claimed.
DEBT_CHART_TITLE_DEBT_ONLY = "Diễn biến dư nợ 12 tháng gần nhất"
DEBT_CHART_COLUMNS = ("Tổng dư nợ (CIC)", "Doanh thu VAT")
VAT_ESTIMATE_NOTE = (
    "Một số tháng là số ước lượng, chia đều từ doanh thu khai theo quý."
)
# Own fixed budget, not part of the weighted per-document pool: this is
# reference material, not case evidence, so it should not compete with —
# or be crowded out by — the documents the matrix actually routed here.
INDUSTRY_KNOWLEDGE_CHAR_BUDGET = 8_000
# How much of the case's own evidence the industry-selection LLM call
# gets to read. Small on purpose: picking 1-of-30 from a short catalogue
# needs a signal of what business the customer is in, not the full case.
INDUSTRY_EVIDENCE_EXCERPT_CHARS = 4_000
SOURCE_LIST_BLOCK_HEADING = "[DANH SÁCH NGUỒN — CHÉP NGUYÊN VĂN]"


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
            # Naming the document *type* tells the agent what it is holding
            # (a VAT return reads very differently from a bank statement);
            # the agent list alone would not.
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
        [(doc.filename, doc.document_type) for doc in documents]
    )
    if not lines:
        return ""
    return "\n".join(
        [
            SOURCE_LIST_BLOCK_HEADING,
            # Named by section rather than by placeholder: the two prompt
            # paths disagree about braces — create_agent passes the system
            # prompt through raw so the template still reads {{TenFile}},
            # while the direct chain renders it down to {TenFile}. Pointing
            # at the field by its heading matches whichever the agent got.
            "Danh sách dưới đã được hệ thống lập sẵn từ đúng những tài liệu "
            "bạn đang đọc. CHÉP NGUYÊN VĂN vào trường \"Hồ sơ\"/\"Nguồn "
            "dữ liệu\" ở đầu báo cáo, mỗi dòng dưới đây là MỘT dòng con. "
            "TUYỆT ĐỐI không gom thêm, không rút gọn thêm, không bỏ dòng "
            "nào, không đổi thứ tự.",
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
        payload = [asdict(doc) for doc in usable]
        yearly_metrics = calculator.extract_yearly_metrics(payload)
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
        return f"[BẢNG TÍNH NHU CẦU TÍN DỤNG unavailable: {exc}]"

    if not table.rows:
        return ""
    lines = [
        CREDIT_NEED_BLOCK_HEADING,
        "Bảng dưới đã được hệ thống TÍNH SẴN bằng công thức cố định. Dùng "
        "thẳng các con số này, TUYỆT ĐỐI không tự tính lại từ số liệu thô.",
        "ĐƠN VỊ: các dòng tiền ghi bằng **tỷ VNĐ** (giống khối "
        "[PRE-COMPUTED FINANCIAL METRICS]); dòng ghi % và ngày giữ nguyên "
        "đơn vị của nó. Giữ đúng đơn vị này khi trình bày.",
        "CỘT \"Nguồn\" cho biết con số đến từ đâu và BẮT BUỘC phải nêu lại "
        "khi diễn giải: \"mặc định\" nghĩa là hồ sơ KHÔNG nêu và hệ thống "
        "dùng tỷ lệ chính sách — không được trình bày như số liệu của khách "
        "hàng. \"tính toán\" là suy ra từ các dòng khác trong chính bảng này.",
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
        lines.extend(f"CẢNH BÁO: {w}" for w in table.warnings)
    return "\n".join(lines)
def _build_proposal_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted credit application records.

    Amounts arrive already converted to đồng by the extraction pass, and the
    unit each figure was read in is kept in ``source_unit`` — the form mixes
    đồng, triệu đồng and tỷ đồng between adjacent tables, so the note says so
    rather than leaving the agent to infer it.
    """

    proposal_docs = [
        doc for doc in selected if doc.is_proposal and doc.proposal_extraction
    ]
    if not proposal_docs:
        return ""
    parts = [
        "[DỮ LIỆU ĐỀ NGHỊ CẤP TÍN DỤNG]",
        "Trích xuất từ mục B (phương án sử dụng vốn, kế hoạch kinh doanh, "
        "hiệu quả, phương án trả nợ), mục C (tài sản bảo đảm) và mục D "
        "(đề nghị cấp tín dụng) của giấy đề nghị.",
        "Mọi số tiền đã quy về ĐỒNG (VNĐ). Trường \"source_unit\" là đơn vị "
        "ghi trên bản gốc, chỉ để đối chiếu — không nhân/chia lại lần nữa.",
    ]
    for doc in proposal_docs:
        parts.append(
            f"--- {doc.filename} ---\n"
            + json.dumps(doc.proposal_extraction, ensure_ascii=False, indent=2)
        )
    return "\n\n".join(parts)
def _build_industry_knowledge_block(
    config: Any,
    usable: list[ClassifiedDocument],
    input_text: str,
    target_agent: str,
) -> str:
    """This case's evidence -> one matching industry reference deck, or "".

    The deck itself was converted once, offline, by
    scripts/ingest_industry_knowledge.py; this only picks which one (see
    industry_knowledge.select_industry — one small LLM call choosing an id
    from the fixed catalogue, same shape as the document-type classifier's
    LLM fallback) and loads its cached text. No manifest yet, no LLM match,
    or no cached text for the matched id all fall through to "" — a case
    that doesn't clearly fit one of the ~30 industries simply gets no
    reference block, same "no data, drop the section" rule the report
    template follows everywhere else.
    """

    manifest = load_industry_manifest()
    if not manifest:
        return ""

    excerpt_parts = [input_text]
    for doc in usable:
        if doc.agent_relevance.get(target_agent) == "R":
            excerpt_parts.append(doc.content)
    excerpt = truncate_text(
        "\n\n".join(part for part in excerpt_parts if part),
        INDUSTRY_EVIDENCE_EXCERPT_CHARS,
    )

    industry_id = select_industry(config.document_llm, excerpt, manifest)
    if not industry_id:
        return ""

    text = load_industry_reference_text(industry_id)
    if not text:
        return ""

    display_name = next(
        (item["display_name"] for item in manifest if item["id"] == industry_id),
        industry_id,
    )
    return (
        f"--- Reference Document filename: {display_name}.pptx (tài liệu "
        "tham khảo ngành, không phải hồ sơ khách hàng) ---\n"
        + truncate_text(text, INDUSTRY_KNOWLEDGE_CHAR_BUDGET)
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

    # This chart is the credit-relationship section's content, and every
    # caller hands over the *unfiltered* document list — so without this
    # gate any run that merely had a CIC S10A file in the folder grew a debt
    # chart, including single BUSINESS_ACTIVITY / FINANCIAL / CREDIT_PROPOSAL
    # / RISK runs and even plain conversation answers. Those reports have no
    # credit-relationship heading, so it landed at the very end of the page.
    # Keyed on the agent's output rather than a route name because that is
    # what the revenue series is read from below: the same condition that
    # makes a chart meaningful also makes it complete.
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

    vat_series = parse_vat_revenue_block(
        sub_agent_outputs.get("CREDIT_RELATIONSHIP_AGENT", "")
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

    cic_docs = [
        doc for doc in selected if doc.is_cic_s10a and doc.cic_s10a_extraction
    ]
    if not cic_docs:
        return ""
    parts = [
        CIC_S10A_BLOCK_HEADING,
        "Trích xuất từ Báo cáo chi tiết quan hệ tín dụng CIC (mã phiếu S10A): "
        "dư nợ hiện tại theo từng TCTD, diễn biến dư nợ 12 tháng gần nhất, "
        "cam kết ngoại bảng, xếp hạng tín dụng và lịch sử cảnh báo.",
        "ĐƠN VỊ: các trường VNĐ (\"vnd\", \"du_no_vay\", \"du_no_the\", "
        "\"tong_du_no\") đã quy về ĐỒNG. Các trường ngoại tệ (\"ngoai_te\") "
        "giữ NGUYÊN TỆ theo bản gốc — không quy đổi, không cộng với cột VNĐ.",
        "Dư nợ trong \"du_no_12_thang\" ĐÃ bao gồm dư nợ ngoại tệ quy đổi; "
        "không cộng thêm số ngoại tệ ở khối khác vào, sẽ thành tính hai lần.",
        "Giá trị null nghĩa là kỳ đó thiếu số liệu báo cáo — KHÔNG phải bằng 0.",
    ]
    for doc in cic_docs:
        parts.append(
            f"--- {doc.filename} ---\n"
            + json.dumps(doc.cic_s10a_extraction, ensure_ascii=False, indent=2)
        )
    return "\n\n".join(parts)
def _build_cic_r21_structured_block(
    selected: list[ClassifiedDocument],
) -> str:
    """Render the extracted CIC R20/R21 collateral records for the prompt."""

    cic_docs = [
        doc for doc in selected if doc.is_cic_r21 and doc.cic_r21_extraction
    ]
    if not cic_docs:
        return ""
    parts = [
        CIC_R21_BLOCK_HEADING,
        "Trích xuất từ Báo cáo thông tin bảo đảm tiền vay CIC (mã phiếu "
        "R20/R21): danh sách tổ chức tín dụng đang nhận bảo đảm và chi "
        "tiết từng tài sản bảo đảm theo tổ chức tín dụng đó.",
        "ĐƠN VỊ: trường \"gia_tri_trieu_vnd\" đã quy về ĐỒNG dù tên trường "
        "vẫn giữ nguyên (đơn vị gốc trên giấy là triệu đồng).",
        "\"loai_tai_san\" là MÃ SỐ hai chữ số của CIC (vd \"08\"), không "
        "phải nhãn mô tả — báo cáo không kèm bảng chú giải mã, không tự "
        "suy diễn ý nghĩa mã này.",
        "\"ngay_giai_chap\" là null nghĩa là tài sản CHƯA giải chấp (vẫn "
        "đang thế chấp), không phải thiếu dữ liệu.",
        "Một khối có \"mo_ta_tai_san\": \"Không có bảo đảm tiền vay bằng "
        "tài sản\" nghĩa là tổ chức tín dụng đó xác nhận KHÔNG nhận tài "
        "sản bảo đảm nào — đây là thông tin có thật, không phải lỗi.",
    ]
    for doc in cic_docs:
        parts.append(
            f"--- {doc.filename} ---\n"
            + json.dumps(doc.cic_r21_extraction, ensure_ascii=False, indent=2)
        )
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

    sitevisit_docs = [
        doc for doc in selected if doc.is_sitevisit and doc.sitevisit_extraction
    ]
    if not sitevisit_docs:
        return ""
    parts = [
        SITEVISIT_BLOCK_HEADING,
        "Trích xuất từ Báo cáo khảo sát thực địa: thông tin cuộc khảo sát, "
        "ngành nghề và mã GSO, sản phẩm/dịch vụ chính, đầu vào - đầu ra, "
        "kế hoạch kinh doanh năm tiếp theo, và kết luận của cán bộ khảo sát.",
        "ĐƠN VỊ: mọi số tiền trong \"business_plan_next_year\" đã quy về "
        "ĐỒNG. Trường \"source_unit\" ghi đơn vị gốc in trên báo cáo.",
        "QUAN TRỌNG - khối \"conclusion\" (overall_assessment, risks_noted, "
        "recommendation, conditions) là Ý KIẾN CHỦ QUAN của cán bộ khảo "
        "sát, KHÔNG phải dữ kiện đo được. Được dùng làm tham khảo và phải "
        "nói rõ là nhận định của cán bộ khảo sát khi nhắc tới; TUYỆT ĐỐI "
        "không trích dẫn như dữ kiện đọc từ hồ sơ.",
        "\"gso_code\" là null nghĩa là báo cáo không in mã ngành GSO - "
        "không tự tra cứu hay suy ra từ tên ngành.",
        "Các khối còn lại là quan sát tại chỗ: dùng để đối chiếu với số "
        "liệu trên BCTC, chênh lệch giữa hai nguồn là thông tin đáng nêu.",
    ]
    for doc in sitevisit_docs:
        parts.append(
            f"--- {doc.filename} ---\n"
            + json.dumps(doc.sitevisit_extraction, ensure_ascii=False, indent=2)
        )
    return "\n\n".join(parts)
def _build_bctc_structured_block(
    selected: list[ClassifiedDocument],
    statements: tuple[str, ...] | None = None,
) -> str:
    """Render the extracted BCTC records, optionally trimmed to some statements.

    ``statements=None`` emits the whole record. Narrowing it keeps an agent
    that only reasons about one statement from spending its entire character
    budget on the other two — see BCTC_JSON_STATEMENTS.
    """

    bctc_docs = [
        doc for doc in selected if doc.is_bctc and doc.bctc_extraction
    ]
    if not bctc_docs:
        return ""
    parts = ["[DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT]"]
    for doc in bctc_docs:
        extraction = doc.bctc_extraction
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
