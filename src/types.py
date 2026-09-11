"""Shared types and small helpers for the underwriting workflow."""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypedDict


AgentName = Literal[
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL_AGENT",
]
WorkflowMode = Literal[
    "single_business_activity",
    "single_credit_relationship",
    "single_financial_analysis",
    "single_credit_proposal",
]
DocumentAgentName = Literal[
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL_AGENT",
    "GENERAL_CONTEXT",
]
SPECIALIST_DOCUMENT_AGENTS = frozenset({
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL_AGENT",
})
VALID_DOCUMENT_AGENTS = SPECIALIST_DOCUMENT_AGENTS | {"GENERAL_CONTEXT"}


@dataclass
class ClassifiedDocument:
    """Extracted document text plus its target agent classification."""

    path: str
    filename: str
    content: str
    agent: DocumentAgentName
    reasoning: str
    confidence: float
    file_hash: str = ""
    source_description: str = ""
    declared_group: str = ""
    extraction_status: str = "success"
    extraction_error: str = ""
    classifier_error_type: str = ""
    classifier_error: str = ""
    document_type: str = ""
    document_group: str = ""
    type_scores: dict[str, float] = field(default_factory=dict)
    agent_relevance: dict[str, str] = field(default_factory=dict)
    loan_program: str = ""
    agent_scores: dict[str, float] = field(default_factory=dict)
    relevant_agents: list[str] = field(default_factory=list)
    is_financial_statement: bool = False
    is_proposal: bool = False
    is_cic_s10a: bool = False
    is_cic_r20: bool = False
    is_sitevisit: bool = False
    is_ledger: bool = False
    financial_statement_extraction: dict[str, Any] | None = None
    financial_statement_extraction_error: str = ""
    financial_statement_extraction_source: str = ""
    proposal_extraction: dict[str, Any] | None = None
    proposal_extraction_error: str = ""
    cic_s10a_extraction: dict[str, Any] | None = None
    cic_s10a_extraction_error: str = ""
    cic_r20_extraction: dict[str, Any] | None = None
    cic_r20_extraction_error: str = ""
    sitevisit_extraction: dict[str, Any] | None = None
    sitevisit_extraction_error: str = ""
    ledger_extraction: dict[str, Any] | None = None
    ledger_extraction_error: str = ""


class UnderwritingGraphState(TypedDict, total=False):
    """State passed between LangGraph workflow nodes."""

    input_paths: list[str]
    files: list[str]
    skipped_outside_box: list[str]
    loan_program: str
    documents: list[ClassifiedDocument]
    document_routes: set[str]
    document_summary: str
    decision: dict[str, Any]
    workflow_mode: WorkflowMode
    gap_analysis: dict[str, Any]
    reference_data: dict[str, Any]
    customer_key: dict[str, Any]
    execution_plan: dict[str, Any]
    steps: list[str]
    output_state: dict[str, Any]


def to_dict_list(items: list[Any]) -> list[dict[str, Any]]:
    """Convert dataclasses to JSON-friendly dictionaries."""

    return [
        asdict(item) if hasattr(item, "__dataclass_fields__") else item
        for item in items
    ]


def extract_text_from_agent_output(output: Any) -> str:
    """Normalize LangChain agent or chain output into plain text."""

    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if hasattr(output, "content"):
        return str(output.content)
    if isinstance(output, dict):
        for key in ["response", "output", "content"]:
            if key in output:
                return extract_text_from_agent_output(output[key])
        messages = output.get("messages")
        if messages:
            return extract_text_from_agent_output(messages[-1])
    return str(output)


def truncate_text(text: str, limit: int) -> str:
    """Truncate long prompt/evidence blocks."""

    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"
