"""Loader for the document routing matrix (src/agents/documents/document_matrix.yaml)."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.types import SPECIALIST_DOCUMENT_AGENTS


MATRIX_PATH = Path(__file__).resolve().parent / "document_matrix.yaml"

RELEVANCE_LEVELS = ("R", "O")
DEFAULT_LOAN_PROGRAM = "PLO"

PRIMARY_AGENT_PRECEDENCE = (
    "CREDIT_PROPOSAL_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "FINANCIAL_ANALYSIS_AGENT",
    "BUSINESS_ACTIVITY_AGENT",
)

_LEVEL_RANK = {"O": 1, "R": 2}

class DocumentMatrixError(RuntimeError):
    """Raised when the matrix file is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class DocumentType:
    """One row of the matrix: a kind of document and who consumes it."""

    id: str
    stt: str
    label: str
    short_label: str
    group_id: str
    group_stt: str
    group_label: str
    keywords: tuple[str, ...]
    agents: dict[str, dict[str, str]]
    primary_agent: str
    requirement: dict[str, str] = field(default_factory=dict)
    financial_statement_extraction: bool = False
    proposal_extraction: bool = False
    cic_s10a_extraction: bool = False
    cic_r20_extraction: bool = False
    sitevisit_extraction: bool = False
    ledger_extraction: bool = False

    @property
    def routing_signature(self) -> frozenset[str]:
        """The set of agents this type feeds, ignoring R/O and loan program."""
        return frozenset(self.agents)


@dataclass(frozen=True)
class DocumentMatrix:
    version: int
    loan_programs: tuple[str, ...]
    loan_program_labels: dict[str, str]
    types: dict[str, DocumentType]


def _fail(message: str) -> None:
    raise DocumentMatrixError(f"{MATRIX_PATH.name}: {message}")


def _need(value: Any, kind: type, message: str) -> Any:
    """Return `value`, or refuse it for being absent, wrong-typed, or empty."""

    if not isinstance(value, kind) or not value:
        _fail(message)
    return value


def _parse_agents(
    raw: Any,
    programs: tuple[str, ...],
    where: str,
) -> dict[str, dict[str, str]]:
    """Expand the `agents` block into {agent: {program: level}}.

    Accepts a scalar level (same across every loan program) or an explicit
    per-program map. A partial map is rejected rather than back-filled: the
    whole point of splitting the four programs is that a future divergence must
    be stated, not guessed.

    An EMPTY mapping is allowed and means "no specialist consumes this type".
    That is not the same as deleting the type: the document is still recognised,
    still named correctly in the source list, and still counts toward the upload
    box it was filed in — it simply is not fed to anyone. Collateral documents
    sit here now that risk assessment is gone.
    """

    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        _fail(f"{where}: 'agents' must be a mapping")

    agents: dict[str, dict[str, str]] = {}
    for agent, value in raw.items():
        if agent not in SPECIALIST_DOCUMENT_AGENTS:
            _fail(
                f"{where}: unknown agent {agent!r}; expected one of "
                f"{sorted(SPECIALIST_DOCUMENT_AGENTS)}"
            )
        if isinstance(value, str):
            level = value.strip().upper()
            if level not in RELEVANCE_LEVELS:
                _fail(f"{where}/{agent}: level {value!r} must be R or O")
            agents[agent] = {program: level for program in programs}
            continue
        if isinstance(value, dict):
            missing = [p for p in programs if p not in value]
            extra = [p for p in value if p not in programs]
            if missing or extra:
                _fail(
                    f"{where}/{agent}: per-program map must list exactly "
                    f"{list(programs)}"
                    + (f"; missing {missing}" if missing else "")
                    + (f"; unknown {extra}" if extra else "")
                )
            levels = {}
            for program in programs:
                level = str(value[program]).strip().upper()
                if level not in RELEVANCE_LEVELS:
                    _fail(
                        f"{where}/{agent}/{program}: level "
                        f"{value[program]!r} must be R or O"
                    )
                levels[program] = level
            agents[agent] = levels
            continue
        _fail(f"{where}/{agent}: expected 'R'/'O' or a per-program mapping")
    return agents


def _resolve_primary_agent(
    declared: str | None,
    agents: dict[str, dict[str, str]],
    where: str,
) -> str:
    """Pick the agent this document type is displayed as belonging to."""

    if declared:
        if declared not in SPECIALIST_DOCUMENT_AGENTS:
            _fail(f"{where}: unknown primary_agent {declared!r}")
        if declared in agents:
            return declared

    def _pick(level: str) -> str | None:
        for agent in PRIMARY_AGENT_PRECEDENCE:
            levels = agents.get(agent)
            if levels and level in levels.values():
                return agent
        return None

    # "" when nothing consumes the type. The caller turns that into
    # GENERAL_CONTEXT, which is where an unconsumed document belongs.
    return _pick("R") or _pick("O") or (sorted(agents)[0] if agents else "")


def _load(path: Path) -> DocumentMatrix:
    if not path.exists():
        _fail(f"matrix file not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DocumentMatrixError(f"{path.name}: invalid YAML: {exc}") from exc
    _need(raw, dict, "top level must be a mapping")

    version = raw.get("version")
    if not isinstance(version, int):
        _fail("'version' must be an integer")

    raw_programs = _need(raw.get("loan_programs"), list,
                         "'loan_programs' must be a non-empty list")
    programs: list[str] = []
    program_labels: dict[str, str] = {}
    for entry in raw_programs:
        _need(entry, dict, "each loan_programs entry must be a mapping")
        _need(entry.get("id"), str, "each loan_programs entry needs an 'id'")
        program_id = str(entry["id"])
        if program_id in program_labels:
            _fail(f"duplicate loan program id {program_id!r}")
        programs.append(program_id)
        program_labels[program_id] = str(entry.get("label") or program_id)

    if DEFAULT_LOAN_PROGRAM not in programs:
        # A constant naming a programme the matrix no longer declares would
        # route every case against a column that does not exist.
        _fail(
            f"DEFAULT_LOAN_PROGRAM {DEFAULT_LOAN_PROGRAM!r} is not one of "
            f"{programs}"
        )

    raw_groups = _need(raw.get("groups"), list,
                       "'groups' must be a non-empty list")

    types: dict[str, DocumentType] = {}
    group_ids: set[str] = set()
    for group in raw_groups:
        _need(group, dict, "each group must be a mapping")
        group_id = str(_need(group.get("id"), str, "each group needs an 'id'"))
        if group_id in group_ids:
            _fail(f"duplicate group id {group_id!r}")
        group_ids.add(group_id)

        raw_types = _need(group.get("types"), list,
                          f"group {group_id!r}: 'types' must be a non-empty list")

        for entry in raw_types:
            _need(entry, dict, f"group {group_id!r}: each type must be a mapping")
            type_id = str(_need(entry.get("id"), str,
                                f"group {group_id!r}: a type is missing 'id'"))
            if type_id in types:
                _fail(f"duplicate document type id {type_id!r}")
            where = f"type {type_id!r}"

            label = str(_need(entry.get("label"), str,
                              f"{where}: 'label' is required")).strip()
            short_label = str(entry.get("short_label") or "").strip() or label

            raw_keywords = _need(entry.get("keywords"), list,
                                 f"{where}: 'keywords' must be a non-empty list")
            keywords: list[str] = []
            for keyword in raw_keywords:
                text = str(keyword).strip()
                if not text:
                    _fail(f"{where}: empty keyword")
                if text in keywords:
                    _fail(f"{where}: duplicate keyword {text!r}")
                keywords.append(text)

            agents = _parse_agents(entry.get("agents"), tuple(programs), where)
            requirement = entry.get("requirement") or {}
            if not isinstance(requirement, dict):
                _fail(f"{where}: 'requirement' must be a mapping")

            types[type_id] = DocumentType(
                id=type_id,
                stt=str(entry.get("stt") or ""),
                label=label,
                short_label=short_label,
                group_id=group_id,
                group_stt=str(group.get("stt") or ""),
                group_label=str(group.get("label") or group_id),
                keywords=tuple(keywords),
                agents=agents,
                primary_agent=_resolve_primary_agent(
                    entry.get("primary_agent") or group.get("primary_agent"),
                    agents,
                    where,
                ),
                requirement={
                    str(key): str(value or "")
                    for key, value in requirement.items()
                },
                financial_statement_extraction=bool(entry.get("financial_statement_extraction", False)),
                proposal_extraction=bool(
                    entry.get("proposal_extraction", False)
                ),
                cic_s10a_extraction=bool(
                    entry.get("cic_s10a_extraction", False)
                ),
                cic_r20_extraction=bool(
                    entry.get("cic_r20_extraction", False)
                ),
                sitevisit_extraction=bool(
                    entry.get("sitevisit_extraction", False)
                ),
                ledger_extraction=bool(
                    entry.get("ledger_extraction", False)
                ),
            )

    if not types:
        _fail("no document types defined")
    return DocumentMatrix(
        version=version,
        loan_programs=tuple(programs),
        loan_program_labels=program_labels,
        types=types,
    )


@lru_cache(maxsize=1)
def load_matrix() -> DocumentMatrix:
    """Load and validate the matrix once per process."""

    return _load(MATRIX_PATH)


def get_type(type_id: str) -> DocumentType | None:
    return load_matrix().types.get(type_id)


def document_type_keywords() -> dict[str, tuple[str, ...]]:
    """{document_type_id: keywords} — the input to rule-based classification."""

    return {type_id: doc.keywords for type_id, doc in load_matrix().types.items()}


def resolve_loan_program(loan_program: str = "") -> str:
    """The programme whose column of the matrix a case is read against."""

    if not loan_program:
        return DEFAULT_LOAN_PROGRAM
    if loan_program not in load_matrix().loan_programs:
        raise DocumentMatrixError(
            f"unknown loan program {loan_program!r}; expected one of "
            f"{list(load_matrix().loan_programs)}"
        )
    return loan_program


def agent_relevance_for_type(
    type_id: str,
    loan_program: str | None = None,
) -> dict[str, str]:
    """Return {agent: "R"|"O"} for a document type."""

    doc = get_type(type_id)
    if doc is None:
        return {}
    if loan_program is not None:
        if loan_program not in load_matrix().loan_programs:
            raise DocumentMatrixError(
                f"unknown loan program {loan_program!r}; expected one of "
                f"{list(load_matrix().loan_programs)}"
            )
        return {
            agent: levels[loan_program] for agent, levels in doc.agents.items()
        }
    return {
        agent: max(levels.values(), key=lambda level: _LEVEL_RANK[level])
        for agent, levels in doc.agents.items()
    }


def primary_agent_for_type(type_id: str) -> str | None:
    doc = get_type(type_id)
    return doc.primary_agent if doc else None


def is_financial_statement_type(type_id: str) -> bool:
    """True when this document type should go through structured BCTC extraction."""

    doc = get_type(type_id)
    return bool(doc and doc.financial_statement_extraction)


def is_proposal_type(type_id: str) -> bool:
    """True when this document type is a credit application to be extracted."""

    doc = get_type(type_id)
    return bool(doc and doc.proposal_extraction)


def is_cic_s10a_type(type_id: str) -> bool:
    """True when this document type is a CIC S10A credit-relationship report."""

    doc = get_type(type_id)
    return bool(doc and doc.cic_s10a_extraction)


def is_cic_r20_type(type_id: str) -> bool:
    """True when this document type is a CIC R20 collateral report."""

    doc = get_type(type_id)
    return bool(doc and doc.cic_r20_extraction)


def is_sitevisit_type(type_id: str) -> bool:
    """True when this document type is a site-visit report to be extracted."""

    doc = get_type(type_id)
    return bool(doc and doc.sitevisit_extraction)


def is_ledger_type(type_id: str) -> bool:
    """True when this document type is a detail-ledger spreadsheet."""

    doc = get_type(type_id)
    return bool(doc and doc.ledger_extraction)


def describe_types_for_prompt(group_id: str = "") -> str:
    """Render the type catalogue for the classifier LLM prompt.

    Built from the matrix so the prompt can never list a type the matrix does
    not define (or miss one it does).
    """

    lines: list[str] = []
    current_group: str | None = None
    for doc in load_matrix().types.values():
        if group_id and doc.group_id != group_id:
            continue
        if doc.group_id != current_group:
            current_group = doc.group_id
            lines.append(f"\n{doc.group_stt}. {doc.group_label}")
        label = " ".join(doc.label.split())
        if len(label) > 180:
            label = label[:180].rstrip() + "..."
        lines.append(f'- "{doc.id}" ({doc.stt}): {label}')
    return "\n".join(lines).strip()
