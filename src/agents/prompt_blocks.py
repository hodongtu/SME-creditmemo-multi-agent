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
from src.matrix.document_matrix import get_type
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
            "Danh sách dưới đã được hệ thống lập sẵn từ đúng những tài liệu "
            "bạn đang đọc. CHÉP NGUYÊN VĂN vào ô \"Nguồn dữ liệu\" của bảng "
            "Thông tin chung, giữ nguyên cả thẻ <em>. TUYỆT ĐỐI không gom "
            "thêm, không rút gọn thêm, không bỏ dòng nào, không đổi thứ tự, "
            "không bỏ đuôi tệp.",
            "Cả danh sách nằm trong MỘT ô, các dòng nối với nhau bằng <br> "
            "(không xuống dòng thật, vì xuống dòng sẽ phá vỡ bảng). Dạng đúng: "
            "- dòng 1<br>- dòng 2<br>- dòng 3",
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
        return f"{CREDIT_NEED_BLOCK_HEADING} unavailable: {exc}"

    if not table.rows:
        return ""
    lines = [
        CREDIT_NEED_BLOCK_HEADING,
        "Bảng dưới đã được hệ thống TÍNH SẴN bằng công thức cố định. Dùng "
        "thẳng các con số này, TUYỆT ĐỐI không tự tính lại từ số liệu thô.",
        "ĐƠN VỊ: các dòng tiền ghi bằng **ĐỒNG**, giống mọi khối dữ liệu khác "
        "trong prompt này; dòng ghi % và ngày giữ nguyên đơn vị của nó. Chép "
        "nguyên con số, chương trình tự quy đổi khi dựng báo cáo.",
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
            "Trích xuất từ mục B (phương án sử dụng vốn, kế hoạch kinh doanh, "
            "hiệu quả, phương án trả nợ), mục C (tài sản bảo đảm) và mục D "
            "(đề nghị cấp tín dụng) của giấy đề nghị.",
            "Mọi số tiền đã quy về ĐỒNG (VNĐ). Trường \"source_unit\" là đơn vị "
            "ghi trên bản gốc, chỉ để đối chiếu — không nhân/chia lại lần nữa.",
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
            "Trích xuất từ Báo cáo chi tiết quan hệ tín dụng CIC (mã phiếu S10A): "
            "dư nợ hiện tại theo từng TCTD, diễn biến dư nợ 12 tháng gần nhất, "
            "cam kết ngoại bảng, xếp hạng tín dụng và lịch sử cảnh báo.",
            "ĐƠN VỊ: các trường VNĐ (\"vnd\", \"du_no_vay\", \"du_no_the\", "
            "\"tong_du_no\") đã quy về ĐỒNG. Các trường ngoại tệ (\"ngoai_te\") "
            "giữ NGUYÊN TỆ theo bản gốc — không quy đổi, không cộng với cột VNĐ.",
            "Dư nợ trong \"du_no_12_thang\" ĐÃ bao gồm dư nợ ngoại tệ quy đổi; "
            "không cộng thêm số ngoại tệ ở khối khác vào, sẽ thành tính hai lần.",
            "Giá trị null nghĩa là kỳ đó thiếu số liệu báo cáo — KHÔNG phải bằng 0.",
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
        "Trích từ sổ chi tiết / bảng cân đối phát sinh công nợ khách hàng nộp "
        "dưới dạng Excel, gộp mọi file thành một bản: khoá của \"accounts\" là "
        "số hiệu tài khoản.",
        "SỐ LIỆU ĐỌC THẲNG TỪ Ô EXCEL, không qua OCR và không do mô hình nào "
        "chép lại — đúng nguyên văn con số trong file. \"units\" ghi đơn vị "
        "từng trường: \"vnd\" là ĐỒNG, \"quantity\" là số lượng.",
        'Báo cáo trình bày theo TỶ VNĐ. Khi chép một số "vnd" vào báo cáo, hãy '
        'GHI NGUYÊN SỐ ĐỒNG CÓ DẤU PHÂN CÁCH NGHÌN (ví dụ 225.510.140.846) và '
        'để chương trình tự quy đổi — đừng tự chia cho một tỷ. Một lượt chạy '
        'trước đã chia nhầm cho một triệu và biến 225,51 tỷ thành 225.510,14.',
        'ĐÂY LÀ DỮ LIỆU ĐỂ ĐIỀN VÀO BÁO CÁO, không phải khung mẫu. "category" '
        'cho biết điền vào mục nào của báo cáo: "receivable" → Phải thu khách '
        'hàng; "payable" → Phải trả người bán (số dư bên NỢ của tài khoản này '
        'là Trả trước cho người bán); "inventory" → Hàng tồn kho; '
        '"fixed_asset" → Tài sản cố định, tài sản dở dang dài hạn; "cash" → '
        'Tiền và các khoản tương đương tiền; "borrowing" → Vay nợ ngắn hạn và '
        'dài hạn; "equity" → Vốn chủ sở hữu; "other_receivable" → Các khoản '
        'mục tài sản khác; "other_payable" → Các khoản mục nguồn vốn khác.',
        'Tên trường: "opening_debit"/"opening_credit" là dư đầu kỳ bên nợ/có, '
        '"debit_movement"/"credit_movement" là phát sinh nợ/có, '
        '"closing_debit"/"closing_credit" là dư cuối kỳ; hàng tồn kho dùng '
        '"opening_/inflow_/outflow_/closing_" kèm "_quantity" hoặc "_value". '
        '"source_columns" cho biết mỗi trường ứng với cột nào trong file gốc.',
        '"items" là toàn bộ dòng chi tiết; cột nào trong file có giá trị 0 thì '
        'ghi 0 chứ không lược đi. Nếu có dòng tên '
        f'"{ledger_extraction.RESIDUAL_LABEL} (N)" thì đó là tổng gộp của N '
        'dòng nhỏ không liệt kê riêng, nên tổng các dòng luôn khớp "totals" — '
        'đừng cộng "items" rồi gọi đó là tổng khi đã có "totals".',
        '"code_source": "printed" nghĩa là số hiệu tài khoản in trong file; '
        '"convention" nghĩa là file không in số hiệu và chương trình xếp theo '
        'quy ước hệ thống tài khoản. Đừng trích dẫn số hiệu "convention" như '
        'thể khách hàng đã ghi nó.',
    ]
    for record in records:
        parts.append(
            json.dumps(
                ledger_extraction.fit_to_budget(record),
                ensure_ascii=False,
                indent=2,
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
        extraction = doc.financial_statement_extraction
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
