import inspect
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph

from src.agents.calculator.financial_ratio_calculator import FinancialRatioCalculator
from src.utils.common import normalize_text
from src.utils.reading.extractors import extract_document_text
from src.utils.report.formatting import convert_amounts_in_text
from src.utils.report.injection import check_injection_markers
from src.utils.report.template_leak import check_template_leakage
from src.utils.report.citations import (
    AGENT_LABEL_PREFIXES,
    FootnoteAudit,
    consolidate_footnotes,
    format_footnote_findings,
    namespace_footnotes,
)
from src.utils.report.markdown_fixups import (
    ensure_blank_line_before_lists,
    tidy_numbers,
)

from src.config import Config
from src.types import (
    AgentName,
    ClassifiedDocument,
    UnderwritingGraphState,
    WorkflowMode,
    extract_text_from_agent_output,
    to_dict_list,
    truncate_text,
)
from src.agents.documents.document_classification import (
    FILENAME_KEYWORD_WEIGHT,
    build_document_classification_prompt,
    rule_classify_document,
)
from src.agents.documents.document_discovery import (
    box_ids,
    compute_file_hash,
    discover_documents,
    group_from_path,
)
from src.types import VALID_DOCUMENT_AGENTS
from src.agents.documents.document_matrix import (
    resolve_loan_program,
    agent_relevance_for_type,
    get_type,
    load_matrix,
    is_financial_statement_type,
    is_cic_r21_type,
    is_sitevisit_type,
    is_ledger_type,
    is_cic_s10a_type,
    is_proposal_type,
    primary_agent_for_type,
)
from src.agents.extraction.financial_statement_extraction import (
    build_financial_statement_extraction_chain,
    extract_financial_statement_data,
)
from src.agents.extraction.proposal_extraction import (
    build_proposal_extraction_chain,
    extract_proposal_structured_data,
)
from src.agents.extraction.cic_s10a_extraction import (
    build_cic_s10a_extraction_chain,
    extract_cic_s10a_structured_data,
)
from src.agents.extraction.cic_r21_extraction import (
    build_cic_r21_extraction_chain,
    extract_cic_r21_structured_data,
)
from src.agents.extraction.sitevisit_extraction import (
    build_sitevisit_extraction_chain,
    extract_sitevisit_structured_data,
)
from src.agents.extraction.ledger_extraction import (
    build_ledger_extraction_chain,
    extract_ledger_batch,
)
from src.agents.calculator.credit_need_calculator import build_credit_need_table
from src.tools.customer_key import resolve_customer_key
from src.agents import prompt_blocks
from src.agents.extraction.vat_revenue import strip_vat_revenue_block
from src.agents.specialist import SPECIALIST_BY_AGENT

DEFAULT_ROUTE: AgentName = "FINANCIAL_ANALYSIS_AGENT"
DEFAULT_WORKFLOW_MODE: WorkflowMode = "single_financial_analysis"

@dataclass(frozen=True)
class ExtractionPass:
    """One structured-extraction pass, declared once.

    These five passes used to be spelled out in eight places — the chain built
    in __init__, a flag table, five *_JSON_AGENTS constants, the route/consumer
    map, the dispatch loop, five near-identical _extract_*_documents wrappers,
    a summary-tag table, and the reads_*_json gates in _build_user_input. Two
    of those had already drifted to different label vocabularies. Adding a
    sixth pass now means adding one row here.

    ``json_agents`` carries two inseparable consequences: the agent gets the
    JSON block, AND the document's raw OCR is replaced by a pointer to it.
    Enabling one without the other would either bill the same content twice or
    drop it. ``extra_consumers`` is for agents that need the pass to have *run*
    without reading the block — they keep the raw OCR.
    """

    label: str                      
    tag: str                          
    flag_attr: str
    result_attr: str
    error_attr: str
    llm_attr: str
    build_chain: Callable[[Any], Any]
    extract: Callable[..., Any]
    build_block: Callable[..., str]
    heading: str
    json_agents: tuple[str, ...] | dict[str, Any]
    extra_consumers: tuple[str, ...] = ()
    per_agent_block_args: bool = False
    # One call for every matching document at once, instead of one per document.
    # For a pass whose record spans files — the same ledger account can arrive
    # split across two of them — per-document extraction cannot see the whole
    # set. Changes the extract signature, which the guard below checks.
    batch: bool = False
    # A pass whose failure stops the run instead of falling back to raw OCR.
    # The fallback is right for a document with no pass at all — nothing else
    # could be sent. It is wrong once a pass has run and failed: the pipeline
    # then knows the structured read did not work, and hands the model exactly
    # the input the pass exists to avoid. Three BCTC extractions failing that
    # way produced a report written off unreadable OCR numbers, and a payload
    # large enough to break the export.
    #
    # Ledger, CIC S10A and CIC R21 keep the fallback deliberately.
    required: bool = False

    def failures(
        self, docs: list[ClassifiedDocument]
    ) -> list[tuple[ClassifiedDocument, str]]:
        """Documents this pass applies to that came back with no JSON.

        Covers all three ways that happens — the chain raised, no LLM was
        configured for the pass, or the run's extraction budget cut it — because
        each ends the same way: no structured data, raw OCR in the prompt. The
        stored error says which, and each points at a different fix.
        """

        return [
            (doc, getattr(doc, self.error_attr, "") or "No reason recorded.")
            for doc in docs
            if getattr(doc, self.flag_attr) and not getattr(doc, self.result_attr)
        ]

    @property
    def chain_attr(self) -> str:
        return f"{self.result_attr}_chain"

    def block_for(self, agent: str, docs: list[ClassifiedDocument]) -> str:
        """The JSON block this pass contributes to one agent's prompt."""

        if agent not in self.json_agents:
            return ""
        if self.per_agent_block_args:
            return self.build_block(docs, self.json_agents[agent])
        return self.build_block(docs)

    def consumers(self) -> set[str]:
        return set(self.json_agents) | set(self.extra_consumers)


EXTRACTION_PASSES: tuple[ExtractionPass, ...] = (
    ExtractionPass(
        label="BCTC",
        tag="BCTC",
        flag_attr="is_financial_statement",
        result_attr="financial_statement_extraction",
        error_attr="financial_statement_extraction_error",
        llm_attr="financial_statement_extraction_llm",
        build_chain=build_financial_statement_extraction_chain,
        extract=extract_financial_statement_data,
        build_block=prompt_blocks._build_financial_statement_block,
        heading=prompt_blocks.FINANCIAL_STATEMENT_BLOCK_HEADING,
        json_agents={
            "FINANCIAL_ANALYSIS_AGENT": None,
            "CREDIT_PROPOSAL_AGENT": ("income_statement",),
        },
        extra_consumers=prompt_blocks.METRICS_BLOCK_AGENTS,
        per_agent_block_args=True,
        required=True,
    ),
    ExtractionPass(
        label="Proposal",
        tag="ĐỀ NGHỊ",
        flag_attr="is_proposal",
        result_attr="proposal_extraction",
        error_attr="proposal_extraction_error",
        llm_attr="proposal_extraction_llm",
        build_chain=build_proposal_extraction_chain,
        extract=extract_proposal_structured_data,
        build_block=prompt_blocks._build_proposal_structured_block,
        heading=prompt_blocks.PROPOSAL_BLOCK_HEADING,
        json_agents=("CREDIT_PROPOSAL_AGENT",),
        required=True,
    ),
    ExtractionPass(
        label="CIC S10A",
        tag="CIC S10A",
        flag_attr="is_cic_s10a",
        result_attr="cic_s10a_extraction",
        error_attr="cic_s10a_extraction_error",
        llm_attr="cic_s10a_extraction_llm",
        build_chain=build_cic_s10a_extraction_chain,
        extract=extract_cic_s10a_structured_data,
        build_block=prompt_blocks._build_cic_s10a_structured_block,
        heading=prompt_blocks.CIC_S10A_BLOCK_HEADING,
        json_agents=("CREDIT_RELATIONSHIP_AGENT",),
        extra_consumers=("CREDIT_PROPOSAL_AGENT",),
    ),
    ExtractionPass(
        label="CIC R21",
        tag="CIC R21",
        flag_attr="is_cic_r21",
        result_attr="cic_r21_extraction",
        error_attr="cic_r21_extraction_error",
        llm_attr="cic_r21_extraction_llm",
        build_chain=build_cic_r21_extraction_chain,
        extract=extract_cic_r21_structured_data,
        build_block=prompt_blocks._build_cic_r21_structured_block,
        heading=prompt_blocks.CIC_R21_BLOCK_HEADING,
        json_agents=("CREDIT_RELATIONSHIP_AGENT",),
    ),
    ExtractionPass(
        label="Sitevisit",
        tag="KHẢO SÁT",
        flag_attr="is_sitevisit",
        result_attr="sitevisit_extraction",
        error_attr="sitevisit_extraction_error",
        llm_attr="sitevisit_extraction_llm",
        build_chain=build_sitevisit_extraction_chain,
        extract=extract_sitevisit_structured_data,
        build_block=prompt_blocks._build_sitevisit_structured_block,
        heading=prompt_blocks.SITEVISIT_BLOCK_HEADING,
        json_agents=(
            "BUSINESS_ACTIVITY_AGENT",
            "FINANCIAL_ANALYSIS_AGENT",
            "CREDIT_RELATIONSHIP_AGENT",
            "CREDIT_PROPOSAL_AGENT",
        ),
        required=True,
    ),
    ExtractionPass(
        label="Ledger",
        tag="SỔ CHI TIẾT",
        flag_attr="is_ledger",
        result_attr="ledger_extraction",
        error_attr="ledger_extraction_error",
        llm_attr="ledger_extraction_llm",
        build_chain=build_ledger_extraction_chain,
        extract=extract_ledger_batch,
        batch=True,
        build_block=prompt_blocks._build_ledger_structured_block,
        heading=prompt_blocks.LEDGER_BLOCK_HEADING,
        # Every agent the matrix routes a detail ledger to. Each consumes it as
        # JSON, which also replaces the raw grid in their prompt: the grid is a
        # wall of TSV that arrived at the tail of the prompt with no heading,
        # and the record is the same figures under an account each report
        # section can be filled from.
        json_agents=(
            "FINANCIAL_ANALYSIS_AGENT",
            "BUSINESS_ACTIVITY_AGENT",
            # The matrix routes bang_ke_xuat_nhap_ton_cong_no here too, at R
            # under PLO, and the borrowings sheet is bank-by-bank debt that
            # reconciles against CIC S10A. Without this it saw the raw grid.
            "CREDIT_RELATIONSHIP_AGENT",
        ),
    ),
)


# _run_structured_extraction gọi extract(chain, filename, content, path) cho mọi
# pass. Kiểm ngay lúc import vì "extract" khai kiểu Callable[..., Any] — không
# type checker nào thấy được một chữ ký lệch. Chuyện này đã xảy ra thật: một lượt
# dọn dẹp bỏ tham số path "không dùng" khỏi bốn extractor và làm vỡ 4/5 pass giữa
# lượt chạy của khách. Không bộ đo nào bắt được, vì chúng gọi các hàm này trực
# tiếp chứ không đi qua runner. Vỡ lúc import thì rẻ; vỡ giữa lượt chạy thì không.
for _pass in EXTRACTION_PASSES:
    try:
        inspect.signature(_pass.extract).bind(
            *((None, []) if _pass.batch else (None, "", "", ""))
        )
    except TypeError as exc:
        raise TypeError(
            f"pass {_pass.label!r}: {_pass.extract.__name__} does not accept "
            f"the runner's call signature — {exc}"
        ) from exc


_PASS_BY_LABEL = {pass_.label: pass_ for pass_ in EXTRACTION_PASSES}


class _ExtractionBudget:
    """What one run may still spend on extraction.

    Shared across every pass in a run, because the limit is on the run and not
    on any one pass: six passes each staying under their own ceiling is exactly
    the case a per-pass limit fails to catch. A batch pass is one call however
    many documents it reads, so calls and characters are counted separately.

    Refuses rather than truncates. A dossier trimmed silently is the failure
    this system already has too many of; a named skip can be acted on.
    """

    __slots__ = ("calls_left", "chars_left", "skipped", "limit_hit")

    def __init__(self, max_calls: int, max_chars: int) -> None:
        self.calls_left = max_calls
        self.chars_left = max_chars
        self.skipped: list[str] = []
        self.limit_hit = ""

    def take(self, calls: int, chars: int, names: list[str]) -> bool:
        """Charge the budget, or record the skip and return False."""

        if calls > self.calls_left:
            self.limit_hit = self.limit_hit or "max_extraction_calls"
        elif chars > self.chars_left:
            self.limit_hit = self.limit_hit or "max_extraction_input_chars"
        else:
            self.calls_left -= calls
            self.chars_left -= chars
            return True
        self.skipped.extend(names)
        return False


# Evidence whose absence stops a run rather than thinning it.
_BLOCKING_EVIDENCE = frozenset(
    {"financial_documents", "credit_relationship_documents"}
)


class Supervisor:
    """Local  supervisor without API, cache, or database dependencies."""

    MIXED_UNIT_WARNING = (
        "CURRENCY UNIT NOTICE: the blocks below use DIFFERENT UNITS, and each "
        "block states its own unit at the top. Read the unit of the block you "
        "are quoting from. NEVER put a figure from one block beside a figure "
        "from another without converting first — the same line item can appear "
        "in two blocks under two units."
    )

    _document_block_header = staticmethod(prompt_blocks._document_block_header)
    _build_source_list_block = staticmethod(prompt_blocks._build_source_list_block)
    _build_financial_metrics_block = staticmethod(prompt_blocks._build_financial_metrics_block)
    _build_credit_need_block = staticmethod(prompt_blocks._build_credit_need_block)
    _build_debt_chart_block = staticmethod(prompt_blocks._build_debt_chart_block)

    DEBT_CHART_ANCHORS = ("dien bien du no", "quan he tin dung")
    RELEVANCE_SCORE = {"R": 2.0, "O": 1.0}

    PRIMARY_DOC_BUDGET_WEIGHT = 3
    SECONDARY_DOC_BUDGET_WEIGHT = 1

    DOC_SECTION_HEADER = "Uploaded document extracted content:\n\n"
    DOC_BLOCK_SEPARATOR = "\n\n---\n\n"
    # The boundary between what the pipeline says and what the customer's own
    # files say. "---" alone was the old boundary and is not one: it appears 86
    # times inside a single sample statement. The fence token is stripped from
    # content before the content is wrapped, so a document cannot close its own
    # fence and continue as if it were the pipeline talking. Paired with
    # SOURCE DATA RULE in specialist.py, which tells the model what it means.
    DOC_FENCE_OPEN = "<<<SOURCE_DOCUMENT {index}>>>"
    DOC_FENCE_CLOSE = "<<</SOURCE_DOCUMENT {index}>>>"
    _FENCE_PATTERN = re.compile(r"<<</?SOURCE_DOCUMENT[^>]*>>>")

    @classmethod
    def _fence(cls, index: int, content: str) -> str:
        """Wrap one document's text so its boundary cannot be forged."""

        return "\n".join(
            [
                cls.DOC_FENCE_OPEN.format(index=index),
                cls._FENCE_PATTERN.sub("[fence marker removed]", content),
                cls.DOC_FENCE_CLOSE.format(index=index),
            ]
        )

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(self, config: Config):
        self.config = config
        self.document_classifier_chain = (
            self._build_document_classifier_chain()
            if config.document_llm
            else None
        )
        self._grouped_classifier_chains: dict[str, Any] = {}
        for pass_ in EXTRACTION_PASSES:
            llm = getattr(config, pass_.llm_attr)
            setattr(self, pass_.chain_attr,
                    pass_.build_chain(llm) if llm else None)
        self.workflow_graph = self._build_workflow_graph()

    def _build_workflow_graph(self):
        """Build the deterministic LangGraph underwriting workflow."""

        workflow = StateGraph(UnderwritingGraphState)
        workflow.add_node("discover_documents", self._graph_discover_documents)
        workflow.add_node("classify_documents", self._graph_classify_documents)
        workflow.add_node("evidence_gap_check", self._graph_evidence_gap_check)
        workflow.add_node("extract_documents", self._graph_extract_documents)
        workflow.add_node("fetch_reference_data", self._graph_fetch_reference_data)
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
            "single_credit_proposal",
            self._graph_run_credit_proposal,
        )

        workflow.set_entry_point("discover_documents")
        workflow.add_edge("discover_documents", "classify_documents")
        workflow.add_edge("classify_documents", "evidence_gap_check")
        workflow.add_conditional_edges(
            "evidence_gap_check",
            self._graph_stop_if_answered,
            {"blocked": END, "continue": "extract_documents"},
        )
        # After extraction, not before: the customer key is read out of the
        # extraction results, so there is nothing to query with until they exist.
        # Conditional because a required pass that failed ends the run here —
        # see ExtractionPass.required.
        workflow.add_conditional_edges(
            "extract_documents",
            self._graph_stop_if_answered,
            {"blocked": END, "continue": "fetch_reference_data"},
        )
        workflow.add_conditional_edges(
            "fetch_reference_data",
            self._graph_select_workflow_branch,
            {
                "single_business_activity": "single_business_activity",
                "single_credit_relationship": "single_credit_relationship",
                "single_financial_analysis": "single_financial_analysis",
                "single_credit_proposal": "single_credit_proposal",
            },
        )
        for node in [
            "single_business_activity",
            "single_credit_relationship",
            "single_financial_analysis",
            "single_credit_proposal",
        ]:
            workflow.add_edge(node, END)
        
        return workflow.compile()

    # ── Graph nodes, in the order the workflow runs them ──────────────────

    def _graph_discover_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Discover supported files from the provided input paths."""

        input_paths = state.get("input_paths") or []
        files = discover_documents(input_paths, self.config.max_files)
        steps = state.get("steps", [])
        steps.append(f"Discovered {len(files)} supported file(s)")

        # The screen puts every file in one of the six upload boxes, so a file
        # under none of them did not come from the screen — it is an integration
        # fault, and classifying it would launder that fault into a report.
        #
        # Dropped here rather than in the classify loop because OCR happens
        # there: a file discarded before extract_document_text costs nothing,
        # one discarded after has already been paid for. And not inside
        # discover_documents, which finds files on disk and holds no notion of
        # a box — every other caller still sees everything.
        in_box: list[str] = []
        outside: list[str] = []
        for path in files:
            if group_from_path(path):
                in_box.append(path)
            else:
                outside.append(os.path.basename(path))
        if outside:
            steps.append(
                f"WARNING: skipped {len(outside)} file(s) under no upload "
                f"box: {', '.join(sorted(outside)[:10])}"
                f"{' …' if len(outside) > 10 else ''}. Every file must sit in "
                f"one of the {len(box_ids())} box folders: "
                f"{', '.join(sorted(box_ids()))}."
            )

        # No out-of-scope branch: the screen picked an agent, so there is always
        # something being asked for. A dossier with no readable file goes on to
        # the evidence gap check, which names the documents it is missing —
        # more use than the constant this used to answer with.
        return {
            **state,
            "files": in_box,
            "skipped_outside_box": outside,
            "steps": steps,
        }

    def _graph_classify_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Extract and classify uploaded documents."""

        steps = state.get("steps", [])
        # Picks which column of the matrix decides every document's R/O
        # relevance, so it is logged even though it is now simply given.
        loan_program = resolve_loan_program(state.get("loan_program", ""))
        steps.append(f"Loan program: {loan_program}")
        documents = self._prepare_documents(
            state.get("files") or [],
            steps,
            loan_program,
        )
        # Structured extraction deliberately does NOT happen here. It costs one
        # LLM call per matching document, and which of those results anybody
        # reads depends on the route, which is not decided until two nodes from
        # now — so it runs in _graph_extract_documents instead.
        document_summary = self._format_document_summary(documents)
        return {
            **state,
            "loan_program": loan_program,
            "documents": documents,
            "document_summary": document_summary,
        }

    def _graph_evidence_gap_check(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Analyze required evidence before running expensive agents."""

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
            response = self._missing_evidence_response(
                gap_analysis,
                # Only when nothing survived. With some documents through, the
                # gap is about what the dossier actually lacks, and blaming the
                # boxes would send the officer looking in the wrong place.
                state.get("skipped_outside_box") or []
                if not documents
                else [],
            )
            output_state = self._build_state(
                response,
                "EVIDENCE_GAP_CHECK",
                decision,
                documents,
                {},
                execution_plan,
                gap_analysis,
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
    def _graph_stop_if_answered(
        state: UnderwritingGraphState,
    ) -> str:
        """Whether a node has already produced the run's answer.

        Two nodes can end the run early — the evidence gap check, and the
        extraction step when a required pass failed. Both say so the same way,
        by putting output_state into the state, so both edges ask this.
        """

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
        budget = _ExtractionBudget(
            self.config.max_extraction_calls,
            self.config.max_extraction_input_chars,
        )

        for pass_ in EXTRACTION_PASSES:
            if pass_.label not in needed:
                continue
            self._run_structured_extraction(
                documents,
                steps,
                flag_attr=pass_.flag_attr,
                chain=getattr(self, pass_.chain_attr),
                extract=pass_.extract,
                result_attr=pass_.result_attr,
                error_attr=pass_.error_attr,
                label=pass_.label,
                missing_llm_message=f"No {pass_.llm_attr} configured.",
                batch=pass_.batch,
                budget=budget,
            )

        if budget.skipped:
            steps.append(
                f"Extraction budget {budget.limit_hit} reached — "
                f"{len(budget.skipped)} document(s) not extracted: "
                f"{', '.join(sorted(set(budget.skipped)))}. Raise the limit in "
                f"Config or submit fewer files."
            )

        skipped = self._describe_skipped_passes(documents, needed)
        if skipped:
            # Say it out loud: a missing extraction block otherwise looks
            # identical to one that failed, and the two need different fixes.
            steps.append(
                f"Saved LLM calls — no agent on route {route} reads: {skipped}"
            )
        # A required pass that ran and produced nothing stops the run. The
        # alternative — the old behaviour — is to send the raw OCR the pass
        # exists to replace, and let the agent write a credit opinion off it.
        failed = [
            (pass_, doc, why)
            for pass_ in EXTRACTION_PASSES
            if pass_.required and pass_.label in needed
            for doc, why in pass_.failures(documents)
        ]
        if failed:
            steps.append(
                f"Stopped after extraction: {len(failed)} required "
                f"extraction(s) failed and raw OCR was not used instead"
            )
            return {
                **state,
                "documents": documents,
                # Same keys the normal return sets. A branch that drops one
                # leaves the state a different shape depending on which way it
                # went, and the next reader of document_summary gets a KeyError
                # instead of a report — which is exactly what svbase caught.
                "document_summary": self._format_document_summary(documents),
                "steps": steps,
                "output_state": self._build_state(
                    self._failed_extraction_response(failed),
                    "EXTRACTION_FAILED",
                    state.get("decision") or {},
                    documents,
                    {},
                    state.get("execution_plan") or {},
                    state.get("gap_analysis") or {},
                    steps,
                    loan_program=state.get("loan_program", ""),
                ),
            }

        return {
            **state,
            "documents": documents,
            # Rebuilt because the tags in it report extraction state, which only
            # exists now — the copy the routing node read was written before any
            # of this ran.
            "document_summary": self._format_document_summary(documents),
            "steps": steps,
        }

    def _graph_fetch_reference_data(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Query the reference data this route's agents read.

        Nothing here is optional-but-silent. A missing executor, a customer key
        that could not be read, a key the wrong shape, a tool the folder's own
        documents already cover — each ends in a step-log line saying which, so a
        report thin on internal data can be told from one that was never asked.
        """

        documents = state.get("documents") or []
        steps = state.get("steps", [])
        route = (state.get("decision") or {}).get("route", DEFAULT_ROUTE)
        specialist = SPECIALIST_BY_AGENT.get(route)
        wanted = list(specialist.query_tools) if specialist else []
        if not wanted:
            return {"reference_data": {}, "customer_key": {}, "steps": steps}

        key = resolve_customer_key(documents)
        for warning in key.warnings:
            steps.append(f"Customer key: {warning}")

        executor = self.config.query_executor
        if executor is None or not key.usable:
            reason = ("No query_executor configured." if executor is None
                      else "Could not determine the tax code.")
            steps.append(
                f"Skipped reference data for {len(wanted)} tool(s): {reason}"
            )
            return {"reference_data": {}, "customer_key": key._asdict(),
                    "steps": steps}

        steps.append(
            f"Customer key: tax code {key.tax_code} read from {key.source_file} "
            f"({key.source_field})"
        )
        present = {doc.document_type for doc in documents}
        fetched: dict[str, Any] = {}
        for query_tool in wanted:
            extras = getattr(query_tool, "extras", None) or {}
            covered = sorted(present & set(extras.get("superseded_by", ())))
            if covered:
                # The folder already holds the paper version, and that one wins.
                steps.append(
                    f"Skipped {query_tool.name} query: the dossier already holds "
                    f"{', '.join(covered)}"
                )
                continue
            try:
                # A LangChain tool answers with a string; json.loads is the
                # contract, not a workaround for it.
                fetched[query_tool.name] = json.loads(
                    query_tool.invoke(
                        {"tax_code": key.tax_code, "executor": executor}
                    )
                )
                steps.append(f"{query_tool.name} query: ok for tax code {key.tax_code}")
            except Exception as exc:
                # A database that is down must not take the report with it: the
                # documents are still evidence, and the block simply stays empty.
                steps.append(
                    f"{query_tool.name} query failed: {type(exc).__name__}: "
                    f"{str(exc)[:200]}"
                )
        # The key travels with the rows so the block that renders them can say
        # whose they are; "_key" is not a tool name, and the block builder
        # only ever looks up labels.
        fetched["_key"] = key._asdict()
        # Recomputed rather than guessed ahead: the gap check runs before this
        # node, so at that point nobody knows whether a query will answer. Now
        # it is known, and the summary the agent reads should say so.
        covered = {
            type_id
            for query_tool in wanted if query_tool.name in fetched
            for type_id in (getattr(query_tool, "extras", None) or {}).get(
                "superseded_by", ()
            )
        }
        state_update: dict[str, Any] = {
            "reference_data": fetched,
            "customer_key": key._asdict(),
            "steps": steps,
        }
        if covered:
            state_update["gap_analysis"] = self._analyze_evidence_gaps(
                documents, route, covered
            )
            steps.append(
                "Evidence gap re-checked: "
                f"{', '.join(sorted(covered))} came from a system query"
            )
        return state_update

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

    def _graph_run_credit_proposal(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the standalone Credit Proposal branch."""

        return {
            **state,
            "output_state": self._run_single_agent(
                "CREDIT_PROPOSAL_AGENT",
                state.get("decision") or {},
                state.get("documents") or [],
                state.get("execution_plan") or {},
                state.get("gap_analysis") or {},
                state.get("steps", []),
                state.get("reference_data") or {},
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
            state.get("decision") or {},
            state.get("documents") or [],
            state.get("execution_plan") or {},
            state.get("gap_analysis") or {},
            state.get("steps", []),
            state.get("reference_data") or {},
        )

    # ── Entry point ───────────────────────────────────────────────────────

    def process(
        self,
        input_paths: list[str] | None = None,
        agent: str = DEFAULT_ROUTE,
        loan_program: str = "",
    ) -> dict[str, Any]:
        """Run one local notebook request through the agent workflow.

        ``agent`` is what the upload screen picked. It used to be guessed from
        the request text by a keyword pass, then an LLM, then a fallback — three
        tiers of inference for something the user was in a position to state.
        """

        initial_state: UnderwritingGraphState = {
            "input_paths": input_paths or [],
            "loan_program": loan_program,
            "decision": self._decision_for(agent),
            "workflow_mode": self._workflow_mode_for_route(agent),
            "steps": ["Received request"],
        }
        result = self.workflow_graph.invoke(initial_state)
        output_state = result.get("output_state")
        if output_state:
            return output_state
        return self._build_state(
            "Workflow graph finished without an output state.",
            "WORKFLOW_GRAPH",
            steps=result.get("steps", []),
        )

    # ── Reading and classifying the uploaded documents ────────────────────

    def _prepare_documents(
        self,
        files: list[str],
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
            declared_group = group_from_path(file_path)
            classification = self._classify_document(
                filename,
                content,
                declared_group,
            )
            document_type = classification.get("document_type", "")
            # A declared box is trusted, which means a file dropped in the wrong
            # one can never be corrected — the types that would have won are not
            # even scored. So say so. Scored against all 22 types here, purely to
            # notice the disagreement; the label is left alone, because only the
            # person who uploaded it knows which of the two they meant.
            #
            # Content, not just the filename. The filename-only version was
            # silent on exactly the files that need it most: with a real balance
            # sheet's text dropped into ho_so_phap_ly, "BCTC_VVS_2024.pdf" warned
            # but "scan001.pdf", "Document1.pdf" and "IMG_2024.pdf" said nothing
            # at all — and a scanner's default name is the normal case, not the
            # edge one. The body scores bao_cao_tai_chinh at 6 in each of them.
            # Measured at 27ms for an 89k-character document, against tens of
            # seconds of OCR already spent on the same text.
            if declared_group:
                owner_type = self._cross_box_owner(
                    filename,
                    content,
                    declared_group,
                )
                if owner_type:
                    steps.append(
                        f"Possible wrong upload box: {filename} was put in "
                        f"'{declared_group}' but its content matches "
                        f"'{owner_type.short_label}' from "
                        f"'{owner_type.group_id}'."
                    )
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
            is_financial_statement = is_financial_statement_type(document_type)
            is_proposal = is_proposal_type(document_type)
            is_cic_s10a = is_cic_s10a_type(document_type)
            is_cic_r21 = is_cic_r21_type(document_type)
            is_sitevisit = is_sitevisit_type(document_type)
            is_ledger = is_ledger_type(document_type)
            steps.append(
                f"Classified document: {filename} -> "
                + (f"{document_type} " if document_type else "(no type matched) ")
                + f"-> {', '.join(relevant_agents) or 'GENERAL_CONTEXT'}"
                + (" [BCTC]" if is_financial_statement else "")
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
                    source_description=str(
                        classification.get("description", "")
                    ).strip(),
                    declared_group=declared_group,
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
                    is_financial_statement=is_financial_statement,
                    is_proposal=is_proposal,
                    is_cic_s10a=is_cic_s10a,
                    is_cic_r21=is_cic_r21,
                    is_sitevisit=is_sitevisit,
                    is_ledger=is_ledger,
                )
            )
        return documents

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

    @staticmethod
    def _cross_box_owner(filename: str, content: str, declared_group: str):
        """The type the file's own content points at, when that is another box.

        None when the content agrees with the declared box, or when it is too
        weak to disagree with anything.

        Runs the ordinary rule classifier with no box restriction rather than
        re-deriving a winner from raw scores, so the two answers are produced by
        one tie-break. A second ranking here would eventually disagree with the
        real one and report a conflict that does not exist.
        """

        unrestricted = rule_classify_document(filename, content, "")
        owner_type = get_type(unrestricted.get("document_type", ""))
        if not owner_type or owner_type.group_id == declared_group:
            return None
        # The floor rule_classify_document already uses to distrust its own
        # answer. Below one filename keyword's worth of evidence a
        # "disagreement" is noise, and a warning nobody can act on is worse
        # than saying nothing.
        best = max(unrestricted.get("scores", {}).values(), default=0)
        return owner_type if best >= FILENAME_KEYWORD_WEIGHT else None

    def _classify_document(
        self,
        filename: str,
        content: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        rule = rule_classify_document(filename, content, group_id)
        threshold = (
            self.config.document_classifier_grouped_confidence_threshold
            if group_id
            else self.config.document_classifier_rule_confidence_threshold
        )
        if rule["document_type"] and (
            rule["confidence"] >= threshold
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

        chain = self._classifier_chain_for(group_id)
        if chain:
            try:
                result = chain.invoke(
                    {
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

    def _classifier_chain_for(self, group_id: str):
        """The classifier chain whose catalogue matches this document's box."""

        # Mirrors the guard the ungrouped chain gets at __init__. Without it a
        # run with no document LLM configured — every check in testing/ — built
        # a chain around None and raised instead of falling back to the rule.
        if not group_id or not self.config.document_llm:
            return self.document_classifier_chain
        if group_id not in self._grouped_classifier_chains:
            self._grouped_classifier_chains[group_id] = (
                self._build_document_classifier_chain(group_id)
            )
        return self._grouped_classifier_chains[group_id]

    def _build_document_classifier_chain(self, group_id: str = ""):
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", build_document_classification_prompt(group_id)),
                (
                    "human",
                    """
                    Filename: {filename}

                    Extracted document text sample:
                    {content_sample}

                    Classify this document.
                    """,
                ),
            ]
        )
        return prompt | self.config.document_llm | JsonOutputParser()

    @classmethod
    def _extraction_tags(cls, doc: ClassifiedDocument) -> str:
        """Mark which structured extractions ran on a document, and which failed.

        A failed extraction silently drops the agent back to raw OCR — the report
        still comes out, just without the structured figures — so the failure has
        to be visible in the summary the decision LLM reads.
        """

        tags = []
        for pass_ in EXTRACTION_PASSES:
            if not getattr(doc, pass_.flag_attr):
                continue
            # Keyed on the error, not on a missing result: this summary is built
            # twice, and the first time — for the routing node — no pass has run
            # yet. Testing the result alone would report every document as a
            # failed extraction at that point, which is the opposite of true.
            if getattr(doc, pass_.error_attr):
                tags.append(f" [{pass_.tag}, trích xuất lỗi]")
            else:
                tags.append(f" [{pass_.tag}]")
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

    # ── Structured extraction: which passes run, and running them ─────────

    @classmethod
    def _passes_needed_for_route(cls, route: str) -> set[str]:
        """Which extraction passes the agents on this route actually consume."""

        # One agent per route since the routing tier went: the map this used to
        # read was {agent: (agent,)}, an identity table whose only real content
        # was the list of valid ids — which SPECIALIST_BY_AGENT already holds.
        agents = {route} if route in SPECIALIST_BY_AGENT else set()
        return {
            pass_.label
            for pass_ in EXTRACTION_PASSES
            if agents & pass_.consumers()
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
        for pass_ in EXTRACTION_PASSES:
            label = pass_.label
            if label in needed:
                continue
            count = sum(1 for doc in documents if getattr(doc, pass_.flag_attr))
            if count:
                parts.append(f"{label} ({count} document(s), {count} LLM call(s))")
        return ", ".join(parts)

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
        batch: bool = False,
        budget: "_ExtractionBudget | None" = None,
    ) -> None:
        """Run one structured-extraction pass over the documents it applies to.

        Several files can need the same pass in one run (current + prior year
        statements, say); they go one at a time. Running them concurrently only
        paid off behind a rate limiter that kept the burst under the provider's
        quota, and that limiter is gone. Results are written onto each document
        in place and nothing raises, so a failed or unconfigured extraction
        always leaves _build_user_input a clean signal to fall back to the raw
        OCR text. That covers extraction *failing*; it does not cover a pass
        declared with the wrong signature, which is a programming error and is
        meant to be loud — the guard under EXTRACTION_PASSES catches that one
        at import instead.

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

        # Charged before the call, not after: the point is to not make it.
        if budget is not None:
            names = [d.filename for d in targets]
            chars = sum(len(d.content) for d in targets)
            # A batch pass is one call for all of its documents; the rest are
            # one call each.
            if not budget.take(1 if batch else len(targets), chars, names):
                for doc in targets:
                    setattr(doc, error_attr, f"Skipped: {budget.limit_hit} reached.")
                steps.append(
                    f"Skipped {label} extraction for {len(targets)} document(s): "
                    f"{budget.limit_hit} reached."
                )
                return

        # A batch pass sees every matching document at once and returns one
        # answer per document, in order. Its record can therefore span files —
        # a ledger account split across two of them merges into one entry.
        pairs = (
            zip(targets, extract(
                chain, [(d.filename, d.content, d.path) for d in targets]
            ))
            if batch
            # The path goes with the text because one pass needs to know what
            # kind of file it is holding: an e-tax XML is read from its codes
            # rather than sent to the model. The other four ignore it.
            else ((d, extract(chain, d.filename, d.content, d.path)) for d in targets)
        )
        for doc, (result, error) in pairs:
            setattr(doc, result_attr, result)
            setattr(doc, error_attr, error)
            if flag_attr == "is_financial_statement" and result is not None:
                doc.financial_statement_extraction_source = (
                    "xml" if doc.path.lower().endswith(".xml") and not error else "llm"
                )
            steps.append(
                f"{label} extraction: {doc.filename} -> "
                + ("ok" if result is not None else f"failed: {error}")
                + (
                    " (read straight from the e-tax XML)"
                    if getattr(doc, "financial_statement_extraction_source", "") == "xml"
                    else ""
                )
            )

    # ── Routing: the screen's choice, checked ─────────────────────────────

    @classmethod
    def _decision_for(cls, agent: str) -> dict[str, Any]:
        """The decision record for an agent the screen chose.

        An unknown id raises rather than falling back to the default: a typo
        would otherwise produce a business-activity report for someone who asked
        for a credit proposal, with nothing in the output to point at.
        """

        if agent not in SPECIALIST_BY_AGENT:
            raise ValueError(
                f"unknown agent {agent!r}; expected one of "
                f"{sorted(SPECIALIST_BY_AGENT)}"
            )
        return {
            "route": agent,
            "workflow_mode": cls._workflow_mode_for_route(agent),
            "reasoning": "Chosen on the upload screen.",
            "confidence": 1.0,
        }

    @staticmethod
    def _workflow_mode_for_route(route: str) -> WorkflowMode:
        """Map a route to the deterministic workflow branch."""

        route_modes = {
            "BUSINESS_ACTIVITY_AGENT": "single_business_activity",
            "CREDIT_RELATIONSHIP_AGENT": "single_credit_relationship",
            "FINANCIAL_ANALYSIS_AGENT": "single_financial_analysis",
            "CREDIT_PROPOSAL_AGENT": "single_credit_proposal",
        }
        return route_modes.get(route, DEFAULT_WORKFLOW_MODE)

    # ── Evidence gaps: what is missing before an agent is worth running ───

    def _analyze_evidence_gaps(
        self,
        documents: list[ClassifiedDocument],
        route: str,
        covered_types: set[str] | None = None,
    ) -> dict[str, Any]:
        inventory = {
            "financial_documents": [],
            "business_activity_documents": [],
            "credit_relationship_documents": [],
            "credit_proposal_documents": [],
            "general_context": [],
        }
        agent_to_bucket = {
            "FINANCIAL_ANALYSIS_AGENT": "financial_documents",
            "BUSINESS_ACTIVITY_AGENT": "business_activity_documents",
            "CREDIT_RELATIONSHIP_AGENT": "credit_relationship_documents",
            "CREDIT_PROPOSAL_AGENT": "credit_proposal_documents",
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

        # Credit relationship joined this list when the matrix stopped routing
        # financial statements to it. Before that it always had *something* —
        # a BCTC it could not read properly but could still fill a page from —
        # so an empty dossier never surfaced. Without an entry here the route
        # answers with a blank report instead of naming what is missing, and a
        # blank report is the one outcome this gate exists to prevent.
        required = {
            "FINANCIAL_ANALYSIS_AGENT": ["financial_documents"],
            "BUSINESS_ACTIVITY_AGENT": ["business_activity_documents"],
            "CREDIT_RELATIONSHIP_AGENT": ["credit_relationship_documents"],
        }.get(route, [])

        missing = []
        for evidence_type in required:
            if inventory.get(evidence_type):
                continue
            missing.append(
                {
                    "type": evidence_type,
                    # A credit-relationship report with neither a CIC file nor
                    # a tool result has no subject at all, so it blocks like
                    # financial analysis rather than degrading like business
                    # activity.
                    "severity": (
                        "high"
                        if evidence_type in _BLOCKING_EVIDENCE
                        else "medium"
                    ),
                    "can_continue_without_it": (
                        evidence_type not in _BLOCKING_EVIDENCE
                    ),
                    "reason": self._gap_reason(evidence_type),
                }
            )

        # Naming the absent documents, which the agent-bucket reasoning above
        # cannot: that only knows a bucket holds no evidence, never what was
        # supposed to be in it.
        for group_id, group_label, absent in self._missing_required_types(
            documents, route, covered_types
        ):
            missing.append(
                {
                    "type": f"upload_box:{group_id}",
                    "severity": "medium",
                    "can_continue_without_it": True,
                    "reason": (
                        f"Hộp \"{group_label}\" thiếu tài liệu bắt buộc: "
                        f"{', '.join(absent)}."
                    ),
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

    @staticmethod
    def _missing_required_types(
        documents: list[ClassifiedDocument],
        route: str,
        covered_types: set[str] | None = None,
    ) -> list[tuple[str, str, list[str]]]:
        """Per upload box: (id, label, short labels of the mandatory types absent).

        Checked per type, not per box. A box holding a single file used to count
        as filled, so a dossier with a balance sheet but no VAT return and no
        bank statement reported nothing at all — the common shortfall was the
        invisible one.

        Narrowed to what this route consumes, the same scoping the bucket check
        above uses. This summary is read by one specialist, and naming documents
        it would not open is noise sitting in its prompt. Two collateral types
        reach no specialist at all, so nothing here ever names them — a checklist
        for the officer would have to come from the matrix, not from here.

        The matrix also carries a ``tai_cap`` column for re-issued facilities,
        unreachable here: nothing in the system says whether a case is new or a
        re-issue, so every case is read against ``cap_moi``.
        """

        # A type queried from the bank's systems is not missing evidence, it is
        # evidence that arrived by another door. Without this every DB-mode run
        # carried "Hộp Hồ sơ nội bộ thiếu Thông tin CIC khách hàng vay" into the
        # agent's prompt while the CIC data sat in the prompt beside it.
        present = {doc.document_type for doc in documents if doc.document_type}
        present |= covered_types or set()
        boxes: dict[str, tuple[str, list[str]]] = {}
        for doc_type in load_matrix().types.values():
            requirement = doc_type.requirement.get("cap_moi", "")
            if "bat buoc" not in normalize_text(requirement):
                continue
            if doc_type.id in present or route not in doc_type.routing_signature:
                continue
            _, absent = boxes.setdefault(
                doc_type.group_id, (doc_type.group_label, [])
            )
            absent.append(doc_type.short_label)
        return [
            (group_id, label, absent)
            for group_id, (label, absent) in sorted(boxes.items())
        ]

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
        }
        return reasons.get(evidence_type, "Relevant evidence was not found.")

    @staticmethod
    def _failed_extraction_response(
        failed: list[tuple[Any, ClassifiedDocument, str]],
    ) -> str:
        """The answer when a required extraction produced nothing.

        Names every file and the reason stored against it, because the three
        causes need three different fixes: a timeout is worth retrying, an
        unconfigured model is a .env edit, and a budget cut means the dossier
        was larger than the run allows.
        """

        lines = [
            f"- **{doc.filename}** ({pass_.label}): {why}"
            for pass_, doc, why in failed
        ]
        return (
            "## Không thể lập báo cáo\n\n"
            "Việc trích xuất dữ liệu có cấu trúc thất bại với các tài liệu "
            "dưới đây. Hệ thống **không dùng văn bản OCR thô thay thế** — số "
            "liệu đọc từ OCR chưa qua trích xuất không đủ tin cậy để đưa vào "
            "báo cáo thẩm định.\n\n"
            "### Tài liệu trích xuất thất bại\n"
            + "\n".join(lines)
            + "\n\nXử lý xong nguyên nhân ở trên rồi chạy lại."
        )

    @staticmethod
    def _missing_evidence_response(
        gap_analysis: dict[str, Any],
        skipped_outside_box: list[str] | None = None,
    ) -> str:
        """The answer when required evidence is missing.

        Always Vietnamese: the screen has no free-text box, so there is no
        request whose language could be matched, and every report this system
        writes is Vietnamese anyway.

        ``skipped_outside_box`` overrides the wording entirely, because the two
        situations call for opposite actions. The evidence list below says
        "send us a financial statement" — useless advice to somebody who did
        send one, into a folder the screen never labelled. Naming the shortfall
        there would have the officer hunting for a document already uploaded.
        """

        skipped = skipped_outside_box or []
        if skipped:
            shown = ", ".join(sorted(skipped)[:10])
            return (
                "## Không thể lập báo cáo — hồ sơ chưa được gắn hộp upload\n\n"
                f"Đã tìm thấy {len(skipped)} file nhưng không file nào nằm "
                "trong hộp upload nào, nên không file nào được xử lý. Đây là "
                "lỗi ở bước nộp hồ sơ, **không phải do thiếu tài liệu**.\n\n"
                f"### File bị bỏ qua\n- {shown}"
                f"{' …' if len(skipped) > 10 else ''}\n\n"
                "### Cách xử lý\n"
                "Nộp lại qua màn hình upload để mỗi file được gắn đúng hộp. "
                f"Sáu hộp hợp lệ: {', '.join(sorted(box_ids()))}."
            )

        missing = gap_analysis.get("missing_evidence", [])
        lines = [
            f"- **{item.get('type', 'evidence')}**: "
            f"{item.get('reason', 'Missing evidence.')}"
            for item in missing
        ]
        return (
            "## Cần bổ sung thêm tài liệu\n\n"
            "Mình chưa thể tiếp tục phân tích vì đang thiếu thông tin "
            "bắt buộc.\n\n"
            "### Tài liệu cần bổ sung\n"
            + ("\n".join(lines) if lines else "- Chưa xác định.")
        )

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
        if route == "CREDIT_PROPOSAL_AGENT":
            agents = ["CREDIT_PROPOSAL_AGENT"]
            order = ["credit_proposal_calculation", "reflection"]
        else:
            # Every remaining route is a single specialist. Unknown ids land
            # here too and are answered with the default rather than a plan
            # naming an agent that no longer exists.
            agents = [route if route in SPECIALIST_BY_AGENT else DEFAULT_ROUTE]
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
            "can_answer_now": bool(gap_analysis.get("can_proceed", True)),
            "missing_information": gap_analysis.get("missing_evidence", []),
            "reasoning": decision.get("reasoning", ""),
        }

    # ── Running one specialist, and finishing its report ──────────────────

    def _run_single_agent(
        self,
        agent_name: str,
        decision: dict[str, Any],
        documents: list[ClassifiedDocument],
        execution_plan: dict[str, Any],
        gap_analysis: dict[str, Any],
        steps: list[str],
        reference_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        steps.append(f"Running {agent_name}")
        user_input = self._build_user_input(
            documents, agent_name, gap_analysis, reference_data or {}
        )
        agent = SPECIALIST_BY_AGENT.get(
            agent_name, SPECIALIST_BY_AGENT[DEFAULT_ROUTE]
        )(self.config.analysis_llm)
        # Namespaced even though a single-agent run has nobody to collide with:
        # the labels a reviewer reads in the .md should mean the same thing here
        # as in a full memo, and this is the one path all five modes share.
        response = namespace_footnotes(
            extract_text_from_agent_output(agent.analyze(user_input)),
            AGENT_LABEL_PREFIXES.get(agent_name, ""),
        )

        return self._finalize(
            response,
            agent_name,
            decision,
            documents,
            {agent_name: response},
            execution_plan,
            gap_analysis,
            steps,
        )

    def _finalize(
        self,
        response: str,
        agent_name: str,
        decision: dict[str, Any],
        documents: list[ClassifiedDocument],
        sub_agent_outputs: dict[str, str],
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
        # bare "-" instead of a <ul>. Fixing it here means every commentary
        # bullet is correct on the page even if a specialist forgot the blank
        # line the prompt asks for.
        response = ensure_blank_line_before_lists(response)
        # "8,00%" -> "8%", and a table cell holding only a zero -> "-". Asked of
        # every agent in NUMBER FORMAT RULE and applied here too, because a rule
        # in a prompt is a request.
        response = tidy_numbers(response)
        # Credit relationship's own analysis call transcribes VAT revenue into
        # a ```vat-doanh-thu block for _build_debt_chart_block to read further
        # down (see vat_revenue.py) — an internal data channel, never meant
        # for the reader. Parsed from sub_agent_outputs below, so stripping it
        # here only guards against the composer having copied it verbatim
        # into a merged multi-agent response.
        response = strip_vat_revenue_block(response)

        # Convert all VNĐ amounts to tỷ VNĐ for display.
        response = convert_amounts_in_text(response)
        # Prompt rules are not enforcement: report the citations that did not
        # resolve, rather than quietly dropping or inventing them.
        response += self._format_footnote_findings(footnote_audit)
        response += self._format_template_findings(response)
        # SOURCE DATA RULE tells the model to report a document that tries to
        # instruct it. This checks the documents directly, because a rule in a
        # prompt is a request — and the model least likely to report an injected
        # instruction is the one that followed it.
        response += self._format_injection_findings(documents)
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
        # The documents carry the programme they were actually classified
        # against, which is the one worth reporting.
        applied = next((doc.loan_program for doc in documents), "")
        return self._build_state(
            response,
            agent_name,
            decision,
            documents,
            sub_agent_outputs,
            execution_plan,
            gap_analysis,
            steps + ["Built final response"],
            self._build_document_selections(documents),
            self._build_financial_metrics_data(documents, agent_name),
            loan_program=applied,
            credit_need=self._build_credit_need_data(documents, agent_name),
        )

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
    def _format_injection_findings(documents: list[ClassifiedDocument]) -> str:
        """Flag source documents whose own text reads as an instruction."""

        findings = check_injection_markers(
            [(doc.filename, doc.content) for doc in documents]
        )
        if not findings:
            return ""
        lines = ["", "**Tài liệu nguồn có dấu hiệu chèn chỉ thị:**", ""]
        lines += [f"- {finding}" for finding in findings]
        return "\n".join(lines) + "\n"

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

    # ── Assembling a specialist's prompt ──────────────────────────────────

    @staticmethod
    def _reference_provenance(reference_data: dict[str, Any]) -> str:
        """Which customer these rows were fetched for, and on whose word.

        The agent is told the key came off a document rather than from the
        screen, because that is the difference between "the bank says" and "a
        scan said, and the bank answered about whoever that was".
        """

        key = reference_data.get("_key") or {}
        if not key.get("tax_code"):
            return ""
        return (
            f"Truy vấn theo MST {key['tax_code']}, đọc từ "
            f"{key.get('source_file') or 'hồ sơ'} ({key.get('source_field') or '?'})."
        )

    def _build_user_input(
        self,
        documents: list[ClassifiedDocument],
        target_agent: str,
        gap_analysis: dict[str, Any],
        reference_data: dict[str, Any] | None = None,
    ) -> str:
        budget = self.config.agent_input_char_budgets.get(target_agent, 12_000)
        base = (
            "Evidence sufficiency summary:\n"
            f"{gap_analysis.get('summary', '')}"
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
        # One block per pass this agent reads as JSON, empty for the rest. Keyed
        # by label so the pointer loop below can find a document's block without
        # matching on the flag a second time.
        json_blocks = {
            pass_.label: pass_.block_for(target_agent, usable)
            for pass_ in EXTRACTION_PASSES
        }
        financial_statement_block = json_blocks["BCTC"]
        proposal_block = json_blocks["Proposal"]
        cic_s10a_block = json_blocks["CIC S10A"]
        cic_r21_block = json_blocks["CIC R21"]
        sitevisit_block = json_blocks["Sitevisit"]
        ledger_block = json_blocks["Ledger"]
        # One block per tool the target agent declares, in the order it lists
        # them. An agent that declares none contributes nothing here.
        target_class = SPECIALIST_BY_AGENT.get(target_agent)
        reference_sections = [
            prompt_blocks._build_tool_result_block(
                query_tool,
                (reference_data or {}).get(query_tool.name) or {},
                self._reference_provenance(reference_data or {}),
            )
            for query_tool in (target_class.query_tools if target_class else ())
        ]
        reference_sections = [block for block in reference_sections if block]
        # State the periods explicitly: several BCTC files overlap by a year, so
        # the merged set (e.g. 2 files -> 3 years) does not match the sample
        # column count in the layout. Telling the agent removes the guesswork.
        if target_agent in _PASS_BY_LABEL["BCTC"].json_agents:
            periods = (
                self._build_financial_metrics_data(usable, target_agent) or {}
            ).get("years")
            if periods:
                base += (
                    "\n\nCác kỳ báo cáo có dữ liệu (dùng đúng số cột này cho mọi "
                    f"bảng theo năm, thứ tự tăng dần): {', '.join(periods)}"
                )
        credit_need_block = self._build_credit_need_block(documents, target_agent)
        # The units only clash once two of these blocks are present, so the
        # warning appears exactly then — a prompt carrying one block needs no
        # reconciling and should not gain a line telling it otherwise.
        money_blocks = sum(
            1
            for block in (
                metrics_block,
                financial_statement_block,
                proposal_block,
                cic_s10a_block,
                cic_r21_block,
                # Counted with the rest: next year's plan carries revenue and
                # COGS, so it can disagree about units with any block above it.
                sitevisit_block,
                # Đồng, straight from the spreadsheet's cells — the one block
                # here whose unit is certain rather than read off a page.
                ledger_block,
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
            - len(financial_statement_block)
            - len(self.DOC_SECTION_HEADER)
            - len(proposal_block)
            - len(cic_s10a_block)
            - len(cic_r21_block)
            - len(sitevisit_block)
            - len(ledger_block)
            - sum(len(block) for block in reference_sections)
            - len(credit_need_block)
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
        for index, doc in enumerate(usable, start=1):
            # A successfully-extracted BCTC doc is represented by its
            # structured JSON (see financial_statement_block above), not its raw OCR dump —
            # that's the whole point of the extraction pass. Gated on the same
            # table as the block itself, because sending one without the other
            # either bills the content twice or loses it entirely. Any other doc
            # (not BCTC, or extraction failed/unavailable) keeps raw content
            # so no evidence is ever silently dropped.
            pointed_at = next(
                (
                    pass_
                    for pass_ in EXTRACTION_PASSES
                    if target_agent in pass_.json_agents
                    and getattr(doc, pass_.flag_attr)
                    and getattr(doc, pass_.result_attr)
                ),
                None,
            )
            if pointed_at is not None:
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    f"— xem {pointed_at.heading} bên dưới."
                )
            else:
                content_section = "\n".join(
                    [
                        "Extracted document content:",
                        self._fence(
                            index, truncate_text(doc.content, _doc_budget(doc))
                        ),
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
        ledger_section = f"{ledger_block}\n\n" if ledger_block else ""
        reference_text = "".join(f"{block}\n\n" for block in reference_sections)
        credit_need_section = (
            f"{credit_need_block}\n\n" if credit_need_block else ""
        )
        return truncate_text(
            (
                f"{base}\n\n"
                # Near the top on purpose: the final truncate trims the tail, and
                # a source list that got cut is the exact failure this replaced.
                f"{source_list_block}\n\n"
                f"{unit_warning}"
                f"{metrics_block}\n\n"
                f"{financial_statement_block}\n\n"
                f"{proposal_section}"
                f"{cic_s10a_section}"
                f"{cic_r21_section}"
                f"{sitevisit_section}"
                f"{ledger_section}"
                f"{reference_text}"
                f"{credit_need_section}"
                f"{self.DOC_SECTION_HEADER}"
                f"{docs_text}"
            ),
            budget,
        )

    @staticmethod
    def _select_documents_for_agent(
        documents: list[ClassifiedDocument],
        target_agent: str,
    ) -> list[ClassifiedDocument]:
        required: list[ClassifiedDocument] = []
        optional: list[ClassifiedDocument] = []
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

    # ── The run log: the same figures as data rather than prose ───────────

    @staticmethod
    def _build_financial_metrics_data(
        documents: list[ClassifiedDocument],
        target_agent: str,
    ) -> dict[str, Any]:
        """Structured form of the deterministic ratio computation.

        The agent gets the markdown block; this returns the same numbers as data
        so a run can be audited afterwards (which line items were matched, what
        the ratios came out as, which sanity checks failed).

        Gated by the same tuple as that block, and read from it rather than
        copied. Without the gate this only stayed quiet on the other two routes
        by accident: the BCTC pass does not run there, so there was no input to
        compute from. That is luck, not design — the day BCTC joins another
        route's json_agents, this would start writing a metrics artefact for a
        route that never asked for one.
        """

        if target_agent not in prompt_blocks.METRICS_BLOCK_AGENTS:
            return {}
        usable = [
            doc for doc in documents if doc.extraction_status == "success"
        ]
        if not usable:
            return {}
        try:
            calculator = FinancialRatioCalculator()
            yearly_metrics = calculator.metrics_from_documents(usable)
            if not yearly_metrics:
                return {}
            years = sorted(yearly_metrics)
            return {
                "unit": "VNĐ",
                "years": years,
                "yearly_metrics": yearly_metrics,
                "ratios": calculator.compute_ratios(yearly_metrics),
                "validation_warnings": calculator.data_quality_warnings(
                    yearly_metrics
                ),
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    @classmethod
    def _build_credit_need_data(
        cls,
        documents: list[ClassifiedDocument],
        target_agent: str,
    ) -> dict[str, Any]:
        """The credit-need table as data, for the run log rather than a prompt.

        Recomputed rather than shared with _build_credit_need_block, matching
        how _build_financial_metrics_block and _build_financial_metrics_data
        already work: the two are called from different places in the graph, and
        the arithmetic is free next to the LLM calls around it.

        Gated by the same tuple as that twin, and read from it rather than
        copied: this table belongs to the credit proposal, and without the gate
        every route wrote a credit_need.json for a proposal it never ran. Two
        copies of the list is how the two gates would drift apart — which is the
        shape of the bug this fixes, one twin gated and the other not.
        """

        if target_agent not in prompt_blocks.CREDIT_NEED_BLOCK_AGENTS:
            return {}
        usable = [doc for doc in documents if doc.extraction_status == "success"]
        if not usable:
            return {}
        try:
            calculator = FinancialRatioCalculator()
            yearly_metrics = calculator.metrics_from_documents(usable)
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

    def _classifications_for_state(
        self,
        documents: list[ClassifiedDocument],
    ) -> list[dict[str, Any]]:
        """Documents as data, with the OCR text bounded.

        The one place a ClassifiedDocument becomes part of the returned payload,
        so bounding it here bounds every caller of _build_state at once. The
        pipeline itself is untouched: prompts and the injection check read
        doc.content directly and still see the whole document. Only what leaves
        the process is cut, because that is what gets exported and what hit a
        size limit on the far side — a 22-file dossier put the payload at
        ~1.6 MB, of which the OCR text was half.
        """

        limit = self.config.result_content_char_limit
        records = to_dict_list(documents)
        # The ledger pass is batch: it merges every workbook into ONE record and
        # hands the same object to each ledger document, so five files used to
        # serialise the same 59 KB five times. The prompt block already
        # de-duplicates by identity, so only the stored copy multiplied. Keyed
        # by id() because it is the same object by construction — comparing
        # 59 KB dicts for equality would be work done to learn what is already
        # known.
        seen: dict[int, str] = {}
        for doc, record in zip(documents, records):
            shared = getattr(doc, "ledger_extraction", None)
            if shared is None:
                continue
            owner = seen.setdefault(id(shared), doc.filename)
            if owner != doc.filename:
                record["ledger_extraction"] = {"same_as": owner}
        if limit <= 0:
            return records
        for record in records:
            content = record.get("content")
            if isinstance(content, str):
                record["content"] = truncate_text(content, limit)
        return records

    def _build_state(
        self,
        response: str,
        agent_name: str,
        decision: dict[str, Any] | None = None,
        documents: list[ClassifiedDocument] | None = None,
        sub_agent_outputs: dict[str, str] | None = None,
        execution_plan: dict[str, Any] | None = None,
        gap_analysis: dict[str, Any] | None = None,
        steps: list[str] | None = None,
        document_selections: dict[str, dict[str, list[str]]] | None = None,
        financial_metrics: dict[str, Any] | None = None,
        loan_program: str = "",
        credit_need: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "response": response,
            "agent_name": agent_name,
            "decision": decision or {},
            "loan_program": loan_program,
            "document_classifications": self._classifications_for_state(
                documents or []
            ),
            "document_selections": document_selections or {},
            "financial_metrics": financial_metrics or {},
            "credit_need": credit_need or {},
            "sub_agent_outputs": sub_agent_outputs or {},
            "execution_plan": execution_plan or {},
            "gap_analysis": gap_analysis or {},
            "steps": steps or [],
        }
