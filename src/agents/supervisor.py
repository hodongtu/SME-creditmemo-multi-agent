"""Supervisor orchestration (extracted from the notebook cell 20).

Canonical AI-agent workflow: routing, document prep/classification, gap analysis,
memo workflow, finalize + hallucination + tỷ-VNĐ formatting. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langgraph.graph import END, StateGraph

from src.agents.financial_ratio_calculator import (
    FinancialRatioCalculator,
)
from src.utils.extractors import extract_document_text
from src.utils.formatting import convert_amounts_in_text
from src.utils.template_leak import check_template_leakage
from src.utils.assertion_check import (
    check_assertion_labelling,
    check_assertion_separation,
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
from src.agents.document_classification import (
    DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT,
    VALID_DOCUMENT_AGENTS,
    compute_file_hash,
    discover_documents,
    is_bctc_document,
    relevant_agents_for_document,
    rule_classify_document,
)
from src.agents.bctc_extraction import (
    build_bctc_extraction_chain,
    extract_bctc_structured_data,
)
from src.agents.specialist import (
    BusinessActivityAnalysis,
    CreditMemoComposerAgent,
    CreditProposalAnalysis,
    CreditRelationshipAnalysis,
    FinancialAnalysis,
    RiskAssessment,
)
from src.agents.guardrails import (
    HallucinationGuardrail,
    LocalGuardrails,
    WebSearchProcessorAgent,
)

DECISION_SYSTEM_PROMPT = """
You are an expert for SME underwriting at a bank.
Your task is to serve as a supervisor/planner for a multi-agent team.

Available routes:
- CONVERSATION_AGENT: greetings, general chat, clarifying questions, or
  non-analysis requests.
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

    # Relative per-document budget weights in _build_user_input: a document's own
    # primary/general evidence should dominate shared/secondary evidence pulled in
    # from another agent's primary document.
    # The deterministic ratio block goes to the agents that reason about the
    # figures: the financial agent computes from them, the risk agent judges
    # leverage and debt-service against them.
    METRICS_BLOCK_AGENTS = ("FINANCIAL_ANALYSIS_AGENT", "RISK_ASSESSMENT_AGENT")

    PRIMARY_DOC_BUDGET_WEIGHT = 3
    SECONDARY_DOC_BUDGET_WEIGHT = 1

    def __init__(self, config: Config):
        self.config = config
        self.guardrails = (
            LocalGuardrails(config.hallucination_llm)
            if config.enable_safety_guardrails
            else None
        )
        self.hallucination_guardrail = (
            HallucinationGuardrail(config.hallucination_llm)
            if config.enable_hallucination_guardrail
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
        workflow.add_node("web_search", self._graph_web_search)
        workflow.add_node("conversation", self._graph_run_conversation)
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
        workflow.add_edge("discover_documents", "classify_documents")
        workflow.add_edge("classify_documents", "decide_workflow")
        workflow.add_edge("decide_workflow", "evidence_gap_check")
        workflow.add_conditional_edges(
            "evidence_gap_check",
            self._graph_after_evidence_gap_check,
            {"blocked": END, "continue": "web_search"},
        )
        workflow.add_conditional_edges(
            "web_search",
            self._graph_select_workflow_branch,
            {
                "conversation": "conversation",
                "single_business_activity": "single_business_activity",
                "single_credit_relationship": "single_credit_relationship",
                "single_financial_analysis": "single_financial_analysis",
                "single_risk_assessment": "single_risk_assessment",
                "single_credit_proposal": "single_credit_proposal",
                "full_credit_memo": "full_credit_memo",
            },
        )
        for node in [
            "conversation",
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
        return {**state, "files": files, "steps": steps}

    def _graph_classify_documents(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Extract and classify uploaded documents."""

        steps = state.get("steps", [])
        documents = self._prepare_documents(
            state.get("files") or [],
            state.get("query", ""),
            state.get("history_context", ""),
            steps,
        )
        self._extract_bctc_documents(documents, steps)
        document_routes: set[str] = set()
        for doc in documents:
            if doc.agent == "GENERAL_CONTEXT":
                document_routes.add("GENERAL_CONTEXT")
                continue
            document_routes.update(doc.relevant_agents)
        document_summary = self._format_document_summary(documents)
        return {
            **state,
            "documents": documents,
            "document_routes": document_routes,
            "document_summary": document_summary,
        }

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
            decision.get("route", "CONVERSATION_AGENT"),
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
                {
                    "status": "SKIPPED",
                    "summary": "Required evidence is missing.",
                },
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

        return state.get("workflow_mode", "conversation")

    def _graph_run_conversation(
        self,
        state: UnderwritingGraphState,
    ) -> UnderwritingGraphState:
        """Run the conversation branch."""

        return {**state, "output_state": self._run_conversation_branch(state)}

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

    def _run_conversation_branch(
        self,
        state: UnderwritingGraphState,
    ) -> dict[str, Any]:
        """Adapt graph state to the existing conversation runner."""

        return self._run_conversation(
            state.get("query", ""),
            state.get("history_context", ""),
            state.get("decision") or {},
            state.get("documents") or [],
            state.get("web_context", ""),
            state.get("execution_plan") or {},
            state.get("gap_analysis") or {},
            state.get("steps", []),
        )

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
            agent_scores = classification.get("scores", {})
            llm_secondary_agents = classification.get("llm_secondary_agents", [])
            relevant_agents = sorted(
                relevant_agents_for_document(
                    classification["agent"],
                    agent_scores,
                    llm_secondary_agents,
                )
            )
            secondary_agents = [
                agent
                for agent in relevant_agents
                if agent != classification["agent"]
            ]
            is_bctc = (
                "FINANCIAL_ANALYSIS_AGENT" in relevant_agents
                and is_bctc_document(filename, content)
            )
            steps.append(
                f"Classified document: {filename} -> {classification['agent']}"
                + (
                    f" (also relevant: {', '.join(secondary_agents)})"
                    if secondary_agents
                    else ""
                )
                + (" [BCTC]" if is_bctc else "")
            )
            documents.append(
                ClassifiedDocument(
                    path=file_path,
                    filename=filename,
                    content=content,
                    agent=classification["agent"],
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
                    agent_scores=agent_scores,
                    llm_secondary_agents=llm_secondary_agents,
                    relevant_agents=relevant_agents,
                    is_bctc=is_bctc,
                )
            )
        return documents

    def _extract_bctc_documents(
        self,
        documents: list[ClassifiedDocument],
        steps: list[str],
    ) -> None:
        """Run structured JSON extraction for every BCTC-tagged document.

        Multiple BCTC files can be uploaded together (e.g. current + prior
        year), so extraction runs concurrently, bounded by max_concurrency —
        same pattern as the parallel analysis agents in
        _run_credit_memo_workflow. Mutates bctc_extraction/
        bctc_extraction_error on each doc in place; never raises, so a failed
        or unconfigured extraction always leaves a clean fallback signal for
        _build_user_input to fall back to raw OCR content.
        """

        bctc_docs = [doc for doc in documents if doc.is_bctc]
        if not bctc_docs:
            return
        if not self.bctc_extraction_chain:
            for doc in bctc_docs:
                doc.bctc_extraction_error = "No bctc_extraction_llm configured."
            steps.append(
                f"Skipped BCTC extraction for {len(bctc_docs)} document(s): "
                "no bctc_extraction_llm configured."
            )
            return

        def _run(doc: ClassifiedDocument):
            result, error = extract_bctc_structured_data(
                self.bctc_extraction_chain,
                doc.filename,
                doc.content,
            )
            return doc, result, error

        max_workers = max(1, min(len(bctc_docs), self.config.max_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_run, doc) for doc in bctc_docs]
            for future in futures:
                doc, result, error = future.result()
                doc.bctc_extraction = result
                doc.bctc_extraction_error = error
                steps.append(
                    f"BCTC extraction: {doc.filename} -> "
                    + ("ok" if result is not None else f"failed: {error}")
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
        if (
            rule["agent"] != "GENERAL_CONTEXT"
            and rule["confidence"]
            >= self.config.document_classifier_rule_confidence_threshold
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
                if result.get("agent") not in VALID_DOCUMENT_AGENTS:
                    return rule
                # Keyword scores are always computed by the rule pass; keep them
                # even when the LLM's primary label wins, so secondary-relevance
                # detection (relevant_agents_for_document) works for every
                # document, not just rule-confident ones.
                result["scores"] = rule.get("scores", {})
                result["llm_secondary_agents"] = [
                    agent
                    for agent in result.get("secondary_agents", [])
                    if agent in VALID_DOCUMENT_AGENTS
                    and agent not in {result.get("agent"), "GENERAL_CONTEXT"}
                ]
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
                ("system", DOCUMENT_CLASSIFICATION_SYSTEM_PROMPT),
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
                route = decision.get("route", "CONVERSATION_AGENT")
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
            "CONVERSATION_AGENT": "conversation",
            "BUSINESS_ACTIVITY_AGENT": "single_business_activity",
            "CREDIT_RELATIONSHIP_AGENT": "single_credit_relationship",
            "FINANCIAL_ANALYSIS_AGENT": "single_financial_analysis",
            "RISK_ASSESSMENT_AGENT": "single_risk_assessment",
            "CREDIT_PROPOSAL_AGENT": "single_credit_proposal",
            "CREDIT_MEMO": "full_credit_memo",
        }
        return route_modes.get(route, "conversation")

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
        if route == "CONVERSATION_AGENT" and has_file:
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
        if route in {
            "CONVERSATION_AGENT",
            "FINANCIAL_ANALYSIS_AGENT",
            "BUSINESS_ACTIVITY_AGENT",
            "CREDIT_RELATIONSHIP_AGENT",
            "RISK_ASSESSMENT_AGENT",
            "CREDIT_PROPOSAL_AGENT",
            "CREDIT_MEMO",
        }:
            return route
        return "CONVERSATION_AGENT"

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
        return "CONVERSATION_AGENT"

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
            "CONVERSATION_AGENT": [],
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
            "recommended_actions": (
                ["run_available_analysis_agents"]
                if route != "CONVERSATION_AGENT"
                else ["answer_directly"]
            ),
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
        elif route in {
            "FINANCIAL_ANALYSIS_AGENT",
            "BUSINESS_ACTIVITY_AGENT",
            "CREDIT_RELATIONSHIP_AGENT",
        }:
            agents = [route]
            order = [
                (
                    "financial_analysis"
                    if route == "FINANCIAL_ANALYSIS_AGENT"
                    else "credit_relationship_analysis"
                    if route == "CREDIT_RELATIONSHIP_AGENT"
                    else "business_activity_analysis"
                ),
                "reflection",
            ]
        else:
            agents = ["CONVERSATION_AGENT"]
            order = ["conversation", "reflection"]

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
        if not self.web_search_agent or decision["route"] == "CONVERSATION_AGENT":
            return ""
        steps.append("Running WEB_SEARCH_AGENT")
        web_query = f"{query}\n\n{document_summary}"
        return self.web_search_agent.process_web_search_results(
            web_query,
            conversation_history,
        )

    def _run_conversation(
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
        steps.append("Running CONVERSATION_AGENT")
        prompt = f"""
        You are an AI-powered Credit Underwriting Conversation Assistant.
        Answer in the same language as the user request.

        Recent conversation history:
        {history_context or 'No previous conversation.'}

        User query:
        {input_text}
        """
        raw = (
            self.config.conversation_llm.invoke(prompt)
            if self.config.conversation_llm
            else AIMessage(
                content=(
                    "Mình sẵn sàng hỗ trợ. Vui lòng cung cấp câu hỏi "
                    "hoặc tài liệu cần phân tích."
                )
            )
        )
        response = extract_text_from_agent_output(raw)
        return self._finalize(
            input_text,
            response,
            "CONVERSATION_AGENT",
            decision,
            documents,
            {"CONVERSATION_AGENT": response},
            web_context,
            history_context,
            execution_plan,
            gap_analysis,
            steps,
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
        response = extract_text_from_agent_output(agent.analyze(user_input))

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

        def _run_business() -> str:
            business_input = self._build_user_input(
                input_text,
                documents,
                history_context,
                "BUSINESS_ACTIVITY_AGENT",
                web_context,
                gap_analysis,
            )
            return extract_text_from_agent_output(
                BusinessActivityAnalysis(self.config.analysis_llm).analyze(
                    business_input
                )
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
            return extract_text_from_agent_output(
                CreditRelationshipAnalysis(
                    self.config.analysis_llm
                ).analyze(credit_relationship_input)
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
            return extract_text_from_agent_output(
                FinancialAnalysis(self.config.analysis_llm).analyze(
                    financial_input
                )
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
            return extract_text_from_agent_output(
                CreditProposalAnalysis(self.config.analysis_llm).analyze(
                    proposal_input
                )
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
        risk_text = extract_text_from_agent_output(
            RiskAssessment(self.config.analysis_llm).analyze(risk_input)
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
        if self.guardrails:
            allowed, checked_response = self.guardrails.check_output(
                response,
                input_text,
            )
            if not allowed:
                agent_name = "OUTPUT_GUARDRAILS"
            response = checked_response

        hallucination = self._run_hallucination_check(
            input_text,
            response,
            decision,
            documents,
            web_context,
            history_context,
            sub_agent_outputs,
        )
        risk = hallucination.get("hallucination_risk", "UNKNOWN")
        action = hallucination.get("final_action", "PASS")
        steps.append(f"Hallucination check: {risk}/{action}")
        # Convert all VNĐ amounts to tỷ VNĐ for display; the hallucination
        # judge already ran on the raw-number response above.
        response = convert_amounts_in_text(response)
        # Surface the verdict — including a clean one, and including the case
        # where the check could not run. Without this the reader cannot tell
        # "checked and passed" apart from "never checked".
        response += self._format_confidence_warnings(hallucination)
        response += self._format_check_unavailable(hallucination)
        # Prompt rules are not enforcement: re-read the finished report and
        # flag places where inference is presented as evidence.
        response += self._format_assertion_findings(response)
        response += self._format_template_findings(response)
        return self._build_state(
            input_text,
            response,
            agent_name,
            decision,
            documents,
            sub_agent_outputs,
            hallucination,
            execution_plan,
            gap_analysis,
            web_context,
            steps + ["Built final response"],
            self._build_document_selections(documents),
            self._build_financial_metrics_data(documents),
        )

    @staticmethod
    def _claim_text(claim: Any) -> str:
        """Render one judge finding, which may be a string or a dict."""

        if isinstance(claim, dict):
            text = str(
                claim.get("claim")
                or claim.get("statement")
                or claim.get("text")
                or claim.get("description")
                or ""
            ).strip()
            reason = str(claim.get("reason") or claim.get("explanation") or "").strip()
            status = str(claim.get("support_status") or "").strip()
            parts = [part for part in [text, status, reason] if part]
            return " — ".join(parts) if parts else str(claim)
        return str(claim).strip()

    @classmethod
    def _format_confidence_warnings(cls, hallucination: dict[str, Any]) -> str:
        """Render the judge's verdict as a section appended to the report.

        Emitted whenever the check actually ran — including a clean verdict —
        because silence would be indistinguishable from "the check never ran".
        Returns "" only when the check did not run; _finalize reports that case
        separately.
        """

        if not hallucination:
            return ""
        if str(hallucination.get("status", "")).upper() in {
            "DISABLED",
            "SKIPPED",
            "ERROR",
        }:
            return ""

        claims = hallucination.get("claims") or []
        rows = [
            (cls._claim_text_only(claim), cls._claim_status_of(claim), cls._claim_reason(claim))
            for claim in claims
        ]
        rows = [row for row in rows if row[0]]
        counts = Counter(status for _, status, _ in rows)
        problem_count = sum(
            count
            for status, count in counts.items()
            if status in cls.PROBLEM_STATUSES
        )
        numeric_errors = [
            text
            for text in (
                cls._claim_text(item)
                for item in hallucination.get("numeric_errors") or []
            )
            if text
        ]
        # When the judge returns claims without a usable support_status, the
        # per-status counts are meaningless — fall back to unsupported_claims so
        # a flagged verdict is never reported as clean.
        flagged = len(hallucination.get("unsupported_claims") or [])
        has_known_status = any(
            status in cls.STATUS_LABELS_VI for _, status, _ in rows
        )
        if not has_known_status:
            problem_count = flagged
        else:
            problem_count = max(problem_count, flagged)

        risk = str(hallucination.get("hallucination_risk", "UNKNOWN")).upper()
        action = str(hallucination.get("final_action", "PASS")).upper()
        summary = str(hallucination.get("summary", "")).strip()

        lines = [
            # Two blank lines: a bare "---" directly under a text line would
            # render as a setext H2, turning the report's last line into a heading.
            "",
            "",
            "---",
            "",
            "## Kiểm chứng độ tin cậy",
            "",
            cls._coverage_line(rows, counts, problem_count, numeric_errors, risk),
        ]
        if summary:
            lines += ["", f"**Nhận xét của bộ kiểm tra**: {summary}"]

        if rows:
            # Problems first so they are read before the supported claims.
            ordered = sorted(
                rows,
                key=lambda row: (row[1] not in cls.PROBLEM_STATUSES, row[0]),
            )
            lines += [
                "",
                "| Nhận định | Đánh giá | Lý do |",
                "|---|---|---|",
            ]
            lines += [
                "| {} | {} | {} |".format(
                    cls._escape_cell(text),
                    cls.STATUS_LABELS_VI.get(status, status or "Không rõ"),
                    cls._escape_cell(reason) or "-",
                )
                for text, status, reason in ordered
            ]

        if numeric_errors:
            lines += ["", "**Số liệu có dấu hiệu sai lệch:**"]
            lines += [f"- {text}" for text in numeric_errors]
        if action != "PASS":
            lines += ["", f"**Khuyến nghị của bộ kiểm tra**: {action}"]

        lines += [
            "",
            (
                "*Đây là kết quả đối chiếu tự động bằng LLM, có thể bỏ sót hoặc "
                "đánh giá sai. Không thay thế cho việc thẩm định của cán bộ tín "
                "dụng.*"
            ),
        ]
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
    def _format_assertion_findings(response: str) -> str:
        """Append findings where evidence and inference were not kept apart."""

        findings = check_assertion_labelling(response)
        findings += check_assertion_separation(response)
        if not findings:
            return ""
        lines = [
            "",
            "**Ranh giới dữ kiện / suy luận:**",
            "",
        ]
        lines += [f"- {finding}" for finding in findings]
        return "\n".join(lines) + "\n"

    @classmethod
    def _format_check_unavailable(cls, hallucination: dict[str, Any]) -> str:
        """Say so when the check did not run, instead of staying silent."""

        status = str((hallucination or {}).get("status", "")).upper()
        if status not in {"DISABLED", "SKIPPED", "ERROR"}:
            return ""
        reasons = {
            "DISABLED": "đã tắt trong cấu hình",
            "SKIPPED": "không có đủ dữ liệu để đối chiếu",
            "ERROR": "gặp lỗi khi chạy",
        }
        summary = str((hallucination or {}).get("summary", "")).strip()
        detail = f" ({summary})" if summary else ""
        return (
            "\n\n---\n\n## Kiểm chứng độ tin cậy\n\n"
            f"*Chưa chạy được kiểm chứng tự động: {reasons[status]}{detail}. "
            "Các nhận định trong báo cáo chưa được đối chiếu lại với hồ sơ.*\n"
        )

    STATUS_LABELS_VI = {
        "SUPPORTED": "Có căn cứ",
        "PARTIALLY_SUPPORTED": "Chưa đầy đủ",
        "UNSUPPORTED": "Không có căn cứ",
        "NOT_VERIFIABLE": "Không kiểm chứng được",
    }
    PROBLEM_STATUSES = {
        "PARTIALLY_SUPPORTED",
        "UNSUPPORTED",
        "NOT_VERIFIABLE",
    }

    @classmethod
    def _coverage_line(
        cls,
        rows: list[tuple[str, str, str]],
        counts: "Counter[str]",
        problem_count: int,
        numeric_errors: list[str],
        risk: str,
    ) -> str:
        """State what was checked, so a clean result is still visible."""

        has_known_status = any(
            status in cls.STATUS_LABELS_VI for _, status, _ in rows
        )
        if not rows:
            checked = "Bộ kiểm tra không trả về danh sách nhận định cụ thể."
        elif not has_known_status:
            # Claims came back without a support status — do not invent counts.
            checked = (
                f"Đã đối chiếu {len(rows)} nhận định với hồ sơ "
                "(bộ kiểm tra không phân loại được từng nhận định)."
            )
        else:
            parts = [f"{counts.get('SUPPORTED', 0)} có căn cứ"]
            for status in ("PARTIALLY_SUPPORTED", "UNSUPPORTED", "NOT_VERIFIABLE"):
                if counts.get(status):
                    parts.append(
                        f"{counts[status]} {cls.STATUS_LABELS_VI[status].lower()}"
                    )
            checked = (
                f"Đã đối chiếu {len(rows)} nhận định với hồ sơ — "
                + ", ".join(parts)
                + "."
            )
        verdict = (
            "Không phát hiện nội dung thiếu căn cứ."
            if not problem_count and not numeric_errors
            else (
                f"Cần rà soát {problem_count} nhận định"
                + (
                    f" và {len(numeric_errors)} số liệu."
                    if numeric_errors
                    else "."
                )
            )
        )
        return f"{checked} Mức rủi ro: **{risk}**. {verdict}"

    @staticmethod
    def _escape_cell(text: str) -> str:
        """Keep a markdown table intact when a claim contains pipes/newlines."""

        return " ".join(str(text or "").split()).replace("|", "\\|")

    @staticmethod
    def _claim_text_only(claim: Any) -> str:
        """The claim statement itself, without status/reason appended."""

        if isinstance(claim, dict):
            return str(
                claim.get("claim")
                or claim.get("statement")
                or claim.get("text")
                or claim.get("description")
                or ""
            ).strip()
        return str(claim).strip()

    @staticmethod
    def _claim_status_of(claim: Any) -> str:
        if not isinstance(claim, dict):
            return ""
        return str(
            claim.get("support_status") or claim.get("status") or ""
        ).strip().upper()

    @staticmethod
    def _claim_reason(claim: Any) -> str:
        if not isinstance(claim, dict):
            return ""
        return str(claim.get("reason") or claim.get("explanation") or "").strip()

    def _run_hallucination_check(
        self,
        input_text: str,
        response: str,
        decision: dict[str, Any],
        documents: list[ClassifiedDocument],
        web_context: str,
        history_context: str,
        sub_agent_outputs: dict[str, str],
    ) -> dict[str, Any]:
        if not self.config.enable_hallucination_guardrail:
            return {"status": "DISABLED"}
        return self.hallucination_guardrail.check(
            input_text,
            response,
            decision["route"],
            evidence_documents=[asdict(doc) for doc in documents],
            web_context=web_context,
            history_context=history_context,
            sub_agent_outputs=sub_agent_outputs,
            # The agent saw these deterministic figures; the judge must too,
            # otherwise correctly-derived ratios look unsupported.
            metrics_block=self._build_financial_metrics_block(
                documents,
                "FINANCIAL_ANALYSIS_AGENT",
            ),
        )

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
            return doc.agent == target_agent or doc.agent == "GENERAL_CONTEXT"

        metrics_block = self._build_financial_metrics_block(
            documents,
            target_agent,
        )
        # State the periods explicitly: several BCTC files overlap by a year, so
        # the merged set (e.g. 2 files -> 3 years) does not match the sample
        # column count in the layout. Telling the agent removes the guesswork.
        if target_agent == "FINANCIAL_ANALYSIS_AGENT":
            periods = (self._build_financial_metrics_data(usable) or {}).get("years")
            if periods:
                base += (
                    "\n\nCác kỳ báo cáo có dữ liệu (dùng đúng số cột này cho mọi "
                    f"bảng theo năm, thứ tự tăng dần): {', '.join(periods)}"
                )
        bctc_block = (
            self._build_bctc_structured_block(usable)
            if target_agent == "FINANCIAL_ANALYSIS_AGENT"
            else ""
        )
        remaining = max(
            1_000,
            budget - len(base) - len(metrics_block) - len(bctc_block),
        )
        # Primary/general docs get more budget than secondary/shared docs, so a
        # document pulled in only because it's also relevant to this agent can't
        # crowd out the agent's own core evidence.
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
            relevance = (
                "Relevance to this agent: primary"
                if _is_primary(doc)
                else (
                    "Relevance to this agent: shared/secondary evidence "
                    f"(also classified as {doc.agent})"
                )
            )
            # A successfully-extracted BCTC doc is represented by its
            # structured JSON (see bctc_block below), not its raw OCR dump —
            # that's the whole point of the extraction pass. Any other doc
            # (not BCTC, or extraction failed/unavailable) keeps raw content
            # so no evidence is ever silently dropped.
            if (
                target_agent == "FINANCIAL_ANALYSIS_AGENT"
                and doc.is_bctc
                and doc.bctc_extraction
            ):
                content_section = (
                    "Extracted document content: đã trích xuất có cấu trúc "
                    "— xem [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT] bên dưới."
                )
            else:
                content_section = "\n".join(
                    [
                        "Extracted document content:",
                        truncate_text(doc.content, _doc_budget(doc)),
                    ]
                )
            blocks.append(
                "\n".join(
                    [
                        f"Document filename: {doc.filename}",
                        f"Classified target agent: {doc.agent}",
                        relevance,
                        f"Classification reason: {doc.reasoning}",
                        f"Extraction status: {doc.extraction_status}",
                        f"Extraction error: {doc.extraction_error}",
                        content_section,
                    ]
                )
            )
        docs_text = "\n\n---\n\n".join(blocks)
        return truncate_text(
            (
                f"{base}\n\n"
                f"{metrics_block}\n\n"
                f"{bctc_block}\n\n"
                "Uploaded document extracted content:\n\n"
                f"{docs_text}"
            ),
            budget,
        )

    @staticmethod
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

        if target_agent not in Supervisor.METRICS_BLOCK_AGENTS:
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

    @staticmethod
    def _build_bctc_structured_block(
        selected: list[ClassifiedDocument],
    ) -> str:
        bctc_docs = [
            doc for doc in selected if doc.is_bctc and doc.bctc_extraction
        ]
        if not bctc_docs:
            return ""
        parts = ["[DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT]"]
        for doc in bctc_docs:
            parts.append(
                f"--- {doc.filename} ---\n"
                + json.dumps(doc.bctc_extraction, ensure_ascii=False, indent=2)
            )
        return "\n\n".join(parts)

    @staticmethod
    def _select_documents_for_agent(
        documents: list[ClassifiedDocument],
        target_agent: str,
    ) -> list[ClassifiedDocument]:
        primary = [doc for doc in documents if doc.agent == target_agent]
        general = [doc for doc in documents if doc.agent == "GENERAL_CONTEXT"]
        # A document that wasn't primarily classified for this agent can still be
        # real evidence for it (keyword score and/or LLM secondary-label signal).
        # Do NOT dump ALL documents regardless (cross-contamination + token
        # blow-up when many files are uploaded) — only documents that clear the
        # secondary-relevance bar, ranked by their score for this agent.
        secondary = sorted(
            (
                doc
                for doc in documents
                if doc.agent != target_agent
                and doc.agent != "GENERAL_CONTEXT"
                and target_agent in doc.relevant_agents
            ),
            key=lambda doc: doc.agent_scores.get(target_agent, 0),
            reverse=True,
        )
        return primary + general + secondary

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
                "primary": [
                    doc.filename
                    for doc in selected
                    if doc.agent == target_agent or doc.agent == "GENERAL_CONTEXT"
                ],
                "secondary": [
                    doc.filename
                    for doc in selected
                    if doc.agent != target_agent and doc.agent != "GENERAL_CONTEXT"
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

    @staticmethod
    def _format_document_summary(documents: list[ClassifiedDocument]) -> str:
        lines = []
        for doc in documents:
            secondary = [
                agent for agent in doc.relevant_agents if agent != doc.agent
            ]
            extra = f" (+{', '.join(secondary)})" if secondary else ""
            bctc_tag = ""
            if doc.is_bctc:
                bctc_tag = (
                    " [BCTC]" if doc.bctc_extraction else " [BCTC, trích xuất lỗi]"
                )
            lines.append(
                f"- {doc.filename}: {doc.agent}{extra}{bctc_tag} "
                f"(confidence={doc.confidence:.2f}, "
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
        hallucination_check: dict[str, Any] | None = None,
        execution_plan: dict[str, Any] | None = None,
        gap_analysis: dict[str, Any] | None = None,
        web_context: str = "",
        steps: list[str] | None = None,
        document_selections: dict[str, dict[str, list[str]]] | None = None,
        financial_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "response": response,
            "agent_name": agent_name,
            "decision": decision or {},
            "document_classifications": to_dict_list(documents or []),
            # Per-agent snapshot of _select_documents_for_agent's output
            # (primary vs secondary/shared filenames) — for monitoring/testing
            # which documents actually feed each specialist's LLM input.
            "document_selections": document_selections or {},
            # Deterministic ratio computation as data (not just the markdown
            # block) so a finished run can be audited afterwards.
            "financial_metrics": financial_metrics or {},
            "sub_agent_outputs": sub_agent_outputs or {},
            "hallucination_check": hallucination_check or {},
            "execution_plan": execution_plan or {},
            "gap_analysis": gap_analysis or {},
            "web_context": web_context,
            "steps": steps or [],
        }
