"""Supervisor orchestration (extracted from the notebook cell 20).

Canonical AI-agent workflow: routing, document prep/classification, gap analysis,
memo workflow, finalize + hallucination + tỷ-VNĐ formatting. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import os
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
    rule_classify_document,
)
from src.agents.specialist import (
    BusinessActivityAnalysis,
    CreditMemoComposerAgent,
    CreditRelationshipAnalysis,
    FinancialAnalysis,
    RiskAssessment,
    calculate_credit_proposal,
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
- CREDIT_PROPOSAL: credit proposal analysis or credit facility proposal
  calculation. This route uses a calculator, not an LLM.
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
  CREDIT_PROPOSAL.
- If the user asks for risk assessment, credit risk, approval view, or risk
  conclusion, use RISK_ASSESSMENT_AGENT.
- If the user explicitly asks to create, draft, prepare, or generate a Credit
  Memo or báo cáo thẩm định, use CREDIT_MEMO.
- CREDIT_MEMO must run the full underwriting workflow:
  BUSINESS_ACTIVITY_AGENT -> CREDIT_RELATIONSHIP_AGENT ->
  FINANCIAL_ANALYSIS_AGENT -> CREDIT_PROPOSAL ->
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

        documents = self._prepare_documents(
            state.get("files") or [],
            state.get("query", ""),
            state.get("history_context", ""),
            state.get("steps", []),
        )
        document_routes = {doc.agent for doc in documents}
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
        """Run the single Credit Proposal calculator branch."""

        return {**state, "output_state": self._run_credit_proposal(state)}

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

    def _run_credit_proposal(
        self,
        state: UnderwritingGraphState,
    ) -> dict[str, Any]:
        """Run the standalone Credit Proposal calculator."""

        query = state.get("query", "")
        documents = state.get("documents") or []
        steps = state.get("steps", [])
        steps.append("Running CREDIT_PROPOSAL calculator")
        response = calculate_credit_proposal(
            input_text=query,
            business_analysis="",
            credit_relationship_analysis="",
            financial_analysis="",
            documents=[asdict(doc) for doc in documents],
        )
        return self._finalize(
            query,
            response,
            "CREDIT_PROPOSAL",
            state.get("decision") or {},
            documents,
            {"CREDIT_PROPOSAL": response},
            state.get("web_context", ""),
            state.get("history_context", ""),
            state.get("execution_plan") or {},
            state.get("gap_analysis") or {},
            steps,
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
            steps.append(
                f"Classified document: {filename} -> {classification['agent']}"
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
                    agent_scores=classification.get("scores", {}),
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
            (CREDIT_PROPOSAL_ROUTE_KEYWORDS, "CREDIT_PROPOSAL"),
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
            "CREDIT_PROPOSAL": "single_credit_proposal",
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
            return "CREDIT_PROPOSAL"
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
                "CREDIT_PROPOSAL",
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
            "CREDIT_PROPOSAL",
            "CREDIT_MEMO",
        }:
            return route
        return "CONVERSATION_AGENT"

    def _fallback_route(self, input_text: str, has_file: bool) -> AgentName:
        normalized = input_text.lower()
        if self._contains_any(normalized, CREDIT_MEMO_KEYWORDS):
            return "CREDIT_MEMO"
        if self._contains_any(normalized, CREDIT_PROPOSAL_ROUTE_KEYWORDS):
            return "CREDIT_PROPOSAL"
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
            "CREDIT_PROPOSAL": "credit_proposal_documents",
            "RISK_ASSESSMENT_AGENT": "risk_assessment_documents",
            "GENERAL_CONTEXT": "general_context",
        }
        for doc in documents:
            bucket = agent_to_bucket.get(doc.agent, "general_context")
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
                "CREDIT_PROPOSAL",
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
        elif route == "CREDIT_PROPOSAL":
            agents = ["CREDIT_PROPOSAL"]
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
        if agent_name == "CREDIT_RELATIONSHIP_AGENT":
            user_input = f"""
            {user_input}

            Instruction:
            - Use T24 and CIC/bureau database tools when customer identifiers
              are available.
            - If tool data is unavailable, clearly state the limitation.
            """
        if agent_name == "RISK_ASSESSMENT_AGENT":
            user_input = f"""
            {user_input}

            Instruction:
            - Return only the Risk Assessment analysis.
            - Do not compose a Credit Memo or underwriting report.
            """
        if agent_name == "FINANCIAL_ANALYSIS_AGENT":
            agent = FinancialAnalysis(self.config.analysis_llm)
        elif agent_name == "CREDIT_RELATIONSHIP_AGENT":
            agent = CreditRelationshipAnalysis(
                self.config.analysis_llm
            )
        elif agent_name == "RISK_ASSESSMENT_AGENT":
            agent = RiskAssessment(self.config.analysis_llm)
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
            "Running BUSINESS_ACTIVITY / CREDIT_RELATIONSHIP / FINANCIAL "
            f"agents in parallel (max_concurrency={self.config.max_concurrency})"
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

        max_workers = max(1, min(3, self.config.max_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            business_future = executor.submit(_run_business)
            credit_relationship_future = executor.submit(_run_credit_relationship)
            financial_future = executor.submit(_run_financial)
            business_text = business_future.result()
            credit_relationship_text = credit_relationship_future.result()
            financial_text = financial_future.result()

        steps.append("Running CREDIT_PROPOSAL calculator")
        credit_proposal_text = calculate_credit_proposal(
            input_text=input_text,
            business_analysis=business_text,
            credit_relationship_analysis=credit_relationship_text,
            financial_analysis=financial_text,
            documents=[asdict(doc) for doc in documents],
        )

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

        Credit proposal from CREDIT_PROPOSAL calculator:
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
            "CREDIT_PROPOSAL": credit_proposal_text,
            "RISK_ASSESSMENT_AGENT": risk_text,
            "CREDIT_MEMO": response,
        }
        return self._finalize(
            input_text,
            response,
            (
                "BUSINESS_ACTIVITY_AGENT, CREDIT_RELATIONSHIP_AGENT, "
                "FINANCIAL_ANALYSIS_AGENT, CREDIT_PROPOSAL, "
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
        )

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
        metrics_block = self._build_financial_metrics_block(
            usable,
            target_agent,
        )
        remaining = max(1_000, budget - len(base) - len(metrics_block))
        per_doc_budget = max(1_000, remaining // max(1, len(usable)))

        blocks = []
        for doc in usable:
            blocks.append(
                "\n".join(
                    [
                        f"Document filename: {doc.filename}",
                        f"Classified target agent: {doc.agent}",
                        f"Classification reason: {doc.reasoning}",
                        f"Extraction status: {doc.extraction_status}",
                        f"Extraction error: {doc.extraction_error}",
                        "Extracted document content:",
                        truncate_text(doc.content, per_doc_budget),
                    ]
                )
            )
        docs_text = "\n\n---\n\n".join(blocks)
        return truncate_text(
            (
                f"{base}\n\n"
                f"{metrics_block}\n\n"
                "Uploaded document extracted content:\n\n"
                f"{docs_text}"
            ),
            budget,
        )

    @staticmethod
    def _build_financial_metrics_block(
        selected: list[ClassifiedDocument],
        target_agent: str,
    ) -> str:
        if target_agent != "FINANCIAL_ANALYSIS_AGENT" or not selected:
            return ""
        try:
            return FinancialRatioCalculator().build_analysis_block(
                [asdict(doc) for doc in selected]
            )
        except Exception as exc:
            return f"[PRE-COMPUTED FINANCIAL METRICS unavailable: {exc}]"

    RELEVANCE_THRESHOLD = 3

    @staticmethod
    def _select_documents_for_agent(
        documents: list[ClassifiedDocument],
        target_agent: str,
    ) -> list[ClassifiedDocument]:
        target = [doc for doc in documents if doc.agent == target_agent]
        general = [doc for doc in documents if doc.agent == "GENERAL_CONTEXT"]
        if target:
            return target + general
        # No document was classified primarily for this agent. Do NOT dump ALL
        # documents (cross-contamination + token blow-up when many files are
        # uploaded); surface only general-context docs plus any document that
        # still scored as relevant for this agent, ranked by that score.
        relevant = sorted(
            (
                doc
                for doc in documents
                if doc.agent != "GENERAL_CONTEXT"
                and doc.agent_scores.get(target_agent, 0)
                >= Supervisor.RELEVANCE_THRESHOLD
            ),
            key=lambda doc: doc.agent_scores.get(target_agent, 0),
            reverse=True,
        )
        return general + relevant

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
        return "\n".join(
            (
                f"- {doc.filename}: {doc.agent} "
                f"(confidence={doc.confidence:.2f}, "
                f"extraction={doc.extraction_status})"
            )
            for doc in documents
        )

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
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "response": response,
            "agent_name": agent_name,
            "decision": decision or {},
            "document_classifications": to_dict_list(documents or []),
            "sub_agent_outputs": sub_agent_outputs or {},
            "hallucination_check": hallucination_check or {},
            "execution_plan": execution_plan or {},
            "gap_analysis": gap_analysis or {},
            "web_context": web_context,
            "steps": steps or [],
        }
