"""LLM client factory + runtime Config (extracted from the notebook).

Import-safe: constructing the LLM clients (config = Config(...)) stays in the
notebook driver cell so importing this module never needs live credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from langchain_openai import ChatOpenAI


def build_llm(model_env: str,
              temperature: float = 0.1,
              timeout_env: str = "LLM_TIMEOUT_SECONDS"):
    """Build one ChatOpenAI client from environment variables."""

    model = os.getenv(model_env, "")

    kwargs = {
        "model": model,
        "base_url": os.getenv("OPENAI_API_BASE"),
        "temperature": temperature,
        "timeout": float(os.getenv(timeout_env, "60")),
        "max_retries": int(os.getenv("LLM_CLIENT_MAX_RETRIES", "0")),
    }
    return ChatOpenAI(**kwargs)

@dataclass
class Config:
    """Runtime config for the local notebook workflow."""

    decision_llm: Any = None
    document_llm: Any = None
    conversation_llm: Any = None
    analysis_llm: Any = None
    credit_memo_llm: Any = None
    hallucination_llm: Any = None
    bctc_extraction_llm: Any = None
    max_files: int = 50
    max_chars_per_document: int = 120_000
    max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_CONCURRENCY", "3"))
    )
    document_classifier_rule_confidence_threshold: float = 0.65
    enable_plan_and_execute: bool = True
    enable_self_ask_gap_analysis: bool = True
    enable_hallucination_guardrail: bool = os.getenv("RUN_HALLUCINATION_CHECK", False)
    enable_safety_guardrails: bool = os.getenv("RUN_SAFETY_GUARDRAILS", False)
    enable_web_search: bool = os.getenv("RUN_WEB_SEARCH", False)
    agent_input_char_budgets: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.agent_input_char_budgets is None:
            self.agent_input_char_budgets = {
                "CONVERSATION_AGENT": 8_000,
                "FINANCIAL_ANALYSIS_AGENT": 60_000,
                "BUSINESS_ACTIVITY_AGENT": 20_000,
                "CREDIT_RELATIONSHIP_AGENT": 20_000,
                "RISK_ASSESSMENT_AGENT": 16_000,
                "CREDIT_PROPOSAL": 8_000,
                "CREDIT_MEMO": 80_000,
                "GENERAL_CONTEXT": 12_000,
            }

