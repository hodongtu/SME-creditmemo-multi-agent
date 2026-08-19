"""Supervisor orchestration (extracted from the notebook cell 20).

Canonical AI-agent workflow: routing, document prep/classification, gap analysis,
memo workflow, finalize + tỷ-VNĐ formatting. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langgraph.graph import END, StateGraph

from src.agents.financial_ratio_calculator import (
    FinancialRatioCalculator,
    # Money in the credit-need table must read exactly like money in the
    # metrics block — same scale, same separators. Sharing the formatter is
    # what stops the two drifting apart again; they sat in one prompt showing
    # 220,05 and 220.050.000.000 for the same figure.
    _format_number,
)
from src.utils.common import normalize_text
from src.utils.extractors import extract_document_text
from src.utils.formatting import convert_amounts_in_text
from src.utils.template_leak import check_template_leakage
from src.utils.citations import (
    AGENT_LABEL_PREFIXES,
    FootnoteAudit,
    consolidate_footnotes,
    format_footnote_findings,
    namespace_footnotes,
)
from src.utils.markdown_fixups import ensure_blank_line_before_lists

from src.config import Config, shared_rate_limiter
from src.types import (
    AgentName,
    ClassifiedDocument,
    UnderwritingGraphState,
    WorkflowMode,
    extract_text_from_agent_output,
    to_dict_list,
    truncate_text,
)
from src.agents.document_classification import (
    VALID_DOCUMENT_AGENTS,
    build_document_classification_prompt,
    compute_file_hash,
    detect_loan_program,
    discover_documents,
    rule_classify_document,
)
from src.matrix.document_matrix import (
    agent_relevance_for_type,
    get_type,
    is_bctc_type,
    is_cic_r21_type,
    is_sitevisit_type,
    is_cic_s10a_type,
    is_proposal_type,
    primary_agent_for_type,
)
from src.agents.bctc_extraction import (
    build_bctc_extraction_chain,
    extract_bctc_structured_data,
)
from src.agents.proposal_extraction import (
    build_proposal_extraction_chain,
    extract_proposal_structured_data,
)
from src.agents.cic_s10a_extraction import (
    build_cic_s10a_extraction_chain,
    extract_cic_s10a_structured_data,
    merge_debt_series,
)
from src.agents.cic_r21_extraction import (
    build_cic_r21_extraction_chain,
    extract_cic_r21_structured_data,
)
from src.agents.sitevisit_extraction import (
    build_sitevisit_extraction_chain,
    extract_sitevisit_structured_data,
)
from src.agents.credit_need_calculator import build_credit_need_table
from src.agents import prompt_blocks
from src.agents.vat_revenue import parse_vat_revenue_block, strip_vat_revenue_block
from src.agents.industry_knowledge import (
    load_industry_manifest,
    load_industry_reference_text,
    select_industry,
)
from src.utils.charts import build_linechart_block, pick_unit
from src.utils.source_list import build_source_lines
from src.agents.specialist import (
    BusinessActivityAnalysis,
    CreditMemoComposerAgent,
    CreditProposalAnalysis,
    CreditRelationshipAnalysis,
    FinancialAnalysis,
    RiskAssessment,
)
from src.agents.guardrails import (
    LocalGuardrails,
    WebSearchProcessorAgent,
)

DECISION_SYSTEM_PROMPT = """
You are an expert for SME underwriting at a bank.
Your task is to serve as a supervisor/planner for a multi-agent team.

Available routes:
- FINANCIAL_ANALYSIS_AGENT: finance-related analysis, including financial
  statements, ledgers, VAT declarations, bank statements, receivables/payables,
  revenue, expenses, cash flow, assets, liabilities, and capital structure.
- BUSINESS_ACTIVITY_AGENT: business operation or business performance analysis.
- CREDIT_RELATIONSHIP_AGENT: credit relationship analysis using internal T24
  credit data and CIC/bureau data queried by tools.
- CREDIT_PROPOSAL_AGENT: credit proposal — requested facility, limit, tenor,
  repayment source, collateral and credit conditions.
- RISK_ASSESSMENT_AGENT: credit risk assessment only. It returns risk analysis,
  not a Credit Memo.
- CREDIT_MEMO: create, draft, prepare, or generate a full underwriting Credit
  Memo / báo cáo thẩm định.

Rules:
- If uploaded documents are mostly finance-related and the user does not specify
  a task, prefer FINANCIAL_ANALYSIS_AGENT.
- If uploaded documents are mostly business operation documents and the user does
  not specify a task, prefer BUSINESS_ACTIVITY_AGENT.
- If the user only asks about credit relationship, T24, CIC, bureau, existing
  facilities, outstanding balance, or repayment status, use CREDIT_RELATIONSHIP_AGENT.
- If the user only asks to analyze or calculate a credit proposal, credit
  facility proposal, loan amount proposal, or đề xuất cấp tín dụng, use
  CREDIT_PROPOSAL_AGENT.
- If the user asks for risk assessment, credit risk, approval view, or risk
  conclusion, use RISK_ASSESSMENT_AGENT.
- If the user explicitly asks to create, draft, prepare, or generate a Credit
  Memo or báo cáo thẩm định, use CREDIT_MEMO.
- CREDIT_MEMO must run the full underwriting workflow:
  BUSINESS_ACTIVITY_AGENT -> CREDIT_RELATIONSHIP_AGENT ->
  FINANCIAL_ANALYSIS_AGENT -> CREDIT_PROPOSAL_AGENT ->
  RISK_ASSESSMENT_AGENT -> CREDIT_MEMO.
- Return JSON only with route, reasoning, and confidence.
"""

# Where routing lands when nothing else decides: the decision LLM returned an id
# that is not a route, or no keyword matched. Named once so the choice is
# greppable rather than repeated as a literal at six call sites. Financial
# analysis because _fallback_route already prefers it whenever files are present,
# and because a request with documents but no stated task is far more often about
# the figures than anything else.
DEFAULT_ROUTE: AgentName = "FINANCIAL_ANALYSIS_AGENT"
DEFAULT_WORKFLOW_MODE: WorkflowMode = "single_financial_analysis"

# Answer for a request that carries no documents and asks for no analysis — a
# greeting, or a question about the tool itself. Written out rather than routed
# to an agent: there is nothing to analyse, so an LLM call could only produce
# chat, and this system is not a chatbot.
OUT_OF_SCOPE_RESPONSE = (
    "Hệ thống này chỉ thực hiện thẩm định tín dụng SME trên hồ sơ khách hàng.\n\n"
    "Để bắt đầu, vui lòng:\n"
    "- Cung cấp hồ sơ (BCTC, tờ khai thuế GTGT, sao kê, báo cáo CIC, "
    "giấy đề nghị cấp tín dụng…), và\n"
    "- Nêu rõ yêu cầu, ví dụ: *\"Lập báo cáo thẩm định cho khách hàng này\"* "
    "hoặc *\"Phân tích hoạt động kinh doanh\"*."
)

CREDIT_MEMO_KEYWORDS = [
    "credit memo",
    "báo cáo thẩm định",
    "báo cáo thẩm định tín dụng",
    "lập báo cáo thẩm định",
    "tạo báo cáo thẩm định",
    "soạn báo cáo thẩm định",
    "generate credit memo",
    "create credit memo",
    "draft credit memo",
    "prepare credit memo",
]
CREDIT_PROPOSAL_ROUTE_KEYWORDS = [
    "credit proposal",
    "credit facility proposal",
    "facility proposal",
    "loan proposal analysis",
    "proposed credit limit",
    "loan amount proposal",
    "phân tích đề xuất tín dụng",
    "phân tích đề xuất cấp tín dụng",
    "đề xuất tín dụng",
    "đề xuất cấp tín dụng",
    "báo cáo đề xuất cấp tín dụng",
    "hạn mức đề xuất",
    "số tiền đề xuất",
    "mức cấp tín dụng đề xuất",
]
RISK_ASSESSMENT_ROUTE_KEYWORDS = [
    "risk assessment",
    "credit risk",
    "risk analysis",
    "approval view",
    "underwriting conclusion",
    "đánh giá rủi ro",
    "rủi ro tín dụng",
    "thẩm định tín dụng",
    "nhận xét rủi ro",
    "phân tích rủi ro",
    "kết luận rủi ro",
    "phê duyệt",
    "khả năng trả nợ",
]
FINANCIAL_ROUTE_KEYWORDS = [
    "financial analysis",
    "financial",
    "finance",
    "phân tích tài chính",
    "tài chính",
    "bctc",
    "báo cáo tài chính",
    "sổ chi tiết",
    "sổ cái",
    "tờ khai thuế",
]
BUSINESS_ROUTE_KEYWORDS = [
    "business activity",
    "business operation",
    "hoạt động kinh doanh",
    "nhà cung cấp",
    "khách hàng",
    "đầu ra",
    "đầu vào",
    "hợp đồng",
]
CREDIT_RELATIONSHIP_ROUTE_KEYWORDS = [
    "credit relationship",
    "credit history",
    "bureau",
    "cic",
    "t24",
    "facility",
    "outstanding balance",
    "repayment status",
    "overdue",
    "quan hệ tín dụng",
    "lịch sử tín dụng",
    "dư nợ",
    "nợ quá hạn",
    "nhóm nợ",
    "tổ chức tín dụng",
]
VIETNAMESE_CHARS = set(
    "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệ"
    "íìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
)



class Supervisor:
    """Local  supervisor without API, cache, or database dependencies."""


    # Every structured block states its own money unit, and they disagree: the
    # metrics table is in tỷ VNĐ while the extractions are in đồng. One revenue
    # figure therefore appears as both "240,80" and "240800000000" in the same
    # prompt. Said out loud whenever more than one of them is present.
    MIXED_UNIT_WARNING = (
        "LƯU Ý ĐƠN VỊ TIỀN: các khối dữ liệu dưới đây dùng ĐƠN VỊ KHÁC NHAU và "
        "mỗi khối tự ghi đơn vị của nó ở ngay đầu khối. Đọc đúng đơn vị của "
        "khối đang trích. TUYỆT ĐỐI không lấy số của khối này đặt cạnh số của "
        "khối khác khi chưa quy đổi — cùng một chỉ tiêu có thể xuất hiện ở hai "
        "khối với hai đơn vị."
    )

    # Which agents read a BCTC as structured JSON, and how much of it. Being in
    # this table has two inseparable consequences: the agent gets the
    # [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT] block, AND the statement's raw OCR is replaced
    # by a pointer to that block. Enabling only one of the two would bill the
    # same content twice, so both read this single table.
    #
    # None means the whole extraction. Credit proposal argues repayment capacity
    # out of earnings, so it takes the income statement alone — the full record
    # runs 23k characters against its 20k budget, and would be cut mid-JSON.
    BCTC_JSON_STATEMENTS: dict[str, tuple[str, ...] | None] = {
        "FINANCIAL_ANALYSIS_AGENT": None,
        "CREDIT_PROPOSAL_AGENT": ("income_statement",),
    }

    # Same arrangement for the credit application form: these agents read it as
    # JSON and therefore do NOT get its raw OCR. Risk assessment is here because
    # sections C and the repayment plan are its subject matter; business
    # activity and financial analysis keep the raw form, which is all they use
    # it for.
    PROPOSAL_JSON_AGENTS = ("CREDIT_PROPOSAL_AGENT", "RISK_ASSESSMENT_AGENT")

    # And for the CIC S10A report. Credit relationship is the obvious consumer —
    # the report *is* its subject matter. Risk assessment is here because the
    # rating, the bad-debt history and the off-balance-sheet commitments in the
    # same document are its evidence, and the matrix already routes the file to
    # it. Same inseparable pair of consequences as the two tables above: these
    # agents get the block AND lose the raw OCR.
    CIC_S10A_JSON_AGENTS = ("CREDIT_RELATIONSHIP_AGENT", "RISK_ASSESSMENT_AGENT")

    # And for the CIC R20/R21 collateral report — same two agents, because
    # that is who the matrix routes cic_tai_san_bao_dam to today. Worth
    # flagging rather than silently matching: the collateral TABLE in the
    # credit-proposal template is the more natural reader for this block, but
    # CREDIT_PROPOSAL_AGENT has no R/O entry for this type in the matrix, so
    # giving it the JSON here would contradict routing everywhere else follows.
    CIC_R21_JSON_AGENTS = ("CREDIT_RELATIONSHIP_AGENT", "RISK_ASSESSMENT_AGENT")

    # The site-visit report goes to everyone, which none of the four above do.
    # It is the one document describing the business itself rather than one
    # facet of it: business activity takes the products and the buy/sell side,
    # financial analysis and credit proposal take next year's revenue and COGS
    # plan, credit relationship reads the trading pattern behind the debt, and
    # risk takes the officer's on-site findings. The matrix routes it to all
    # five to match — a block only ever sees documents routed to that agent.
    SITEVISIT_JSON_AGENTS = (
        "BUSINESS_ACTIVITY_AGENT",
        "FINANCIAL_ANALYSIS_AGENT",
        "CREDIT_RELATIONSHIP_AGENT",
        "CREDIT_PROPOSAL_AGENT",
        "RISK_ASSESSMENT_AGENT",
    )

    # The prompt blocks live in src/agents/prompt_blocks.py — see that module's
    # docstring for why only this group could move. Rebound here under their
    # original names so no existing call site changes, and so the constants that
    # travelled with them are still readable through Supervisor as before.
    METRICS_BLOCK_AGENTS = prompt_blocks.METRICS_BLOCK_AGENTS
    CREDIT_NEED_BLOCK_AGENTS = prompt_blocks.CREDIT_NEED_BLOCK_AGENTS
    CREDIT_NEED_BLOCK_HEADING = prompt_blocks.CREDIT_NEED_BLOCK_HEADING
    CIC_S10A_BLOCK_HEADING = prompt_blocks.CIC_S10A_BLOCK_HEADING
    CIC_R21_BLOCK_HEADING = prompt_blocks.CIC_R21_BLOCK_HEADING
    SITEVISIT_BLOCK_HEADING = prompt_blocks.SITEVISIT_BLOCK_HEADING
    SOURCE_LIST_BLOCK_HEADING = prompt_blocks.SOURCE_LIST_BLOCK_HEADING
    DEBT_CHART_TITLE = prompt_blocks.DEBT_CHART_TITLE
    DEBT_CHART_TITLE_DEBT_ONLY = prompt_blocks.DEBT_CHART_TITLE_DEBT_ONLY
    DEBT_CHART_COLUMNS = prompt_blocks.DEBT_CHART_COLUMNS
    VAT_ESTIMATE_NOTE = prompt_blocks.VAT_ESTIMATE_NOTE
    INDUSTRY_KNOWLEDGE_CHAR_BUDGET = prompt_blocks.INDUSTRY_KNOWLEDGE_CHAR_BUDGET
    INDUSTRY_EVIDENCE_EXCERPT_CHARS = prompt_blocks.INDUSTRY_EVIDENCE_EXCERPT_CHARS

    _document_block_header = staticmethod(prompt_blocks._document_block_header)
    _build_source_list_block = staticmethod(prompt_blocks._build_source_list_block)
    _build_financial_metrics_block = staticmethod(prompt_blocks._build_financial_metrics_block)
    _build_credit_need_block = staticmethod(prompt_blocks._build_credit_need_block)
    _build_proposal_structured_block = staticmethod(prompt_blocks._build_proposal_structured_block)
    _build_debt_chart_block = staticmethod(prompt_blocks._build_debt_chart_block)
    _build_cic_s10a_structured_block = staticmethod(prompt_blocks._build_cic_s10a_structured_block)
    _build_cic_r21_structured_block = staticmethod(prompt_blocks._build_cic_r21_structured_block)
    _build_sitevisit_structured_block = staticmethod(prompt_blocks._build_sitevisit_structured_block)
    _build_bctc_structured_block = staticmethod(prompt_blocks._build_bctc_structured_block)


    # Agents that need the CIC S10A extraction to have *run* without reading the
    # JSON block itself. Credit proposal subtracts the balance outstanding at
    # other lenders from the working-capital need, so the pass has to happen on a
    # single-proposal route — but handing it the raw block would also strip that
    # document's OCR, which it has no use for. Kept apart from
    # CIC_S10A_JSON_AGENTS so the two reasons cannot be confused later.
    CIC_S10A_CALCULATOR_AGENTS = ("CREDIT_PROPOSAL_AGENT",)


    # The flag each extraction pass selects documents by. Single-sourced because
    # three things key off the same pass names: which passes a route needs, which
    # ones then run, and how many calls the skipped ones would have cost.
    EXTRACTION_PASS_FLAGS = {
        "BCTC": "is_bctc",
        "Proposal": "is_proposal",
        "CIC S10A": "is_cic_s10a",
        "CIC R21": "is_cic_r21",
        "Sitevisit": "is_sitevisit",
    }

    # Which agents each route actually runs. Structured extraction costs one LLM
    # call per matching document, so the run needs to know who is going to read
    # the result before it pays for it — see _passes_needed_for_route.
    ROUTE_AGENTS: dict[str, tuple[str, ...]] = {
        "BUSINESS_ACTIVITY_AGENT": ("BUSINESS_ACTIVITY_AGENT",),
        "CREDIT_RELATIONSHIP_AGENT": ("CREDIT_RELATIONSHIP_AGENT",),
        "FINANCIAL_ANALYSIS_AGENT": ("FINANCIAL_ANALYSIS_AGENT",),
        "RISK_ASSESSMENT_AGENT": ("RISK_ASSESSMENT_AGENT",),
        "CREDIT_PROPOSAL_AGENT": ("CREDIT_PROPOSAL_AGENT",),
        "CREDIT_MEMO": (
            "BUSINESS_ACTIVITY_AGENT",
            "CREDIT_RELATIONSHIP_AGENT",
            "FINANCIAL_ANALYSIS_AGENT",
            "CREDIT_PROPOSAL_AGENT",
            "RISK_ASSESSMENT_AGENT",
        ),
    }


    # Where to anchor it, most specific first. The composer renumbers headings
    # when it merges the five specialist reports, so matching on an exact
    # "## 2. ..." string would work in a single-agent run and quietly fail in a
    # full memo. Matched on accent-stripped substrings of the heading text.
    DEBT_CHART_ANCHORS = ("dien bien du no", "quan he tin dung")

    # Numeric form of the matrix's R/O relevance levels, so ranking and budget
    # code can compare relevance without carrying the R/O vocabulary around.
    RELEVANCE_SCORE = {"R": 2.0, "O": 1.0}

    # Relative per-document budget weights in _build_user_input. The matrix says
    # which documents an agent *requires* (R) versus may use (O); required
    # evidence must not be squeezed out by optional evidence. Documents that
    # matched no matrix type are shared with everyone and get the optional
    # weight — they are context, not this agent's core evidence.
    PRIMARY_DOC_BUDGET_WEIGHT = 3
    SECONDARY_DOC_BUDGET_WEIGHT = 1

    # Fixed prompt scaffolding around the document blocks. Named constants so
    # the budget arithmetic in _build_user_input measures exactly what it emits.
    DOC_SECTION_HEADER = "Uploaded document extracted content:\n\n"
    DOC_BLOCK_SEPARATOR = "\n\n---\n\n"

    # Which agents get an industry reference block (see
    # _build_industry_knowledge_block). A set, not a matrix entry, because
    # this is not per-case document routing — it is one static reference deck
    # selected from src/knowledge/, same file for every case in that industry.
    INDUSTRY_KNOWLEDGE_AGENTS = {"BUSINESS_ACTIVITY_AGENT"}

    def __init__(self, config: Config):
        self.config = config
        self.guardrails = (
            LocalGuardrails(config.guardrail_llm)
            if config.enable_safety_guardrails
            else None
        )
        self.web_search_agent = (
            WebSearchProcessorAgent()
            if config.enable_web_search
            else None
        )
        self.decision_chain = (
            self._build_decision_chain() 
            if config.decision_llm 
            else None
        )
        self.document_classifier_chain = (
            self._build_document_classifier_chain()
            if config.document_llm
            else None
        )
        self.bctc_extraction_chain = (
            build_bctc_extraction_chain(config.bctc_extraction_llm)
            if config.bctc_extraction_llm
            else None
        )
        self.proposal_extraction_chain = (
            build_proposal_extraction_chain(config.proposal_extraction_llm)
            if config.proposal_extraction_llm
            else None
        )
        self.cic_s10a_extraction_chain = (
            build_cic_s10a_extraction_chain(config.cic_s10a_extraction_llm)
            if config.cic_s10a_extraction_llm
            else None
        )
        self.cic_r21_extraction_chain = (
            build_cic_r21_extraction_chain(config.cic_r21_extraction_llm)
            if config.cic_r21_extraction_llm
            else None
        )
        self.sitevisit_extraction_chain = (
            build_sitevisit_extraction_chain(config.sitevisit_extraction_llm)
            if config.sitevisit_extraction_llm
            else None
        )
        self.workflow_graph = self._build_workflow_graph()

    def _build_workflow_graph(self):
        """Build the deterministic LangGraph underwriting workflow."""

        workflow = StateGraph(UnderwritingGraphState)
        workflow.add_node("prepare_input", self._graph_prepare_input)
        workflow.add_node("input_guardrail", self._graph_input_guardrail)
        workflow.add_node("discover_documents", self._graph_discover_documents)
        workflow.add_node("classify_documents", self._graph_classify_documents)
        workflow.add_node("decide_workflow", self._graph_decide_workflow)
        workflow.add_node("evidence_gap_check", self._graph_evidence_gap_check)
        workflow.add_node("extract_documents", self._graph_extract_documents)
        workflow.add_node("web_search", self._graph_web_search)
        workflow.add_node(
            "single_business_activity",
            self._graph_run_business_activity,
        )
        workflow.add_node(
            "single_credit_relationship",
            self._graph_run_credit_relationship,
        )
        workflow.add_node(
            "single_financial_analysis",
            self._graph_run_financial_analysis,
        )
        workflow.add_node(
            "single_risk_assessment",
            self._graph_run_risk_assessment,
        )
        workflow.add_node(
            "single_credit_proposal",
            self._graph_run_credit_proposal,
        )
        workflow.add_node("full_credit_memo", self._graph_run_credit_memo)

        workflow.set_entry_point("prepare_input")
        workflow.add_edge("prepare_input", "input_guardrail")
        workflow.add_conditional_edges(
            "input_guardrail",
            self._graph_after_input_guardrail,
            {"blocked": END, "continue": "discover_documents"},
        )
        workflow.add_conditional_edges(
            "discover_documents",
            self._graph_after_discover_documents,
            {"out_of_scope": END, "continue": "classify_documents"},
        )
        workflow.add_edge("classify_documents", "decide_workflow")
        workflow.add_edge("decide_workflow", "evidence_gap_check")
        workflow.add_conditional_edges(
            "evidence_gap_check",
            self._graph_after_evidence_gap_check,
            {"blocked": END, "continue": "extract_documents"},
        )
        workflow.add_edge("extract_documents", "web_search")
        workflow.add_conditional_edges(
            "web_search",
            self._graph_select_workflow_branch,
            {
                "single_business_activity": "single_business_activity",
                "single_credit_relationship": "single_credit_relationship",
                "single_financial_analysis": "single_financial_analysis",
                "single_risk_assessment": "single_risk_assessment",
                "single_credit_proposal": "single_credit_proposal",
                "full_credit_memo": "full_credit_memo",
            },
        )
        for node in [
            "single_business_activity",
            "single_credit_relationship",
            "single_financial_analysis",
            "single_risk_assessment",
            "single_credit_proposal",
            "full_credit_memo",
        ]:
            workflow.add_edge(node, END)
        
        return workflow.compile()

    def _graph_prepare_input(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Normalize the user request and conversation context."""

        query = state.get("query", "")
        input_paths = state.get("input_paths") or []
        conversation_history = state.get("conversation_history") or []
        steps = ["Received request"]
        history_context = self._format_history(conversation_history)
        steps.append("Built compact conversation context")
        return {
            **state,
            "query": query,
            "input_paths": input_paths,
            "conversation_history": conversation_history,
            "history_context": history_context,
            "steps": steps,
        }

    def _graph_input_guardrail(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Stop early when input guardrails reject the request."""

        query = state.get("query", "")
        steps = state.get("steps", [])
        if self.guardrails and query.strip():
            allowed, message = self.guardrails.check_input(query)
            steps.append("Checked input guardrails")
            if not allowed:
                return {
                    **state,
                    "steps": steps,
                    "output_state": self._build_state(
                        query,
                        message,
                        "INPUT_GUARDRAILS",
                        steps=steps + ["Blocked by input guardrails"],
                    ),
                }
        return {**state, "steps": steps}

    @staticmethod
    def _graph_after_input_guardrail(state: UnderwritingGraphState) -> str:
        """Choose whether to continue after input guardrail checks."""

        return "blocked" if state.get("output_state") else "continue"

    def _graph_discover_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Discover supported files from the provided input paths."""

        files = discover_documents(
            state.get("input_paths") or [],
            self.config.max_files,
        )
        steps = state.get("steps", [])
        steps.append(f"Discovered {len(files)} supported file(s)")
        if files or self._has_analysis_intent(state.get("query", "")):
            return {**state, "files": files, "steps": steps}

        # Nothing to analyse and nothing asked for — answer from a constant and
        # stop. Sitting here rather than earlier because only now is "no usable
        # document" certain: the caller may have passed a folder holding nothing
        # this system reads. Still ahead of every cost, since discovery only
        # lists filenames and all OCR belongs to classify_documents.
        steps.append("Out of scope: no documents and no analysis request")
        return {
            **state,
            "files": files,
            "steps": steps,
            "output_state": self._build_state(
                state.get("query", ""),
                OUT_OF_SCOPE_RESPONSE,
                "OUT_OF_SCOPE",
                {"route": "OUT_OF_SCOPE", "reasoning": "No documents, no analysis request."},
                [],
                {},
                {},
                {},
                "",
                steps,
            ),
        }

    @staticmethod
    def _graph_after_discover_documents(state: UnderwritingGraphState) -> str:
        """Stop when there is nothing to analyse and nothing was asked."""

        return "out_of_scope" if state.get("output_state") else "continue"

    @classmethod
    def _has_analysis_intent(cls, query: str) -> bool:
        """True when the request names work one of the specialists can do.

        Read off the same keyword lists routing already uses, so a request that
        would have reached an agent can never be turned away here. Kept separate
        from "are there documents": asking for financial analysis without
        attaching statements should still reach the evidence gap check, which
        answers with the specific documents that are missing.
        """

        normalized = (query or "").lower()
        return any(
            cls._contains_any(normalized, keywords)
            for keywords in (
                CREDIT_MEMO_KEYWORDS,
                CREDIT_PROPOSAL_ROUTE_KEYWORDS,
                RISK_ASSESSMENT_ROUTE_KEYWORDS,
                CREDIT_RELATIONSHIP_ROUTE_KEYWORDS,
                FINANCIAL_ROUTE_KEYWORDS,
                BUSINESS_ROUTE_KEYWORDS,
            )
        )

    def _graph_classify_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Extract and classify uploaded documents."""

        steps = state.get("steps", [])
        # Resolved once per run, before any document is classified: it picks
        # which column of the matrix decides every document's R/O relevance.
        detection = detect_loan_program(
            state.get("query", ""),
            state.get("history_context", ""),
        )
        loan_program = detection["program"] or ""
        steps.append(self._format_loan_program_step(detection))
        documents = self._prepare_documents(
            state.get("files") or [],
            state.get("query", ""),
            state.get("history_context", ""),
            steps,
            loan_program,
        )
        # Structured extraction deliberately does NOT happen here. It costs one
        # LLM call per matching document, and which of those results anybody
        # reads depends on the route, which is not decided until two nodes from
        # now — so it runs in _graph_extract_documents instead.
        document_routes: set[str] = set()
        for doc in documents:
            if doc.agent == "GENERAL_CONTEXT":
                document_routes.add("GENERAL_CONTEXT")
                continue
            document_routes.update(doc.relevant_agents)
        document_summary = self._format_document_summary(documents)
        return {
            **state,
            "loan_program": loan_program,
            "loan_program_detection": detection,
            "documents": documents,
            "document_routes": document_routes,
            "document_summary": document_summary,
        }

    @staticmethod
    def _format_loan_program_step(detection: dict[str, Any]) -> str:
        """Explain the loan program decision in the run's step log.

        Detection comes from free text the user typed, so it has to be visible:
        a silently wrong (or silently absent) program would quietly change how
        documents are prioritised with nothing in the output to point at.
        """

        if detection["program"]:
            return (
                f"Loan program: {detection['program']} "
                f"(matched \"{detection['matched_alias']}\" in the "
                f"{detection['source']})"
            )
        if detection["candidates"]:
            return (
                "Loan program: ambiguous — the request names "
                f"{', '.join(detection['candidates'])}. Using the strongest "
                "relevance across all programs."
            )
        return (
            "Loan program: not specified in the request. Using the strongest "
            "relevance across all programs (documents can only be "
            "over-prioritised, never dropped)."
        )

    def _graph_decide_workflow(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Select route and deterministic workflow mode."""

        documents = state.get("documents") or []
        decision = self._decide(
            state.get("query", ""),
            bool(documents),
            state.get("history_context", ""),
            state.get("document_summary", ""),
            state.get("document_routes") or set(),
        )
        steps = state.get("steps", [])
        steps.append(f"Selected route: {decision['route']}")
        steps.append(f"Selected workflow mode: {decision['workflow_mode']}")
        return {
            **state,
            "decision": decision,
            "workflow_mode": decision["workflow_mode"],
            "steps": steps,
        }

    def _graph_evidence_gap_check(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Analyze required evidence before running expensive agents."""

        query = state.get("query", "")
        decision = state.get("decision") or {}
        documents = state.get("documents") or []
        steps = state.get("steps", [])
        gap_analysis = self._analyze_evidence_gaps(
            documents,
            decision.get("route", DEFAULT_ROUTE),
        )
        execution_plan = self._build_execution_plan(decision, gap_analysis)
        steps.append("Built Self-Ask evidence gap analysis")
        steps.append("Built LangGraph execution plan")
        if not execution_plan.get("can_answer_now", True):
            response = self._missing_evidence_response(query, gap_analysis)
            output_state = self._build_state(
                query,
                response,
                "EVIDENCE_GAP_CHECK",
                decision,
                documents,
                {},
                execution_plan,
                gap_analysis,
                "",
                steps
                + [
                    "Stopped before agent execution because required evidence "
                    "is missing"
                ],
            )
            return {
                **state,
                "gap_analysis": gap_analysis,
                "execution_plan": execution_plan,
                "steps": steps,
                "output_state": output_state,
            }
        return {
            **state,
            "gap_analysis": gap_analysis,
            "execution_plan": execution_plan,
            "steps": steps,
        }

    @staticmethod
    def _graph_after_evidence_gap_check(
        state: UnderwritingGraphState,
    ) -> str:
        """Choose whether missing required evidence blocks execution."""

        return "blocked" if state.get("output_state") else "continue"

    def _graph_extract_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run only the structured extractions this route will actually read.

        Placed after the evidence gap check rather than beside classification so
        two kinds of waste disappear: a run blocked for missing evidence pays for
        no extraction at all, and a route whose agents never read a given block
        does not pay for it either — a single BUSINESS_ACTIVITY run used to spend
        an LLM call per CIC and BCTC file that nothing downstream then opened.

        Safe to defer because nothing between classification and here reads an
        extraction *result*: routing and the gap check both work off the document
        types the matrix assigned.
        """

        documents = state.get("documents") or []
        steps = state.get("steps", [])
        route = (state.get("decision") or {}).get("route", DEFAULT_ROUTE)
        needed = self._passes_needed_for_route(route)

        for label, run_pass in (
            ("BCTC", self._extract_bctc_documents),
            ("Proposal", self._extract_proposal_documents),
            ("CIC S10A", self._extract_cic_s10a_documents),
            ("CIC R21", self._extract_cic_r21_documents),
            ("Sitevisit", self._extract_sitevisit_documents),
        ):
            if label in needed:
                run_pass(documents, steps)

        skipped = self._describe_skipped_passes(documents, needed)
        if skipped:
            # Say it out loud: a missing extraction block otherwise looks
            # identical to one that failed, and the two need different fixes.
            steps.append(
                f"Saved LLM calls — no agent on route {route} reads: {skipped}"
            )
        return {
            **state,
            "documents": documents,
            # Rebuilt because the tags in it report extraction state, which only
            # exists now — the copy the routing node read was written before any
            # of this ran.
            "document_summary": self._format_document_summary(documents),
            "steps": steps,
        }

    @classmethod
    def _passes_needed_for_route(cls, route: str) -> set[str]:
        """Which extraction passes the agents on this route actually consume."""

        agents = set(cls.ROUTE_AGENTS.get(route, ()))
        consumers = {
            # Financial analysis and credit proposal read the BCTC JSON itself;
            # the metrics agents read ratios computed from the same extraction.
            "BCTC": set(cls.BCTC_JSON_STATEMENTS) | set(cls.METRICS_BLOCK_AGENTS),
            "Proposal": set(cls.PROPOSAL_JSON_AGENTS),
            "CIC S10A": (
                set(cls.CIC_S10A_JSON_AGENTS)
                | set(cls.CIC_S10A_CALCULATOR_AGENTS)
            ),
            "CIC R21": set(cls.CIC_R21_JSON_AGENTS),
            "Sitevisit": set(cls.SITEVISIT_JSON_AGENTS),
        }
        return {
            label
            for label, readers in consumers.items()
            if agents & readers
        }

    @classmethod
    def _describe_skipped_passes(
        cls,
        documents: list[ClassifiedDocument],
        needed: set[str],
    ) -> str:
        """Name the skipped passes and how many documents each would have cost.

        Counts documents rather than just naming passes so the step log shows the
        saving, not merely the decision.
        """

        parts = []
        for label, flag_attr in cls.EXTRACTION_PASS_FLAGS.items():
            if label in needed:
                continue
            count = sum(1 for doc in documents if getattr(doc, flag_attr))
            if count:
                parts.append(f"{label} ({count} document(s), {count} LLM call(s))")
        return ", ".join(parts)

    def _graph_web_search(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Optionally enrich state with web search context."""

        web_context = self._maybe_run_web_search(
            state.get("query", ""),
            state.get("conversation_history") or [],
            state.get("decision") or {},
            state.get("document_summary", ""),
            state.get("steps", []),
        )
        return {**state, "web_context": web_context}

    @staticmethod
    def _graph_select_workflow_branch(
        state: UnderwritingGraphState,
    ) -> WorkflowMode:
        """Route graph execution by deterministic workflow mode."""

        return state.get("workflow_mode", DEFAULT_WORKFLOW_MODE)

    def _graph_run_business_activity(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the single business activity branch."""

        return {
            **state,
            "output_state": self._run_single_agent_branch(
                state,
                "BUSINESS_ACTIVITY_AGENT",
            ),
        }

    def _graph_run_credit_relationship(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the single credit relationship branch."""

        return {
            **state,
            "output_state": self._run_single_agent_branch(
                state,
                "CREDIT_RELATIONSHIP_AGENT",
            ),
        }

    def _graph_run_financial_analysis(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the single financial analysis branch."""

        return {
            **state,
            "output_state": self._run_single_agent_branch(
                state,
                "FINANCIAL_ANALYSIS_AGENT",
            ),
        }

    def _graph_run_risk_assessment(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the single risk assessment branch."""

        return {
            **state,
            "output_state": self._run_single_agent_branch(
                state,
                "RISK_ASSESSMENT_AGENT",
            ),
        }

    def _graph_run_credit_proposal(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the standalone Credit Proposal branch."""

        return {
            **state,
            "output_state": self._run_single_agent(
                "CREDIT_PROPOSAL_AGENT",
                state.get("query", ""),
                state.get("history_context", ""),
                state.get("decision") or {},
                state.get("documents") or [],
                state.get("web_context", ""),
                state.get("execution_plan") or {},
                state.get("gap_analysis") or {},
                state.get("steps", []),
            ),
        }

    def _graph_run_credit_memo(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the full Credit Memo underwriting branch."""

        return {
            **state,
            "output_state": self._run_credit_memo_workflow(
                state.get("query", ""),
                state.get("history_context", ""),
                state.get("decision") or {},
                state.get("documents") or [],
                state.get("web_context", ""),
                state.get("execution_plan") or {},
                state.get("gap_analysis") or {},
                state.get("steps", []),
            ),
        }

    def _run_single_agent_branch(
        self,
        state: UnderwritingGraphState,
        agent_name: str,
    ) -> dict[str, Any]:
        """Adapt graph state to the existing single-agent runner."""

        return self._run_single_agent(
            agent_name,
            state.get("query", ""),
            state.get("history_context", ""),
            state.get("decision") or {},
            state.get("documents") or [],
            state.get("web_context", ""),
            state.get("execution_plan") or {},
            state.get("gap_analysis") or {},
            state.get("steps", []),
        )

    def process(
        self,
        query: str,
        input_paths: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Run one local notebook request through the agent workflow."""

        initial_state: UnderwritingGraphState = {
            "query": query,
            "input_paths": input_paths or [],
            "conversation_history": conversation_history or [],
        }
        result = self.workflow_graph.invoke(initial_state)
        output_state = result.get("output_state")
        if output_state:
            return output_state
        return self._build_state(
            query,
            "Workflow graph finished without an output state.",
            "WORKFLOW_GRAPH",
            steps=result.get("steps", []),
        )

    def _prepare_documents(
        self,
        files: list[str],
        input_text: str,
        history_context: str,
        steps: list[str],
        loan_program: str = "",
    ) -> list[ClassifiedDocument]:
        documents = []
        seen_hashes: set[str] = set()
        for file_path in files:
            filename = Path(file_path).name
            file_hash = compute_file_hash(file_path)
            if file_hash in seen_hashes:
                steps.append(f"Skipped duplicate document: {filename}")
                continue
            seen_hashes.add(file_hash)
            try:
                steps.append(f"Extracting document content: {filename}")
                content = extract_document_text(
                    file_path,
                    ocr_timeout_seconds=float(
                        os.getenv("OCR_TIMEOUT_SECONDS", "120")
                    ),
                )
                extraction_status = "success"
                extraction_error = ""
            except Exception as exc:
                content = (
                    f"[Document text extraction failed for {filename}: {exc}]"
                )
                extraction_status = "failed"
                extraction_error = f"{type(exc).__name__}: {exc}"

            content = truncate_text(
                content,
                self.config.max_chars_per_document,
            )
            classification = self._classify_document(
                filename,
                content,
                input_text,
                history_context,
            )
            document_type = classification.get("document_type", "")
            type_scores = classification.get("scores", {})
            # The matrix decides the fan-out. A document that matched no type
            # stays GENERAL_CONTEXT, which _select_documents_for_agent already
            # shares with every agent, so nothing is dropped on a miss.
            # An empty loan_program resolves to the strongest level across all
            # programs — see agent_relevance_for_type.
            agent_relevance = agent_relevance_for_type(
                document_type,
                loan_program or None,
            )
            relevant_agents = sorted(agent_relevance)
            matched = get_type(document_type)
            agent = primary_agent_for_type(document_type) or "GENERAL_CONTEXT"
            is_bctc = is_bctc_type(document_type)
            is_proposal = is_proposal_type(document_type)
            is_cic_s10a = is_cic_s10a_type(document_type)
            is_cic_r21 = is_cic_r21_type(document_type)
            is_sitevisit = is_sitevisit_type(document_type)
            steps.append(
                f"Classified document: {filename} -> "
                + (f"{document_type} " if document_type else "(no type matched) ")
                + f"-> {', '.join(relevant_agents) or 'GENERAL_CONTEXT'}"
                + (" [BCTC]" if is_bctc else "")
            )
            documents.append(
                ClassifiedDocument(
                    path=file_path,
                    filename=filename,
                    content=content,
                    agent=agent,
                    reasoning=classification.get("reasoning", ""),
                    confidence=float(classification.get("confidence", 0.0)),
                    file_hash=file_hash,
                    extraction_status=extraction_status,
                    extraction_error=extraction_error,
                    classifier_error_type=classification.get(
                        "classifier_error_type",
                        "",
                    ),
                    classifier_error=classification.get("classifier_error", ""),
                    document_type=document_type,
                    document_group=matched.group_id if matched else "",
                    type_scores=type_scores,
                    agent_relevance=agent_relevance,
                    loan_program=loan_program,
                    agent_scores={
                        name: self.RELEVANCE_SCORE[level]
                        for name, level in agent_relevance.items()
                    },
                    relevant_agents=relevant_agents,
                    is_bctc=is_bctc,
                    is_proposal=is_proposal,
                    is_cic_s10a=is_cic_s10a,
                    is_cic_r21=is_cic_r21,
                    is_sitevisit=is_sitevisit,
                )
            )
        return documents

    def _run_structured_extraction(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
        *,
        flag_attr: str,
        chain: Any,
        extract: Any,
        result_attr: str,
        error_attr: str,
        label: str,
        missing_llm_message: str,
    ) -> None:
        """Run one structured-extraction pass over the documents it applies to.

        Several files can need the same pass in one run (current + prior year
        statements, say), so it goes concurrently, bounded by max_concurrency —
        the same pattern as the parallel analysis agents. Results are written
        onto each document in place and nothing raises, so a failed or
        unconfigured extraction always leaves _build_user_input a clean signal
        to fall back to the raw OCR text.

        Shared by the BCTC and credit-application passes: they differ only in
        which documents they apply to and where the result is stored.
        """

        targets = [doc for doc in documents if getattr(doc, flag_attr)]
        if not targets:
            return
        if not chain:
            for doc in targets:
                setattr(doc, error_attr, missing_llm_message)
            steps.append(
                f"Skipped {label} extraction for {len(targets)} document(s): "
                f"{missing_llm_message}"
            )
            return

        def _run(doc: ClassifiedDocument):
            result, error = extract(chain, doc.filename, doc.content)
            return doc, result, error

        max_workers = max(1, min(len(targets), self.config.max_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_run, doc) for doc in targets]
            for future in futures:
                doc, result, error = future.result()
                setattr(doc, result_attr, result)
                setattr(doc, error_attr, error)
                steps.append(
                    f"{label} extraction: {doc.filename} -> "
                    + ("ok" if result is not None else f"failed: {error}")
                )

    def _extract_bctc_documents(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
    ) -> None:
        self._run_structured_extraction(
            documents,
            steps,
            flag_attr="is_bctc",
            chain=self.bctc_extraction_chain,
            extract=extract_bctc_structured_data,
            result_attr="bctc_extraction",
            error_attr="bctc_extraction_error",
            label="BCTC",
            missing_llm_message="No bctc_extraction_llm configured.",
        )

    def _extract_proposal_documents(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
    ) -> None:
        self._run_structured_extraction(
            documents,
            steps,
            flag_attr="is_proposal",
            chain=self.proposal_extraction_chain,
            extract=extract_proposal_structured_data,
            result_attr="proposal_extraction",
            error_attr="proposal_extraction_error",
            label="Proposal",
            missing_llm_message="No proposal_extraction_llm configured.",
        )

    def _extract_cic_s10a_documents(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
    ) -> None:
        self._run_structured_extraction(
            documents,
            steps,
            flag_attr="is_cic_s10a",
            chain=self.cic_s10a_extraction_chain,
            extract=extract_cic_s10a_structured_data,
            result_attr="cic_s10a_extraction",
            error_attr="cic_s10a_extraction_error",
            label="CIC S10A",
            missing_llm_message="No cic_s10a_extraction_llm configured.",
        )

    def _extract_cic_r21_documents(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
    ) -> None:
        self._run_structured_extraction(
            documents,
            steps,
            flag_attr="is_cic_r21",
            chain=self.cic_r21_extraction_chain,
            extract=extract_cic_r21_structured_data,
            result_attr="cic_r21_extraction",
            error_attr="cic_r21_extraction_error",
            label="CIC R21",
            missing_llm_message="No cic_r21_extraction_llm configured.",
        )

    def _extract_sitevisit_documents(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
    ) -> None:
        self._run_structured_extraction(
            documents,
            steps,
            flag_attr="is_sitevisit",
            chain=self.sitevisit_extraction_chain,
            extract=extract_sitevisit_structured_data,
            result_attr="sitevisit_extraction",
            error_attr="sitevisit_extraction_error",
            label="Sitevisit",
            missing_llm_message="No sitevisit_extraction_llm configured.",
        )

    @staticmethod
    def _document_sample(content: str, limit: int = 2_400) -> str:
        """Sample head+middle+tail so classification is not cover-page biased."""

        text = content or ""
        if len(text) <= limit:
            return text
        chunk = limit // 3
        head = text[:chunk]
        mid_start = max(chunk, (len(text) - chunk) // 2)
        middle = text[mid_start:mid_start + chunk]
        tail = text[-chunk:]
        return f"{head}\n...\n{middle}\n...\n{tail}"

    def _classify_document(
        self,
        filename: str,
        content: str,
        input_text: str,
        history_context: str,
    ) -> dict[str, Any]:
        rule = rule_classify_document(filename, content)
        if rule["document_type"] and (
            rule["confidence"]
            >= self.config.document_classifier_rule_confidence_threshold
            # Even a low-confidence rule result is worth keeping when every
            # plausible type routes to the same agents: the LLM could only
            # change the label, not where the document goes.
            or rule["routing_unambiguous"]
            # Or when the filename names exactly one type and the body agreed.
            # Confidence is a margin measure, so a well-named file whose text
            # happens to quote a neighbouring type's vocabulary can dip under
            # the threshold with nothing actually in doubt.
            or rule["filename_decisive"]
        ):
            return rule

        if self.document_classifier_chain:
            try:
                result = self.document_classifier_chain.invoke(
                    {
                        "input_text": input_text or "No explicit user request.",
                        "history_context": (
                            history_context or "No previous conversation."
                        ),
                        "filename": filename,
                        "content_sample": (
                            self._document_sample(content)
                            or "No text extracted from the document."
                        ),
                    }
                )
                document_type = result.get("document_type") or ""
                # An id the matrix doesn't define would silently route the
                # document nowhere, so fall back to the rule result instead.
                if document_type and get_type(document_type) is None:
                    return rule
                result["document_type"] = document_type
                # Keyword scores always come from the rule pass, so monitoring
                # can see the runner-up types even when the LLM label wins.
                result["scores"] = rule.get("scores", {})
                return result
            except Exception as exc:
                rule["classifier_error_type"] = type(exc).__name__
                rule["classifier_error"] = str(exc)[:500]
                rule["reasoning"] = (
                    "Fallback keyword classification because document "
                    "classifier failed."
                )
        return rule

    def _build_decision_chain(self):
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", DECISION_SYSTEM_PROMPT),
                (
                    "human",
                    """
                    Recent conversation history:
                    {history_context}

                    User query: {input_text}

                    Has uploaded file: {has_file}

                    Uploaded document classification summary:
                    {document_summary}

                    Choose the route.
                    """,
                ),
            ]
        )
        return prompt | self.config.decision_llm | JsonOutputParser()

    def _build_document_classifier_chain(self):
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", build_document_classification_prompt()),
                (
                    "human",
                    """
                    User request:
                    {input_text}

                    Recent conversation history:
                    {history_context}

                    Filename: {filename}

                    Extracted document text sample:
                    {content_sample}

                    Classify this document.
                    """,
                ),
            ]
        )
        return prompt | self.config.document_llm | JsonOutputParser()

    def _deterministic_route(self, input_text: str) -> AgentName | None:
        """Return a route decided purely by keywords, or None if ambiguous.

        Mirrors the keyword branches of ``_override_route`` (which already take
        precedence over the decision LLM). When a keyword matches we can skip the
        decision-LLM round-trip entirely — the routing outcome is identical.
        """
        normalized = input_text.lower()
        keyword_routes = [
            (CREDIT_MEMO_KEYWORDS, "CREDIT_MEMO"),
            (CREDIT_PROPOSAL_ROUTE_KEYWORDS, "CREDIT_PROPOSAL_AGENT"),
            (RISK_ASSESSMENT_ROUTE_KEYWORDS, "RISK_ASSESSMENT_AGENT"),
            (CREDIT_RELATIONSHIP_ROUTE_KEYWORDS, "CREDIT_RELATIONSHIP_AGENT"),
            (FINANCIAL_ROUTE_KEYWORDS, "FINANCIAL_ANALYSIS_AGENT"),
            (BUSINESS_ROUTE_KEYWORDS, "BUSINESS_ACTIVITY_AGENT"),
        ]
        for keywords, route in keyword_routes:
            if self._contains_any(normalized, keywords):
                return route
        return None

    def _decide(
        self,
        input_text: str,
        has_file: bool,
        history_context: str,
        document_summary: str,
        document_routes: set[str],
    ) -> dict[str, Any]:
        deterministic_route = self._deterministic_route(input_text)
        if deterministic_route is not None:
            decision = {
                "reasoning": "Deterministic keyword route (decision LLM skipped).",
                "confidence": 1.0,
            }
            route = deterministic_route
        elif self.decision_chain:
            try:
                decision = self.decision_chain.invoke(
                    {
                        "input_text": input_text,
                        "has_file": str(has_file),
                        "history_context": (
                            history_context or "No previous conversation."
                        ),
                        "document_summary": (
                            document_summary or "No uploaded documents."
                        ),
                    }
                )
                route = decision.get("route", DEFAULT_ROUTE)
            except Exception:
                decision = {
                    "reasoning": (
                        "Fallback route selected because supervisor "
                        "classification failed."
                    ),
                    "confidence": 0.0,
                }
                route = self._fallback_route(input_text, has_file)
        else:
            decision = {
                "reasoning": (
                    "Rule-based route selected because decision LLM is "
                    "not configured."
                ),
                "confidence": 0.0,
            }
            route = self._fallback_route(input_text, has_file)

        route = self._override_route(
            route,
            input_text,
            has_file,
            document_routes,
        )
        workflow_mode = self._workflow_mode_for_route(route)
        return {
            "route": route,
            "workflow_mode": workflow_mode,
            "reasoning": decision.get("reasoning", ""),
            "confidence": float(decision.get("confidence", 0.0)),
            "requires_credit_memo": route == "CREDIT_MEMO",
        }

    @staticmethod
    def _workflow_mode_for_route(route: str) -> WorkflowMode:
        """Map a route to the deterministic workflow branch."""

        route_modes = {
            "BUSINESS_ACTIVITY_AGENT": "single_business_activity",
            "CREDIT_RELATIONSHIP_AGENT": "single_credit_relationship",
            "FINANCIAL_ANALYSIS_AGENT": "single_financial_analysis",
            "RISK_ASSESSMENT_AGENT": "single_risk_assessment",
            "CREDIT_PROPOSAL_AGENT": "single_credit_proposal",
            "CREDIT_MEMO": "full_credit_memo",
        }
        return route_modes.get(route, DEFAULT_WORKFLOW_MODE)

    def _override_route(
        self,
        route: str,
        input_text: str,
        has_file: bool,
        document_routes: set[str],
    ) -> AgentName:
        normalized = input_text.lower()
        if self._contains_any(normalized, CREDIT_MEMO_KEYWORDS):
            return "CREDIT_MEMO"
        if self._contains_any(normalized, CREDIT_PROPOSAL_ROUTE_KEYWORDS):
            return "CREDIT_PROPOSAL_AGENT"
        if self._contains_any(normalized, RISK_ASSESSMENT_ROUTE_KEYWORDS):
            return "RISK_ASSESSMENT_AGENT"
        if self._contains_any(normalized, CREDIT_RELATIONSHIP_ROUTE_KEYWORDS):
            return "CREDIT_RELATIONSHIP_AGENT"
        if self._contains_any(normalized, FINANCIAL_ROUTE_KEYWORDS):
            return "FINANCIAL_ANALYSIS_AGENT"
        if self._contains_any(normalized, BUSINESS_ROUTE_KEYWORDS):
            return "BUSINESS_ACTIVITY_AGENT"
        valid_routes = {
            "FINANCIAL_ANALYSIS_AGENT",
            "BUSINESS_ACTIVITY_AGENT",
            "CREDIT_RELATIONSHIP_AGENT",
            "RISK_ASSESSMENT_AGENT",
            "CREDIT_PROPOSAL_AGENT",
            "CREDIT_MEMO",
        }
        # The request named no task, so let the evidence decide. Previously
        # reached via route == CONVERSATION_AGENT, which was how the decision
        # step said "this is not analysis"; with that route gone, the same
        # situation shows up as a route it does not recognise.
        if route not in valid_routes and has_file:
            both_analysis_docs = {
                "FINANCIAL_ANALYSIS_AGENT",
                "BUSINESS_ACTIVITY_AGENT",
            }.issubset(document_routes)
            if both_analysis_docs:
                return "RISK_ASSESSMENT_AGENT"
            for candidate in [
                "FINANCIAL_ANALYSIS_AGENT",
                "BUSINESS_ACTIVITY_AGENT",
                "CREDIT_RELATIONSHIP_AGENT",
                "CREDIT_PROPOSAL_AGENT",
                "RISK_ASSESSMENT_AGENT",
            ]:
                if candidate in document_routes:
                    return candidate
        if route in valid_routes:
            return route
        return DEFAULT_ROUTE

    def _fallback_route(self, input_text: str, has_file: bool) -> AgentName:
        normalized = input_text.lower()
        if self._contains_any(normalized, CREDIT_MEMO_KEYWORDS):
            return "CREDIT_MEMO"
        if self._contains_any(normalized, CREDIT_PROPOSAL_ROUTE_KEYWORDS):
            return "CREDIT_PROPOSAL_AGENT"
        if self._contains_any(
            normalized,
            RISK_ASSESSMENT_ROUTE_KEYWORDS
            + ["underwriting", "repayment", "approve", "approval"],
        ):
            return "RISK_ASSESSMENT_AGENT"
        if self._contains_any(normalized, CREDIT_RELATIONSHIP_ROUTE_KEYWORDS):
            return "CREDIT_RELATIONSHIP_AGENT"
        if self._contains_any(
            normalized,
            BUSINESS_ROUTE_KEYWORDS + ["supplier", "customer", "market"],
        ):
            return "BUSINESS_ACTIVITY_AGENT"
        if has_file or self._contains_any(
            normalized,
            FINANCIAL_ROUTE_KEYWORDS
            + [
                "financial statement",
                "balance sheet",
                "income statement",
                "cash flow",
                "bank statement",
                "vat",
                "ledger",
            ],
        ):
            return "FINANCIAL_ANALYSIS_AGENT"
        # No keyword matched and no file. Requests like this are turned away
        # after discovery, so reaching here means the caller drove the graph
        # some other way — answer with the default rather than inventing a
        # route the workflow has no branch for.
        return DEFAULT_ROUTE

    def _analyze_evidence_gaps(
        self,
        documents: list[ClassifiedDocument],
        route: str,
    ) -> dict[str, Any]:
        inventory = {
            "financial_documents": [],
            "business_activity_documents": [],
            "credit_relationship_documents": [],
            "credit_proposal_documents": [],
            "risk_assessment_documents": [],
            "general_context": [],
        }
        agent_to_bucket = {
            "FINANCIAL_ANALYSIS_AGENT": "financial_documents",
            "BUSINESS_ACTIVITY_AGENT": "business_activity_documents",
            "CREDIT_RELATIONSHIP_AGENT": "credit_relationship_documents",
            "CREDIT_PROPOSAL_AGENT": "credit_proposal_documents",
            "RISK_ASSESSMENT_AGENT": "risk_assessment_documents",
            "GENERAL_CONTEXT": "general_context",
        }
        for doc in documents:
            if doc.agent == "GENERAL_CONTEXT":
                inventory["general_context"].append(doc.filename)
                continue
            # Count the document toward every agent it's real evidence for, not
            # just its primary label, so "missing evidence" doesn't fire for
            # coverage that's actually present in a combined document.
            for agent in doc.relevant_agents:
                bucket = agent_to_bucket.get(agent, "general_context")
                inventory[bucket].append(doc.filename)

        required = {
            "FINANCIAL_ANALYSIS_AGENT": ["financial_documents"],
            "BUSINESS_ACTIVITY_AGENT": ["business_activity_documents"],
            "RISK_ASSESSMENT_AGENT": [
                "financial_documents",
                "business_activity_documents",
            ],
            "CREDIT_MEMO": [
                "financial_documents",
                "business_activity_documents",
            ],
        }.get(route, [])

        missing = []
        for evidence_type in required:
            if inventory.get(evidence_type):
                continue
            missing.append(
                {
                    "type": evidence_type,
                    "severity": (
                        "high"
                        if evidence_type == "financial_documents"
                        else "medium"
                    ),
                    "can_continue_without_it": (
                        evidence_type != "financial_documents"
                    ),
                    "reason": self._gap_reason(evidence_type),
                }
            )

        can_proceed = not any(
            item["severity"] == "high"
            and not item["can_continue_without_it"]
            for item in missing
        )
        available = (
            ", ".join(
                f"{bucket}: {len(files)}"
                for bucket, files in inventory.items()
                if files
            )
            or "none"
        )
        missing_summary = (
            ", ".join(item["type"] for item in missing)
            if missing
            else "none"
        )
        return {
            "evidence_inventory": inventory,
            "missing_evidence": missing,
            "recommended_actions": ["run_available_analysis_agents"],
            "can_proceed": can_proceed,
            "summary": (
                f"Route {route}. Available evidence: {available}. "
                f"Missing evidence: {missing_summary}."
            ),
        }

    def _build_execution_plan(
        self,
        decision: dict[str, Any],
        gap_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        route = decision["route"]
        workflow_mode = decision.get(
            "workflow_mode",
            self._workflow_mode_for_route(route),
        )
        requires_credit_memo = route == "CREDIT_MEMO"
        if route == "CREDIT_MEMO":
            agents = [
                "BUSINESS_ACTIVITY_AGENT",
                "CREDIT_RELATIONSHIP_AGENT",
                "FINANCIAL_ANALYSIS_AGENT",
                "CREDIT_PROPOSAL_AGENT",
                "RISK_ASSESSMENT_AGENT",
                "CREDIT_MEMO",
            ]
            order = [
                "business_activity_analysis",
                "credit_relationship_analysis",
                "financial_analysis",
                "credit_proposal_calculation",
                "risk_assessment",
                "credit_memo_composition",
                "reflection",
            ]
        elif route == "RISK_ASSESSMENT_AGENT":
            agents = ["RISK_ASSESSMENT_AGENT"]
            order = ["risk_assessment", "reflection"]
        elif route == "CREDIT_PROPOSAL_AGENT":
            agents = ["CREDIT_PROPOSAL_AGENT"]
            order = ["credit_proposal_calculation", "reflection"]
        else:
            # Every remaining route is a single specialist. Unknown ids land
            # here too and are answered with the default rather than a plan
            # naming an agent that no longer exists.
            agents = [route if route in self.ROUTE_AGENTS else DEFAULT_ROUTE]
            order = [
                (
                    "financial_analysis"
                    if agents[0] == "FINANCIAL_ANALYSIS_AGENT"
                    else "credit_relationship_analysis"
                    if agents[0] == "CREDIT_RELATIONSHIP_AGENT"
                    else "business_activity_analysis"
                ),
                "reflection",
            ]

        return {
            "plan_version": "notebook-local-v1",
            "intent": workflow_mode,
            "route": route,
            "workflow_mode": workflow_mode,
            "required_agents": agents,
            "execution_order": order,
            "requires_credit_memo": requires_credit_memo,
            "can_answer_now": bool(gap_analysis.get("can_proceed", True)),
            "missing_information": gap_analysis.get("missing_evidence", []),
            "reasoning": decision.get("reasoning", ""),
        }

    def _maybe_run_web_search(
        self,
        query: str,
        conversation_history: list[dict[str, str]],
        decision: dict[str, Any],
        document_summary: str,
        steps: list[str],
    ) -> str:
        if not self.web_search_agent:
            return ""
        steps.append("Running WEB_SEARCH_AGENT")
        web_query = f"{query}\n\n{document_summary}"
        return self.web_search_agent.process_web_search_results(
            web_query,
            conversation_history,
        )

    def _run_single_agent(
        self,
        agent_name: str,
        input_text: str,
        history_context: str,
        decision: dict[str, Any],
        documents: list[ClassifiedDocument],
        web_context: str,
        execution_plan: dict[str, Any],
        gap_analysis: dict[str, Any],
        steps: list[str],
    ) -> dict[str, Any]:
        steps.append(f"Running {agent_name}")
        user_input = self._build_user_input(
            input_text,
            documents,
            history_context,
            agent_name,
            web_context,
            gap_analysis,
        )
        if agent_name == "FINANCIAL_ANALYSIS_AGENT":
            agent = FinancialAnalysis(self.config.analysis_llm)
        elif agent_name == "CREDIT_RELATIONSHIP_AGENT":
            agent = CreditRelationshipAnalysis(self.config.analysis_llm)
        elif agent_name == "RISK_ASSESSMENT_AGENT":
            agent = RiskAssessment(self.config.analysis_llm)
        elif agent_name == "CREDIT_PROPOSAL_AGENT":
            agent = CreditProposalAnalysis(self.config.analysis_llm)
        else:
            agent = BusinessActivityAnalysis(self.config.analysis_llm)
        # Namespaced even though a single-agent run has nobody to collide with:
        # the labels a reviewer reads in the .md should mean the same thing here
        # as in a full memo, and this is the one path all five modes share.
        response = namespace_footnotes(
            extract_text_from_agent_output(agent.analyze(user_input)),
            AGENT_LABEL_PREFIXES.get(agent_name, ""),
        )

        return self._finalize(
            input_text,
            response,
            agent_name,
            decision,
            documents,
            {agent_name: response},
            web_context,
            history_context,
            execution_plan,
            gap_analysis,
            steps,
        )

    def _run_credit_memo_workflow(
        self,
        input_text: str,
        history_context: str,
        decision: dict[str, Any],
        documents: list[ClassifiedDocument],
        web_context: str,
        execution_plan: dict[str, Any],
        gap_analysis: dict[str, Any],
        steps: list[str],
    ) -> dict[str, Any]:
        # Business, credit-relationship and financial analysis each read the same
        # documents and are independent, so run them concurrently (bounded by the
        # provider rate limit via LLM_MAX_CONCURRENCY). RISK + the memo composer
        # below still receive all three outputs, so report quality is unchanged.
        steps.append(
            "Running BUSINESS_ACTIVITY / CREDIT_RELATIONSHIP / FINANCIAL / "
            "CREDIT_PROPOSAL_AGENT agents in parallel "
            f"(max_concurrency={self.config.max_concurrency})"
        )

        # Each specialist numbers its own footnotes from 1, so their labels are
        # namespaced the moment the text exists — before the composer merges
        # them and a shared "[^1]" would silently resolve to one agent's source.
        def _run_business() -> str:
            business_input = self._build_user_input(
                input_text,
                documents,
                history_context,
                "BUSINESS_ACTIVITY_AGENT",
                web_context,
                gap_analysis,
            )
            return namespace_footnotes(
                extract_text_from_agent_output(
                    BusinessActivityAnalysis(self.config.analysis_llm).analyze(
                        business_input
                    )
                ),
                AGENT_LABEL_PREFIXES["BUSINESS_ACTIVITY_AGENT"],
            )

        def _run_credit_relationship() -> str:
            credit_relationship_input = f"""
            {self._build_user_input(
                input_text,
                documents,
                history_context,
                "CREDIT_RELATIONSHIP_AGENT",
                web_context,
                gap_analysis,
            )}

            Instruction:
            - Use T24 and CIC/bureau database tools when customer identifiers are
              available.
            - If tool data is unavailable, clearly state the limitation.
            """
            return namespace_footnotes(
                extract_text_from_agent_output(
                    CreditRelationshipAnalysis(
                        self.config.analysis_llm
                    ).analyze(credit_relationship_input)
                ),
                AGENT_LABEL_PREFIXES["CREDIT_RELATIONSHIP_AGENT"],
            )

        def _run_financial() -> str:
            financial_input = self._build_user_input(
                input_text,
                documents,
                history_context,
                "FINANCIAL_ANALYSIS_AGENT",
                web_context,
                gap_analysis,
            )
            # Financial analysis runs with require_citations = False, so this is
            # a no-op today. Kept so the one agent that opts out is not also the
            # one place the pipeline silently differs.
            return namespace_footnotes(
                extract_text_from_agent_output(
                    FinancialAnalysis(self.config.analysis_llm).analyze(
                        financial_input
                    )
                ),
                AGENT_LABEL_PREFIXES["FINANCIAL_ANALYSIS_AGENT"],
            )

        def _run_proposal() -> str:
            proposal_input = self._build_user_input(
                input_text,
                documents,
                history_context,
                "CREDIT_PROPOSAL_AGENT",
                web_context,
                gap_analysis,
            )
            return namespace_footnotes(
                extract_text_from_agent_output(
                    CreditProposalAnalysis(self.config.analysis_llm).analyze(
                        proposal_input
                    )
                ),
                AGENT_LABEL_PREFIXES["CREDIT_PROPOSAL_AGENT"],
            )

        max_workers = max(1, min(4, self.config.max_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            business_future = executor.submit(_run_business)
            credit_relationship_future = executor.submit(_run_credit_relationship)
            financial_future = executor.submit(_run_financial)
            proposal_future = executor.submit(_run_proposal)
            business_text = business_future.result()
            credit_relationship_text = credit_relationship_future.result()
            financial_text = financial_future.result()
            credit_proposal_text = proposal_future.result()

        steps.append("Running RISK_ASSESSMENT_AGENT")
        risk_context = self._build_user_input(
            input_text,
            documents,
            history_context,
            "RISK_ASSESSMENT_AGENT",
            web_context,
            gap_analysis,
        )
        risk_input = f"""
        {risk_context}

        Business activity analysis from BUSINESS_ACTIVITY_AGENT:
        {business_text}

        Credit relationship analysis from CREDIT_RELATIONSHIP_AGENT:
        {credit_relationship_text}

        Financial analysis from FINANCIAL_ANALYSIS_AGENT:
        {financial_text}

        Credit proposal from CREDIT_PROPOSAL_AGENT:
        {credit_proposal_text}
        """
        risk_text = namespace_footnotes(
            extract_text_from_agent_output(
                RiskAssessment(self.config.analysis_llm).analyze(risk_input)
            ),
            AGENT_LABEL_PREFIXES["RISK_ASSESSMENT_AGENT"],
        )

        steps.append("Running CREDIT_MEMO_COMPOSER_AGENT")
        response = CreditMemoComposerAgent(
            self.config.credit_memo_llm,
            max_input_chars=self.config.agent_input_char_budgets.get(
                "CREDIT_MEMO",
                80_000,
            ),
        ).compose(
            input_text=input_text,
            business_analysis=business_text,
            credit_relationship_analysis=credit_relationship_text,
            financial_analysis=financial_text,
            credit_proposal=credit_proposal_text,
            risk_assessment=risk_text,
        )
        sub_outputs = {
            "BUSINESS_ACTIVITY_AGENT": business_text,
            "CREDIT_RELATIONSHIP_AGENT": credit_relationship_text,
            "FINANCIAL_ANALYSIS_AGENT": financial_text,
            "CREDIT_PROPOSAL_AGENT": credit_proposal_text,
            "RISK_ASSESSMENT_AGENT": risk_text,
            "CREDIT_MEMO": response,
        }
        return self._finalize(
            input_text,
            response,
            (
                "BUSINESS_ACTIVITY_AGENT, CREDIT_RELATIONSHIP_AGENT, "
                "FINANCIAL_ANALYSIS_AGENT, CREDIT_PROPOSAL_AGENT, "
                "RISK_ASSESSMENT_AGENT, CREDIT_MEMO_COMPOSER_AGENT"
            ),
            decision,
            documents,
            sub_outputs,
            web_context,
            history_context,
            execution_plan,
            gap_analysis,
            steps,
        )

    def _finalize(
        self,
        input_text: str,
        response: str,
        agent_name: str,
        decision: dict[str, Any],
        documents: list[ClassifiedDocument],
        sub_agent_outputs: dict[str, str],
        web_context: str,
        history_context: str,
        execution_plan: dict[str, Any],
        gap_analysis: dict[str, Any],
        steps: list[str],
    ) -> dict[str, Any]:
        # First, so everything below (chart insertion, footnote/template-leak
        # findings) reads a report whose citations already sit in one list at
        # the end, not scattered mid-section. Returns the audit too — gathering
        # the definitions collapses repeated labels, so a checker running
        # afterwards could no longer see that two agents had claimed the same
        # one.
        response, footnote_audit = consolidate_footnotes(response)
        # Same reasoning as above, for a different renderer quirk: a bullet
        # list glued to the line above it with no blank line renders as a
        # bare "-" instead of a <ul>. Fixing it here means every "Nhận định"
        # bullet is correct on the page even if a specialist forgot the blank
        # line the prompt asks for.
        response = ensure_blank_line_before_lists(response)
        # Credit relationship's own analysis call transcribes VAT revenue into
        # a ```vat-doanh-thu block for _build_debt_chart_block to read further
        # down (see vat_revenue.py) — an internal data channel, never meant
        # for the reader. Parsed from sub_agent_outputs below, so stripping it
        # here only guards against the composer having copied it verbatim
        # into a merged multi-agent response.
        response = strip_vat_revenue_block(response)

        if self.guardrails:
            allowed, checked_response = self.guardrails.check_output(
                response,
                input_text,
            )
            if not allowed:
                agent_name = "OUTPUT_GUARDRAILS"
            response = checked_response

        # Convert all VNĐ amounts to tỷ VNĐ for display, after the output
        # safety check above has already seen the raw-number response.
        response = convert_amounts_in_text(response)
        # Prompt rules are not enforcement: report the citations that did not
        # resolve, rather than quietly dropping or inventing them.
        response += self._format_footnote_findings(footnote_audit)
        response += self._format_template_findings(response)
        # Last, and deliberately so: everything above this line reads or edits
        # what the *model* wrote, and this block is written by the pipeline from
        # extracted JSON. It is not the checkers' business, and the text rewriter
        # has no business in it either.
        #
        # Measured rather than assumed — with today's formatting the block
        # survives all three untouched: convert_amounts_in_text only matches
        # thousands-grouped integers and every value here carries two decimals,
        # and both assertion checks stay silent on it. So this ordering is a
        # guard against a future change to either side, not a fix for a live bug.
        chart_block, chart_title = self._build_debt_chart_block(
            documents,
            sub_agent_outputs,
        )
        if chart_block:
            response, anchor = self._insert_debt_chart(
                response,
                chart_block,
                chart_title,
            )
            steps.append(
                "Inserted debt/revenue chart "
                + (
                    "at the end (no credit-relationship heading found)"
                    if anchor == "appended"
                    else f'under the heading matching "{anchor}"'
                )
            )
        # detect_loan_program is a pure string match over the same two strings
        # the classify node used (input guardrails pass `query` through
        # untouched), so recomputing here is exact and avoids threading the
        # detection dict through five graph nodes and three runner signatures.
        # The documents carry the program that was actually applied, so cross-
        # check rather than trust the recomputation blindly.
        detection = detect_loan_program(input_text, history_context)
        applied = next((doc.loan_program for doc in documents), "")
        if documents and (detection["program"] or "") != applied:
            detection = {
                "program": applied or None,
                "matched_alias": "",
                "candidates": [],
                "source": "applied-at-classification",
            }
        # Throttling is otherwise indistinguishable from a hang: report how many
        # LLM calls the run made and how long the rate limiter held them back.
        rate_limit = shared_rate_limiter().snapshot()
        return self._build_state(
            input_text,
            response,
            agent_name,
            decision,
            documents,
            sub_agent_outputs,
            execution_plan,
            gap_analysis,
            web_context,
            steps
            + [
                f"LLM: {rate_limit['llm_calls']} calls at a "
                f"{rate_limit['requests_per_minute']}/min limit — this run "
                f"could not finish faster than "
                f"{rate_limit['minimum_seconds_for_these_calls']}s",
                "Built final response",
            ],
            self._build_document_selections(documents),
            self._build_financial_metrics_data(documents),
            loan_program=applied,
            loan_program_detection=detection,
            rate_limit=rate_limit,
            credit_need=self._build_credit_need_data(documents),
        )

    @staticmethod
    def _format_template_findings(response: str) -> str:
        """Flag layout scaffolding or placeholders copied into the report."""

        findings = check_template_leakage(response)
        if not findings:
            return ""
        lines = ["", "**Lỗi bám mẫu báo cáo:**", ""]
        lines += [f"- {finding}" for finding in findings]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_footnote_findings(audit: FootnoteAudit) -> str:
        """Append the citations that did not resolve against their definitions.

        Takes the audit rather than the text: it was produced by the same pass
        that gathered the definitions, which is the only point where a label
        claimed by two agents is still visible.
        """

        findings = format_footnote_findings(audit)
        if not findings:
            return ""
        lines = [
            "",
            "**Chú thích nguồn:**",
            "",
        ]
        lines += [f"- {finding}" for finding in findings]
        return "\n".join(lines) + "\n"

    def _build_user_input(
        self,
        input_text: str,
        documents: list[ClassifiedDocument],
        history_context: str,
        target_agent: str,
        web_context: str,
        gap_analysis: dict[str, Any],
    ) -> str:
        budget = self.config.agent_input_char_budgets.get(target_agent, 12_000)
        language_instruction = (
            "Response language instruction:\n"
            "- Answer in the same language as the current user request.\n"
            "- If the current user request is Vietnamese, answer fully in "
            "Vietnamese.\n"
            "- If the user request has no clear language, answer in Vietnamese "
            "by default.\n\n"
        )
        base = (
            "Recent conversation history:\n"
            f"{history_context or 'No previous conversation.'}\n\n"
            f"{language_instruction}"
            "Evidence sufficiency summary:\n"
            f"{gap_analysis.get('summary', '')}\n\n"
            "Current user request:\n"
            f"{input_text}"
        )
        if web_context:
            base += (
                "\n\nExternal business registry and industry context from "
                f"web search:\n{web_context}"
            )

        selected = self._select_documents_for_agent(documents, target_agent)
        # Only successfully-extracted documents become evidence; failed ones are
        # still listed in the document summary but must not pollute agent input.
        usable = [doc for doc in selected if doc.extraction_status == "success"]

        def _is_primary(doc: ClassifiedDocument) -> bool:
            """Required evidence for this agent, per the matrix."""

            return doc.agent_relevance.get(target_agent) == "R"

        source_list_block = self._build_source_list_block(usable)
        metrics_block = self._build_financial_metrics_block(
            documents,
            target_agent,
        )
        # State the periods explicitly: several BCTC files overlap by a year, so
        # the merged set (e.g. 2 files -> 3 years) does not match the sample
        # column count in the layout. Telling the agent removes the guesswork.
        reads_bctc_json = target_agent in self.BCTC_JSON_STATEMENTS
        if reads_bctc_json:
            periods = (self._build_financial_metrics_data(usable) or {}).get("years")
            if periods:
                base += (
                    "\n\nCác kỳ báo cáo có dữ liệu (dùng đúng số cột này cho mọi "
                    f"bảng theo năm, thứ tự tăng dần): {', '.join(periods)}"
                )
        bctc_block = (
            self._build_bctc_structured_block(
                usable,
                self.BCTC_JSON_STATEMENTS[target_agent],
            )
            if reads_bctc_json
            else ""
        )
        reads_proposal_json = target_agent in self.PROPOSAL_JSON_AGENTS
        proposal_block = (
            self._build_proposal_structured_block(usable)
            if reads_proposal_json
            else ""
        )
        reads_cic_s10a_json = target_agent in self.CIC_S10A_JSON_AGENTS
        cic_s10a_block = (
            self._build_cic_s10a_structured_block(usable)
            if reads_cic_s10a_json
            else ""
        )
        reads_cic_r21_json = target_agent in self.CIC_R21_JSON_AGENTS
        cic_r21_block = (
            self._build_cic_r21_structured_block(usable)
            if reads_cic_r21_json
            else ""
        )
        reads_sitevisit_json = target_agent in self.SITEVISIT_JSON_AGENTS
        sitevisit_block = (
            self._build_sitevisit_structured_block(usable)
            if reads_sitevisit_json
            else ""
        )
        credit_need_block = self._build_credit_need_block(documents, target_agent)
        industry_block = (
            prompt_blocks._build_industry_knowledge_block(
                self.config, usable, input_text, target_agent
            )
            if target_agent in self.INDUSTRY_KNOWLEDGE_AGENTS
            else ""
        )
        # The units only clash once two of these blocks are present, so the
        # warning appears exactly then — a prompt carrying one block needs no
        # reconciling and should not gain a line telling it otherwise.
        money_blocks = sum(
            1
            for block in (
                metrics_block,
                bctc_block,
                proposal_block,
                cic_s10a_block,
                cic_r21_block,
                # Counted with the rest: next year's plan carries revenue and
                # COGS, so it can disagree about units with any block above it.
                sitevisit_block,
                credit_need_block,
            )
            if block
        )
        unit_warning = (
            f"{self.MIXED_UNIT_WARNING}\n\n" if money_blocks > 1 else ""
        )
        # Every block spends characters on its own header (filename, document
        # type, relevance, extraction status) plus a separator, before any
        # content. That overhead has to come out of the budget up front: without
        # it the assembled prompt overshoots and the final truncate_text lops
        # whole documents off the end — the risk agent, which the matrix feeds
        # every document type, loses its last files entirely.
        block_overhead = sum(
            len(self._document_block_header(doc, target_agent)) for doc in usable
        ) + len(self.DOC_BLOCK_SEPARATOR) * max(0, len(usable) - 1)
        remaining = max(
            1_000,
            budget
            - len(base)
            - len(source_list_block)
            - len(metrics_block)
            - len(bctc_block)
            - len(self.DOC_SECTION_HEADER)
            - len(proposal_block)
            - len(cic_s10a_block)
            - len(cic_r21_block)
            - len(sitevisit_block)
            - len(credit_need_block)
            - len(industry_block)
            - len(unit_warning)
            - block_overhead,
        )
        # Required evidence gets more budget than optional evidence, so a
        # document the matrix marks optional for this agent can't crowd out the
        # documents it actually needs.
        weight_sum = sum(
            self.PRIMARY_DOC_BUDGET_WEIGHT
            if _is_primary(doc)
            else self.SECONDARY_DOC_BUDGET_WEIGHT
            for doc in usable
        ) or 1
        unit_budget = remaining / weight_sum

        def _doc_budget(doc: ClassifiedDocument) -> int:
            weight = (
                self.PRIMARY_DOC_BUDGET_WEIGHT
                if _is_primary(doc)
                else self.SECONDARY_DOC_BUDGET_WEIGHT
            )
            return max(1_000, int(unit_budget * weight))

        blocks = []
        for doc in usable:
            # A successfully-extracted BCTC doc is represented by its
            # structured JSON (see bctc_block above), not its raw OCR dump —
            # that's the whole point of the extraction pass. Gated on the same
            # table as the block itself, because sending one without the other
            # either bills the content twice or loses it entirely. Any other doc
            # (not BCTC, or extraction failed/unavailable) keeps raw content
            # so no evidence is ever silently dropped.
            if reads_bctc_json and doc.is_bctc and doc.bctc_extraction:
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    "— xem [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT] bên dưới."
                )
            elif reads_proposal_json and doc.is_proposal and doc.proposal_extraction:
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    "— xem [DỮ LIỆU ĐỀ NGHỊ CẤP TÍN DỤNG] bên dưới."
                )
            elif reads_cic_s10a_json and doc.is_cic_s10a and doc.cic_s10a_extraction:
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    f"— xem {self.CIC_S10A_BLOCK_HEADING} bên dưới."
                )
            elif reads_cic_r21_json and doc.is_cic_r21 and doc.cic_r21_extraction:
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    f"— xem {self.CIC_R21_BLOCK_HEADING} bên dưới."
                )
            elif (
                reads_sitevisit_json
                and doc.is_sitevisit
                and doc.sitevisit_extraction
            ):
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    f"— xem {self.SITEVISIT_BLOCK_HEADING} bên dưới."
                )
            else:
                content_section = "\n".join(
                    [
                        "Extracted document content:",
                        truncate_text(doc.content, _doc_budget(doc)),
                    ]
                )
            blocks.append(
                self._document_block_header(doc, target_agent) + content_section
            )
        docs_text = self.DOC_BLOCK_SEPARATOR.join(blocks)
        # Emitted only when there is something to say: an empty slot would add
        # blank lines to every prompt that has no credit application, churning
        # them for nothing.
        proposal_section = f"{proposal_block}\n\n" if proposal_block else ""
        cic_s10a_section = f"{cic_s10a_block}\n\n" if cic_s10a_block else ""
        cic_r21_section = f"{cic_r21_block}\n\n" if cic_r21_block else ""
        sitevisit_section = f"{sitevisit_block}\n\n" if sitevisit_block else ""
        credit_need_section = (
            f"{credit_need_block}\n\n" if credit_need_block else ""
        )
        industry_section = f"{industry_block}\n\n" if industry_block else ""
        return truncate_text(
            (
                f"{base}\n\n"
                # Near the top on purpose: the final truncate trims the tail, and
                # a source list that got cut is the exact failure this replaced.
                f"{source_list_block}\n\n"
                f"{unit_warning}"
                f"{metrics_block}\n\n"
                f"{bctc_block}\n\n"
                f"{proposal_section}"
                f"{cic_s10a_section}"
                f"{cic_r21_section}"
                f"{sitevisit_section}"
                f"{credit_need_section}"
                f"{industry_section}"
                f"{self.DOC_SECTION_HEADER}"
                f"{docs_text}"
            ),
            budget,
        )






    @classmethod
    def _build_credit_need_data(
        cls,
        documents: list[ClassifiedDocument],
    ) -> dict[str, Any]:
        """The credit-need table as data, for the run log rather than a prompt.

        Recomputed rather than shared with _build_credit_need_block, matching
        how _build_financial_metrics_block and _build_financial_metrics_data
        already work: the two are called from different places in the graph, and
        the arithmetic is free next to the LLM calls around it.
        """

        usable = [doc for doc in documents if doc.extraction_status == "success"]
        if not usable:
            return {}
        try:
            calculator = FinancialRatioCalculator()
            payload = [asdict(doc) for doc in usable]
            yearly_metrics = calculator.extract_yearly_metrics(payload)
            if not yearly_metrics:
                return {}
            return build_credit_need_table(
                yearly_metrics,
                calculator.compute_ratios(yearly_metrics),
                next((d.proposal_extraction for d in usable
                      if d.is_proposal and d.proposal_extraction), None),
                next((d.sitevisit_extraction for d in usable
                      if d.is_sitevisit and d.sitevisit_extraction), None),
                [d.cic_s10a_extraction for d in usable
                 if d.is_cic_s10a and d.cic_s10a_extraction],
            ).as_dict()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _build_financial_metrics_data(
        documents: list[ClassifiedDocument],
    ) -> dict[str, Any]:
        """Structured form of the deterministic ratio computation.

        The agent gets the markdown block; this returns the same numbers as data
        so a run can be audited afterwards (which line items were matched, what
        the ratios came out as, which sanity checks failed).
        """

        usable = [
            doc for doc in documents if doc.extraction_status == "success"
        ]
        if not usable:
            return {}
        try:
            calculator = FinancialRatioCalculator()
            payload = [asdict(doc) for doc in usable]
            yearly_metrics = calculator.extract_yearly_metrics(payload)
            if not yearly_metrics:
                return {}
            years = sorted(yearly_metrics)
            return {
                "unit": "VNĐ",
                "years": years,
                "yearly_metrics": yearly_metrics,
                "ratios": calculator.compute_ratios(yearly_metrics),
                "validation_warnings": calculator._validation_warnings(
                    years,
                    yearly_metrics,
                ),
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}




    @classmethod
    def _insert_debt_chart(
        cls,
        response: str,
        block: str,
        title: str,
    ) -> tuple[str, str]:
        """Place the chart under the credit-relationship heading.

        Returns ``(text, where)``; ``where`` names the anchor that matched so the
        step log can say whether the chart landed in its section or was appended
        as a fallback.

        ``title`` is only used by that fallback, and is taken from the caller
        rather than the class constant so the heading cannot promise a revenue
        series the block itself leaves out.

        Appending is the last resort rather than the failure case: a chart at the
        end of the memo is worse than one in its section, but far better than a
        chart that silently disappears because the composer reworded a heading.
        """

        if not block:
            return response, ""
        lines = (response or "").splitlines()
        for anchor in cls.DEBT_CHART_ANCHORS:
            for index, line in enumerate(lines):
                if not line.lstrip().startswith("#"):
                    continue
                if anchor in normalize_text(line):
                    return (
                        "\n".join(
                            lines[: index + 1] + ["", block] + lines[index + 1:]
                        ),
                        anchor,
                    )
        return (
            f"{response}\n\n## {title}\n\n{block}\n",
            "appended",
        )





    @staticmethod
    def _select_documents_for_agent(
        documents: list[ClassifiedDocument],
        target_agent: str,
    ) -> list[ClassifiedDocument]:
        # Membership comes from the matrix, not from which agent "owns" the
        # document: one document legitimately feeds several agents (a BCTC is
        # evidence for both FINANCIAL and RISK). Required evidence goes first so
        # it wins the char budget when many files are uploaded.
        required: list[ClassifiedDocument] = []
        optional: list[ClassifiedDocument] = []
        # Documents that matched no matrix type are shared with every agent, so
        # a classification miss degrades to "everyone sees it" instead of
        # silently hiding evidence from the agent that needed it.
        unmatched: list[ClassifiedDocument] = []
        for doc in documents:
            level = doc.agent_relevance.get(target_agent)
            if level == "R":
                required.append(doc)
            elif level:
                optional.append(doc)
            elif doc.agent == "GENERAL_CONTEXT":
                unmatched.append(doc)
        return required + optional + unmatched

    @classmethod
    def _build_document_selections(
        cls,
        documents: list[ClassifiedDocument],
    ) -> dict[str, dict[str, list[str]]]:
        """Snapshot, per specialist agent, exactly what _build_user_input would
        feed it — for monitoring/testing, not for prompt construction itself.
        Recomputed via the same _select_documents_for_agent used at call time,
        so it can never drift from the real selection."""

        selections: dict[str, dict[str, list[str]]] = {}
        for target_agent in sorted(VALID_DOCUMENT_AGENTS - {"GENERAL_CONTEXT"}):
            selected = cls._select_documents_for_agent(documents, target_agent)
            selections[target_agent] = {
                "required": [
                    f"{doc.filename} [{doc.document_type}]"
                    for doc in selected
                    if doc.agent_relevance.get(target_agent) == "R"
                ],
                "optional": [
                    f"{doc.filename} [{doc.document_type}]"
                    for doc in selected
                    if doc.agent_relevance.get(target_agent) == "O"
                ],
                "unmatched_shared": [
                    doc.filename
                    for doc in selected
                    if target_agent not in doc.agent_relevance
                ],
            }
        return selections

    @staticmethod
    def _format_history(
        history: list[dict[str, str]],
        limit: int = 20,
    ) -> str:
        if not history:
            return ""
        lines = []
        for item in history[-limit:]:
            content = truncate_text(item.get("content", ""), 1_000)
            lines.append(f"{item.get('role', 'user')}: {content}")
        return "\n".join(lines)

    # (flag attribute, result attribute, label) for each structured-extraction
    # pass, so the summary tag is written once instead of once per pass. A third
    # pass added later shows up here and nowhere else.
    EXTRACTION_PASSES = (
        ("is_bctc", "bctc_extraction", "bctc_extraction_error", "BCTC"),
        (
            "is_proposal",
            "proposal_extraction",
            "proposal_extraction_error",
            "ĐỀ NGHỊ",
        ),
        (
            "is_cic_s10a",
            "cic_s10a_extraction",
            "cic_s10a_extraction_error",
            "CIC S10A",
        ),
        (
            "is_cic_r21",
            "cic_r21_extraction",
            "cic_r21_extraction_error",
            "CIC R21",
        ),
        (
            "is_sitevisit",
            "sitevisit_extraction",
            "sitevisit_extraction_error",
            "KHẢO SÁT",
        ),
    )

    @classmethod
    def _extraction_tags(cls, doc: ClassifiedDocument) -> str:
        """Mark which structured extractions ran on a document, and which failed.

        A failed extraction silently drops the agent back to raw OCR — the report
        still comes out, just without the structured figures — so the failure has
        to be visible in the summary the decision LLM reads.
        """

        tags = []
        for flag_attr, _, error_attr, label in cls.EXTRACTION_PASSES:
            if not getattr(doc, flag_attr):
                continue
            # Keyed on the error, not on a missing result: this summary is built
            # twice, and the first time — for the routing node — no pass has run
            # yet. Testing the result alone would report every document as a
            # failed extraction at that point, which is the opposite of true.
            if getattr(doc, error_attr):
                tags.append(f" [{label}, trích xuất lỗi]")
            else:
                tags.append(f" [{label}]")
        return "".join(tags)

    @classmethod
    def _format_document_summary(cls, documents: list[ClassifiedDocument]) -> str:
        lines = []
        for doc in documents:
            routing = (
                ", ".join(
                    f"{agent}:{level}"
                    for agent, level in sorted(doc.agent_relevance.items())
                )
                or "GENERAL_CONTEXT (không khớp loại nào)"
            )
            lines.append(
                f"- {doc.filename}: {doc.document_type or '-'} -> {routing}"
                f"{cls._extraction_tags(doc)} (confidence={doc.confidence:.2f}, "
                f"extraction={doc.extraction_status})"
            )
        return "\n".join(lines)

    @staticmethod
    def _gap_reason(evidence_type: str) -> str:
        reasons = {
            "financial_documents": (
                "Financial analysis needs financial statements, ledgers, "
                "tax filings, bank statements, or equivalent finance documents."
            ),
            "business_activity_documents": (
                "Business activity analysis is stronger with contracts, "
                "invoices, supplier/customer lists, production, or sales documents."
            ),
            "credit_relationship_documents": (
                "Credit relationship analysis can use uploaded T24/CIC files, "
                "but normally queries T24 and CIC data through database tools."
            ),
            "credit_proposal_documents": (
                "Credit proposal analysis is stronger with proposed limit, "
                "facility terms, tenor, pricing, collateral, or repayment plan."
            ),
            "risk_assessment_documents": (
                "Risk assessment is stronger with loan proposal, collateral, "
                "repayment source, or credit memo documents."
            ),
        }
        return reasons.get(evidence_type, "Relevant evidence was not found.")

    @staticmethod
    def _missing_evidence_response(
        input_text: str,
        gap_analysis: dict[str, Any],
    ) -> str:
        missing = gap_analysis.get("missing_evidence", [])
        lines = [
            f"- **{item.get('type', 'evidence')}**: "
            f"{item.get('reason', 'Missing evidence.')}"
            for item in missing
        ]
        if Supervisor._prefer_vietnamese_response(input_text):
            return (
                "## Cần bổ sung thêm tài liệu\n\n"
                "Mình chưa thể tiếp tục phân tích vì đang thiếu thông tin "
                "bắt buộc.\n\n"
                "### Tài liệu cần bổ sung\n"
                + ("\n".join(lines) if lines else "- Chưa xác định.")
            )
        return (
            "## Additional Documents Required\n\n"
            "Required evidence is missing.\n\n"
            + ("\n".join(lines) if lines else "- Not specified.")
        )

    @staticmethod
    def _prefer_vietnamese_response(text: str) -> bool:
        normalized = (text or "").lower()
        return "hãy" in normalized or any(
            char in VIETNAMESE_CHARS for char in normalized
        )

    @staticmethod
    def _contains_any(text: str, keywords: list[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _build_state(
        input_text: str,
        response: str,
        agent_name: str,
        decision: dict[str, Any] | None = None,
        documents: list[ClassifiedDocument] | None = None,
        sub_agent_outputs: dict[str, str] | None = None,
        execution_plan: dict[str, Any] | None = None,
        gap_analysis: dict[str, Any] | None = None,
        web_context: str = "",
        steps: list[str] | None = None,
        document_selections: dict[str, dict[str, list[str]]] | None = None,
        financial_metrics: dict[str, Any] | None = None,
        # Appended last, and always passed by keyword: two call sites supply the
        # earlier parameters positionally, so inserting anywhere above would
        # silently shift their arguments.
        loan_program: str = "",
        loan_program_detection: dict[str, Any] | None = None,
        rate_limit: dict[str, Any] | None = None,
        credit_need: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "response": response,
            "agent_name": agent_name,
            "decision": decision or {},
            # Which loan program column of the document matrix was used, and how
            # it was determined — the R/O levels in document_classifications are
            # only interpretable against it.
            "loan_program": loan_program,
            "loan_program_detection": loan_program_detection or {},
            # LLM call count and seconds spent waiting on the rate limiter, so a
            # slow run can be told apart from a stuck one.
            "rate_limit": rate_limit or {},
            "document_classifications": to_dict_list(documents or []),
            # Per-agent snapshot of _select_documents_for_agent's output
            # (primary vs secondary/shared filenames) — for monitoring/testing
            # which documents actually feed each specialist's LLM input.
            "document_selections": document_selections or {},
            # Deterministic ratio computation as data (not just the markdown
            # block) so a finished run can be audited afterwards.
            "financial_metrics": financial_metrics or {},
            # The credit-need table as data. Money stays in đồng here even
            # though the prompt shows tỷ VNĐ — a monitoring dump should carry
            # the figure, not its presentation.
            "credit_need": credit_need or {},
            "sub_agent_outputs": sub_agent_outputs or {},
            "execution_plan": execution_plan or {},
            "gap_analysis": gap_analysis or {},
            "web_context": web_context,
            "steps": steps or [],
        }
