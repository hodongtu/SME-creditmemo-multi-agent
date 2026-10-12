"""Every system prompt lives in config/prompts/<name>.md; this is the only reader."""

from __future__ import annotations

import re
from functools import lru_cache

from src.utils.paths import PROJECT_ROOT

PROMPT_DIR = PROJECT_ROOT / "config" / "prompts"
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

PROMPTS: dict[str, frozenset[str]] = {
    "financial_statement_extraction": frozenset(),
    "proposal_extraction": frozenset(),
    "sitevisit_extraction": frozenset(),
    "ledger_extraction": frozenset({"top_row_criteria_table"}),
    "cic_r20_extraction": frozenset(),
    "cic_s10a_extraction": frozenset(),
    "document_classification": frozenset({"document_type_catalogue"}),
    "specialist_rules": frozenset({"intro", "citation_rule", "analysis_guidance",
                                   "output_template", "slot_syntax"}),
    "specialist_citation_rule": frozenset(),
    "specialist_intro_business_activity": frozenset(),
    "specialist_intro_financial_analysis": frozenset(),
    "specialist_intro_credit_relationship": frozenset(),
    "specialist_intro_credit_proposal": frozenset(),
    "extraction_user": frozenset(),
    "document_classification_user": frozenset(),
    "block_relevance_required": frozenset(),
    "block_relevance_optional": frozenset(),
    "block_relevance_general": frozenset(),
    "block_relevance_other_box": frozenset(),
    "block_source_list": frozenset(),
    "block_credit_need": frozenset(),
    "block_proposal": frozenset(),
    "block_cic_s10a": frozenset(),
    "block_cic_r20": frozenset(),
    "block_sitevisit": frozenset(),
    "block_ledger": frozenset({"residual_label"}),
    "user_mixed_unit_warning": frozenset(),
    "user_reporting_periods": frozenset({"periods"}),
    "user_structured_pointer": frozenset({"heading"}),
    "metrics_intro": frozenset(),
    "metrics_data_quality": frozenset(),
    "metrics_source_files": frozenset(),
}


class PromptError(RuntimeError):
    """A prompt file is missing, or its placeholders disagree with the code."""


@lru_cache(maxsize=16)
def prompt_text(name: str) -> str:
    if name not in PROMPTS:
        raise PromptError(f"prompt '{name}' is not declared in src/prompts.py")
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"no prompt file at config/prompts/{name}.md")
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    found = set(_PLACEHOLDER.findall(text))
    if found != PROMPTS[name]:
        raise PromptError(f"config/prompts/{name}.md has placeholders {sorted(found)}, "
                          f"the code fills {sorted(PROMPTS[name])}")
    return text.strip() + "\n"


def system_prompt(name: str, **values: str) -> str:
    """The prompt with every {{placeholder}} filled; other braces are left as written."""

    missing = PROMPTS.get(name, frozenset()) - set(values)
    if missing or set(values) - PROMPTS.get(name, frozenset()):
        raise PromptError(f"prompt '{name}' needs exactly {sorted(PROMPTS.get(name, ()))}, got {sorted(values)}")
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), prompt_text(name))
