"""LLM client factory + runtime Config (extracted from the notebook)."""

import os
from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI


def env_flag(name: str, default: str = "false") -> bool:
    """Read a boolean env var."""
    keys = {"1", "true", "yes", "y", "on"}
    return os.getenv(name, default).strip().lower() in keys


def build_llm(
    model_env: str,
    temperature: float = 0.1,
    timeout_env: str = "LLM_TIMEOUT_SECONDS",
):
    """Build one ChatOpenAI client from environment variables."""

    model = os.getenv(model_env, "")

    kwargs = {
        "model": model,
        "base_url": os.getenv("OPENAI_API_BASE"),
        "temperature": temperature,
        "timeout": float(os.getenv(timeout_env, "60")),
        "max_retries": int(os.getenv("LLM_CLIENT_MAX_RETRIES", "3")),
    }
    return ChatOpenAI(**kwargs)


@dataclass
class Config:
    """Runtime config for the local notebook workflow."""

    document_llm: Any = None
    analysis_llm: Any = None
    financial_statement_extraction_llm: Any = None
    proposal_extraction_llm: Any = None
    cic_s10a_extraction_llm: Any = None
    cic_r21_extraction_llm: Any = None
    sitevisit_extraction_llm: Any = None
    max_files: int = 50
    max_chars_per_document: int = 120_000
    document_classifier_rule_confidence_threshold: float = 0.65
    document_classifier_grouped_confidence_threshold: float = 0.65
    enable_plan_and_execute: bool = True
    enable_self_ask_gap_analysis: bool = True
    agent_input_char_budgets: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.agent_input_char_budgets is None:
            self.agent_input_char_budgets = {
                "FINANCIAL_ANALYSIS_AGENT": 120_000,
                "BUSINESS_ACTIVITY_AGENT": 120_000,
                "CREDIT_RELATIONSHIP_AGENT": 120_000,
                "CREDIT_PROPOSAL_AGENT": 120_000,
                "GENERAL_CONTEXT": 12_000,
            }

