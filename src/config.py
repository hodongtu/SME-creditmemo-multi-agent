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
    # An unset variable used to become model="", which the API answers with
    # "you must provide a model parameter" — a 400 raised mid-run, per document,
    # naming nothing. Say which variable is missing, before any call is made.
    if not model:
        raise ValueError(
            f"{model_env} is not set; add it to .env or point this pass at a "
            f"variable that is"
        )

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
    ledger_extraction_llm: Any = None
    # Runs one reference-data query: (sql, params) -> list of row dicts. Left
    # unset the pipeline queries nothing and says so; connecting, pooling and
    # permissions belong to whoever supplies this.
    query_executor: Any = None
    max_files: int = 50
    max_chars_per_document: int = 120_000
    # A ceiling on what ONE run may spend on extraction. The analysis prompt has
    # always been capped (agent_input_char_budgets); extraction was capped per
    # document only, so total spend scaled with file count with no limit — a
    # 50-file dossier is 50 extraction calls and ~2M input tokens. Reached, the
    # run stops starting new extractions and says which documents it skipped,
    # rather than quietly billing for a dossier nobody meant to submit.
    max_extraction_calls: int = 30
    max_extraction_input_chars: int = 2_000_000
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

