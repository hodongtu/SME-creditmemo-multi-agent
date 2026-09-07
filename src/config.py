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
    max_tokens_env: str = "LLM_MAX_TOKENS",
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
        "max_retries": int(os.getenv("LLM_CLIENT_MAX_RETRIES", "1")),
        # Left unset, the gateway picks its own ceiling — and a small one is
        # invisible: JsonOutputParser repairs the truncated JSON rather than
        # raising, so a reply cut off mid-array arrives looking complete. One
        # live run returned 2 accounts out of 15 sheets that way.
        #
        # Per pass, like the timeout, because one shared number has to fit the
        # weakest model in the fleet: gpt-4o-mini caps completions at 16,384
        # while gpt-5.4-mini allows 128,000, and the ledger pass — the one that
        # actually runs out — is on the second. Measured, not read off a page:
        # testing/probe_max_tokens.py asks the API and it answers in the 400.
        "max_tokens": int(os.getenv(max_tokens_env, "8192")),
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
    # How much of a document's OCR text survives into result["document_
    # classifications"]. The pipeline keeps the whole text in memory and feeds
    # the whole text to the prompts; this bounds only what leaves the process,
    # because the caller exports that payload and a 22-file dossier put it past
    # the size limit on the other side. Head truncation keeps the cover page and
    # loses the middle, which is where the financial tables are — when OCR is
    # what needs investigating, set OCR_CACHE_DIR and read the cached text.
    result_content_char_limit: int = 2_000
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

