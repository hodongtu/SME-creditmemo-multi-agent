import inspect
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph

from src.agents.calculator.financial_ratio_calculator import FinancialRatioCalculator
from src.utils.common import normalize_text
from src.utils.reading.extractors import extract_document_text
from src.utils.report.formatting import convert_amounts_in_text, tidy_numbers
from src.utils.report.citations import (
    AGENT_LABEL_PREFIXES,
    FootnoteAudit,
    consolidate_footnotes,
    format_footnote_findings,
    namespace_footnotes,
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
    is_cic_r20_type,
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
from src.agents.extraction.cic_r20_extraction import (
    build_cic_r20_extraction_chain,
    extract_cic_r20_structured_data,
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
    """One structured-extraction pass, declared once."""

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
    batch: bool = False
    required: bool = False

    def failures(
        self, docs: list[ClassifiedDocument]
    ) -> list[tuple[ClassifiedDocument, str]]:
        """Documents this pass applies to that came back with no JSON."""

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
        label="CIC R20",
        tag="CIC R20",
        flag_attr="is_cic_r20",
        result_attr="cic_r20_extraction",
        error_attr="cic_r20_extraction_error",
        llm_attr="cic_r20_extraction_llm",
        build_chain=build_cic_r20_extraction_chain,
        extract=extract_cic_r20_structured_data,
        build_block=prompt_blocks._build_cic_r20_structured_block,
        heading=prompt_blocks.CIC_R20_BLOCK_HEADING,
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
        json_agents=(
            "FINANCIAL_ANALYSIS_AGENT",
            "BUSINESS_ACTIVITY_AGENT",
            "CREDIT_RELATIONSHIP_AGENT",
        ),
    ),
)


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
    """What one run may still spend on extraction."""

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
        loan_program = resolve_loan_program(state.get("loan_program", ""))
        steps.append(f"Loan program: {loan_program}")
        documents = self._prepare_documents(
            state.get("files") or [],
            steps,
            loan_program,
        )
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
        """Whether a node has already produced the run's answer."""

        return "blocked" if state.get("output_state") else "continue"

    def _graph_extract_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run only the structured extractions this route will actually read."""

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
            steps.append(
                f"Saved LLM calls — no agent on route {route} reads: {skipped}"
            )
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
            "document_summary": self._format_document_summary(documents),
            "steps": steps,
        }

    def _graph_fetch_reference_data(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Query the reference data this route's agents read."""

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
                steps.append(
                    f"Skipped {query_tool.name} query: the dossier already holds "
                    f"{', '.join(covered)}"
                )
                continue
            try:
                fetched[query_tool.name] = json.loads(
                    query_tool.invoke(
                        {"tax_code": key.tax_code, "executor": executor}
                    )
                )
                steps.append(f"{query_tool.name} query: ok for tax code {key.tax_code}")
            except Exception as exc:
                steps.append(
                    f"{query_tool.name} query failed: {type(exc).__name__}: "
                    f"{str(exc)[:200]}"
                )
        fetched["_key"] = key._asdict()
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
        """Run one local notebook request through the agent workflow. """

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
            is_cic_r20 = is_cic_r20_type(document_type)
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
                    is_cic_r20=is_cic_r20,
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
        """The type the file's own content points at, when that is another box."""

        unrestricted = rule_classify_document(filename, content, "")
        owner_type = get_type(unrestricted.get("document_type", ""))
        if not owner_type or owner_type.group_id == declared_group:
            return None
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
            or rule["routing_unambiguous"]
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
                if document_type and get_type(document_type) is None:
                    return rule
                result["document_type"] = document_type
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
        """Mark which structured extractions ran on a document, and which failed."""

        tags = []
        for pass_ in EXTRACTION_PASSES:
            if not getattr(doc, pass_.flag_attr):
                continue
            if getattr(doc, pass_.error_attr):
                tags.append(f" [{pass_.tag}, extraction failed]")
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
                or "GENERAL_CONTEXT (matched no known type)"
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
        """Name the skipped passes and how many documents each would have cost."""

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
        """Run one structured-extraction pass over the documents it applies to."""

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

        if budget is not None:
            names = [d.filename for d in targets]
            chars = sum(len(d.content) for d in targets)
            if not budget.take(1 if batch else len(targets), chars, names):
                for doc in targets:
                    setattr(doc, error_attr, f"Skipped: {budget.limit_hit} reached.")
                steps.append(
                    f"Skipped {label} extraction for {len(targets)} document(s): "
                    f"{budget.limit_hit} reached."
                )
                return

        pairs = (
            zip(targets, extract(
                chain, [(d.filename, d.content, d.path) for d in targets]
            ))
            if batch
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
        """The decision record for an agent the screen chose."""

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
            for agent in doc.relevant_agents:
                bucket = agent_to_bucket.get(agent, "general_context")
                inventory[bucket].append(doc.filename)

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
        """Per upload box: (id, label, short labels of the mandatory types absent)."""

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
        """The answer when a required extraction produced nothing. """

        lines = [
            f"- **{doc.filename}** ({pass_.label}): {why}"
            for pass_, doc, why in failed
        ]
        return (
            "## Không thể tạo báo cáo\n\n"
            "Việc trích xuất dữ liệu có cấu trúc không thành công với các tài liệu "
            "dưới đây. Hệ thống **không dùng văn bản OCR thô thay thế** — số "
            "liệu đọc từ OCR chưa qua trích xuất không đủ tin cậy để đưa vào "
            "báo cáo thẩm định.\n\n"
            "### Tài liệu trích xuất không thành công\n"
            + "\n".join(lines)
            + "\n\nHãy kiểm tra tài liệu đầu vào và thử lại."
        )

    @staticmethod
    def _missing_evidence_response(
        gap_analysis: dict[str, Any],
        skipped_outside_box: list[str] | None = None,
    ) -> str:
        """The answer when required evidence is missing."""

        skipped = skipped_outside_box or []
        if skipped:
            shown = ", ".join(sorted(skipped)[:10])
            return (
                "## Không thể tạo báo cáo — hồ sơ chưa được gắn loại tài liệu\n\n"
                f"Đã tìm thấy {len(skipped)} file nhưng không file nào nằm "
                "được gắn loại tài liệu, vì vậy không file nào được xử lý. Đây là "
                "lỗi ở bước upload hồ sơ, **không phải do thiếu tài liệu**.\n\n"
                f"### File bị bỏ qua\n- {shown}"
                f"{' …' if len(skipped) > 10 else ''}\n\n"
                "### Cách xử lý\n"
                "Upload lại qua màn hình upload để mỗi file được gắn đúng loại tài liệu. "
                f"Loại tài liệu hợp lệ: {', '.join(sorted(box_ids()))}."
            )

        missing = gap_analysis.get("missing_evidence", [])
        lines = [
            f"- **{item.get('type', 'evidence')}**: "
            f"{item.get('reason', 'Missing evidence.')}"
            for item in missing
        ]
        return (
            "## Cần bổ sung thêm tài liệu\n\n"
            "Tôi chưa thể tiếp tục phân tích vì đang thiếu thông tin "
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
        response, footnote_audit = consolidate_footnotes(response)
        response = tidy_numbers(response)
        response = strip_vat_revenue_block(response)
        # Convert all VNĐ amounts to tỷ VNĐ for display.
        response = convert_amounts_in_text(response)
        response += self._format_footnote_findings(footnote_audit)

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
        """Place the chart under the credit-relationship heading."""

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
    def _format_footnote_findings(audit: FootnoteAudit) -> str:
        """Append the citations that did not resolve against their definitions."""

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
        """Which customer these rows were fetched for, and on whose word."""

        key = reference_data.get("_key") or {}
        if not key.get("tax_code"):
            return ""
        return (
            f"Queried by tax code {key['tax_code']}, read from "
            f"{key.get('source_file') or 'the dossier'} "
            f"({key.get('source_field') or '?'})."
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
        usable = [doc for doc in selected if doc.extraction_status == "success"]

        def _is_primary(doc: ClassifiedDocument) -> bool:
            """Required evidence for this agent, per the matrix."""

            return doc.agent_relevance.get(target_agent) == "R"

        source_list_block = self._build_source_list_block(usable)
        metrics_block = self._build_financial_metrics_block(
            documents,
            target_agent,
        )

        json_blocks = {
            pass_.label: pass_.block_for(target_agent, usable)
            for pass_ in EXTRACTION_PASSES
        }
        financial_statement_block = json_blocks["BCTC"]
        proposal_block = json_blocks["Proposal"]
        cic_s10a_block = json_blocks["CIC S10A"]
        cic_r20_block = json_blocks["CIC R20"]
        sitevisit_block = json_blocks["Sitevisit"]
        ledger_block = json_blocks["Ledger"]
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

        if target_agent in _PASS_BY_LABEL["BCTC"].json_agents:
            periods = (
                self._build_financial_metrics_data(usable, target_agent) or {}
            ).get("years")
            if periods:
                base += (
                    "\n\nReporting periods with data — use exactly this many "
                    "year columns in every yearly table, oldest first: "
                    f"{', '.join(periods)}"
                )
        credit_need_block = self._build_credit_need_block(documents, target_agent)

        money_blocks = sum(
            1
            for block in (
                metrics_block,
                financial_statement_block,
                proposal_block,
                cic_s10a_block,
                cic_r20_block,
                sitevisit_block,
                ledger_block,
                credit_need_block,
            )
            if block
        )
        unit_warning = (
            f"{self.MIXED_UNIT_WARNING}\n\n" if money_blocks > 1 else ""
        )

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
            - len(cic_r20_block)
            - len(sitevisit_block)
            - len(ledger_block)
            - sum(len(block) for block in reference_sections)
            - len(credit_need_block)
            - len(unit_warning)
            - block_overhead,
        )

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
                    "Extracted document content: structured extraction "
                    f"available — see {pointed_at.heading} below."
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
        proposal_section = f"{proposal_block}\n\n" if proposal_block else ""
        cic_s10a_section = f"{cic_s10a_block}\n\n" if cic_s10a_block else ""
        cic_r20_section = f"{cic_r20_block}\n\n" if cic_r20_block else ""
        sitevisit_section = f"{sitevisit_block}\n\n" if sitevisit_block else ""
        ledger_section = f"{ledger_block}\n\n" if ledger_block else ""
        reference_text = "".join(f"{block}\n\n" for block in reference_sections)
        credit_need_section = (
            f"{credit_need_block}\n\n" if credit_need_block else ""
        )
        return truncate_text(
            (
                f"{base}\n\n"
                f"{source_list_block}\n\n"
                f"{unit_warning}"
                f"{metrics_block}\n\n"
                f"{financial_statement_block}\n\n"
                f"{proposal_section}"
                f"{cic_s10a_section}"
                f"{cic_r20_section}"
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
        """Structured form of the deterministic ratio computation."""

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
        """The credit-need table as data, for the run log rather than a prompt."""

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
        """Documents as data, with the OCR text bounded. """

        limit = self.config.result_content_char_limit
        records = to_dict_list(documents)
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
