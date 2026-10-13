"""Shared plumbing for the LLM passes that turn a scanned document into JSON."""

from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.prompts import system_prompt as prompt_file
from src.utils.common import normalize_text


TRIEU_VND = 10 ** 6


def resolve_money_multiplier(source_unit: Any) -> int:
    """Turn a unit a form printed into the multiplier that takes it to đồng."""

    tokens = normalize_text(str(source_unit or "")).split()
    if "ty" in tokens:
        return 10 ** 9
    if "trieu" in tokens:
        return 10 ** 6
    if "nghin" in tokens:
        return 10 ** 3
    return 1


def scale_amount(value: Any, multiplier: int) -> Any:
    """Multiply a printed figure into đồng, leaving anything else untouched."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(value * multiplier, 2)


def scale_declared_blocks(
    result: dict[str, Any],
    amount_fields: dict[str, tuple[str, ...]],
) -> dict[str, int]:
    """Scale each block's flat money fields by that block's own source_unit."""

    multipliers: dict[str, int] = {}
    for block_name, fields in amount_fields.items():
        block = result.get(block_name)
        if not isinstance(block, dict):
            continue
        multiplier = resolve_money_multiplier(block.get("source_unit"))
        multipliers[block_name] = multiplier
        for field in fields:
            block[field] = scale_amount(block.get(field), multiplier)
    return multipliers


def build_extraction_chain(system_prompt: str, llm: Any):
    """Wire a system prompt to the LLM. The reply is parsed by run_extraction.

    The JSON parser used to sit on the end of this chain, which threw away the
    reply's metadata — and `finish_reason` is the only thing that knows the
    model ran out of room. JsonOutputParser REPAIRS a JSON string cut mid-object
    rather than refusing it, so a ledger pass that hit the ceiling returned a
    record that looked complete with half its accounts missing. One live run
    came back with 2 accounts out of 15 sheets.
    """

    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            ("human", prompt_file("extraction_user")),
        ]
    )
    return prompt | llm


# What each provider calls "I stopped because I ran out of output tokens".
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "model_length"})


def _finish_reason(reply: BaseMessage) -> str:
    metadata = reply.response_metadata or {}
    for key in ("finish_reason", "stop_reason"):
        value = metadata.get(key)
        if value:
            return str(value).lower()
    return ""


def _parse_reply(reply: Any) -> tuple[dict[str, Any] | None, str]:
    """The reply as a dict, or why it cannot be used.

    A chain that still carries its own parser hands back a dict; that is left
    alone so a caller which built one keeps working.
    """

    if isinstance(reply, dict):
        return reply, ""
    if not isinstance(reply, BaseMessage):
        return None, f"Extraction returned {type(reply).__name__}, not a reply"

    reason = _finish_reason(reply)
    if reason in TRUNCATED_FINISH_REASONS:
        tokens = (reply.usage_metadata or {}).get("output_tokens")
        return None, (
            f"The model ran out of output tokens (finish_reason={reason!r}"
            + (f", {tokens:,} written" if tokens else "")
            + "). The reply is incomplete, and the JSON parser would repair it "
            "into a record that looks whole with sheets missing. Raise this "
            "pass's max_tokens_env (LLM_LEDGER_MAX_TOKENS for the ledger pass; "
            "run testing/probe_max_tokens.py for the model's real ceiling), or "
            "send fewer sheets per call."
        )[:500]
    try:
        return JsonOutputParser().parse(str(reply.content)), ""
    except OutputParserException as exc:
        return None, f"Reply was not JSON: {exc}"[:500]


def _explain(exc: Exception) -> str:
    """The failure as a sentence somebody can act on."""

    text = str(exc)
    if "max_tokens" in text and "at most" in text:
        return (
            "max_tokens exceeds the model's ceiling — the request was refused "
            "before the model ran. Lower LLM_MAX_TOKENS (or the pass's own "
            "max_tokens_env, e.g. LLM_LEDGER_MAX_TOKENS) in .env; run "
            "testing/probe_max_tokens.py to read the model's real ceiling. "
            f"API said: {text}"
        )[:500]
    return f"{type(exc).__name__}: {text}"[:500]


def run_extraction(
    chain: Any,
    filename: str,
    content: str,
    required_keys: set[str],
    missing_llm_message: str,
    normalize: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """Invoke an extraction chain, validate its shape, and normalise the result."""

    if chain is None:
        return None, missing_llm_message
    try:
        reply = chain.invoke({"filename": filename, "content": content})
    except Exception as exc:
        return None, _explain(exc)

    result, problem = _parse_reply(reply)
    if problem:
        return None, problem
    if not isinstance(result, dict):
        return None, f"Extraction returned non-dict result: {type(result).__name__}"
    missing = required_keys - result.keys()
    if missing:
        return None, f"Extraction result missing keys: {sorted(missing)}"
    return (normalize(result) if normalize else result), ""
