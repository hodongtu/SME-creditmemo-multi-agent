"""Shared types and small helpers for the underwriting workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypedDict


AgentName = Literal[
    "CONVERSATION_AGENT",
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL",
    "RISK_ASSESSMENT_AGENT",
    "CREDIT_MEMO",
]
WorkflowMode = Literal[
    "conversation",
    "single_business_activity",
    "single_credit_relationship",
    "single_financial_analysis",
    "single_risk_assessment",
    "single_credit_proposal",
    "full_credit_memo",
]
DocumentAgentName = Literal[
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL",
    "RISK_ASSESSMENT_AGENT",
    "GENERAL_CONTEXT",
]


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
    extraction_status: str = "success"
    extraction_error: str = ""
    classifier_error_type: str = ""
    classifier_error: str = ""
    agent_scores: dict[str, float] = field(default_factory=dict)


class UnderwritingGraphState(TypedDict, total=False):
    """State passed between LangGraph workflow nodes."""

    query: str
    input_paths: list[str]
    conversation_history: list[dict[str, str]]
    history_context: str
    files: list[str]
    documents: list[ClassifiedDocument]
    document_routes: set[str]
    document_summary: str
    decision: dict[str, Any]
    workflow_mode: WorkflowMode
    gap_analysis: dict[str, Any]
    execution_plan: dict[str, Any]
    web_context: str
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
