"""Specialist agents + credit-memo composer (extracted from the notebook)."""

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.agents.documents.document_matrix import get_type
from src.prompts import system_prompt
from src.tools import cic, t24
from src.utils.paths import PROJECT_ROOT
from src.types import truncate_text


class SpecialistAgent:
    """Base wrapper for specialist direct chains or tool agents."""

    name = "specialist_agent"
    agent_id = ""
    structure_relative_path = ""
    guidance_relative_path = ""
    prompt_name = ""
    query_tools: list = []
    require_citations = True

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
        """Separate Agent-Skills style YAML frontmatter from the body."""

        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text  

        metadata: dict[str, str] = {}
        key = ""
        for line in parts[1].splitlines():
            if not line.strip():
                continue
            if not line.startswith((" ", "\t")) and ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                metadata[key] = "" if value in {">-", ">", "|", "|-"} else value
            elif key:
                metadata[key] = f"{metadata[key]} {line.strip()}".strip()
        return metadata, parts[2].lstrip("\n")

    @classmethod
    def _guidance_metadata(cls) -> dict[str, str]:
        """Frontmatter of this agent's guidance file (name/description)."""

        if not cls.guidance_relative_path:
            return {}
        path = PROJECT_ROOT / cls.guidance_relative_path
        if not path.is_file():
            return {}
        metadata, _ = cls._split_frontmatter(path.read_text(encoding="utf-8"))
        return metadata

    @classmethod
    def _read_template(cls, relative_path: str) -> str:
        """Read a template file, dropping any frontmatter."""

        if not relative_path:
            return ""
        path = PROJECT_ROOT / relative_path
        if not path.is_file():
            return ""
        _, body = cls._split_frontmatter(path.read_text(encoding="utf-8"))
        return body

    def __init__(self, llm: Any, tools: list[Any] | None = None):
        self.llm = llm
        self.tools = tools or []
        self.output_template = self._read_template(self.structure_relative_path)
        self.analysis_guidance = self._read_template(self.guidance_relative_path)
        self.guidance_metadata = self._guidance_metadata()

        self.system_prompt = system_prompt(
            "specialist_rules",
            intro=system_prompt(f"specialist_intro_{self.prompt_name}").strip(),
            citation_rule=(system_prompt("specialist_citation_rule")
                           if self.require_citations else ""),
            analysis_guidance=self.analysis_guidance,
            output_template=self.output_template,
            slot_syntax="{{TenTruong}}",
        )
        self.agent = None
        self.chain = None
        self.agent_error = ""
        
        if self.llm and self.tools:
            try:
                self.agent = create_agent(
                    model=self.llm,
                    tools=self.tools,
                    system_prompt=self.system_prompt,
                    name=self.name,
                )
            except Exception as exc:
                self.agent_error = f"{type(exc).__name__}: {exc}"
                print(f"{self.name} create_agent failed: {self.agent_error}")
        if self.llm and self.agent is None:
            self.chain = self._build_direct_chain()

    def analyze(self, user_input: str) -> Any:
        """Run the specialist analysis."""

        if self.agent:
            return self.agent.invoke(
                {"messages": [HumanMessage(content=user_input)]}
            )
        if self.chain:
            return self.chain.invoke({"user_input": user_input})
        return AIMessage(content=self._fallback_response(user_input))

    def _build_direct_chain(self):
        """Build a single-call chain for specialists without tools."""

        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=self.system_prompt.replace("{{", "{").replace("}}", "}")),
                ("human", "{user_input}"),
            ]
        )
        return prompt | self.llm | StrOutputParser()

    def _fallback_response(self, user_input: str) -> str:
        """Return deterministic preview when no analysis LLM is configured."""

        return (
            f"# {self.name}\n\n"
            "No analysis LLM is configured, so this is an evidence preview "
            "rather than a report.\n\n"
            "```text\n"
            f"{truncate_text(user_input, 3_000)}\n"
            "```"
        )


class BusinessActivityAnalysis(SpecialistAgent):
    """Business activity analysis specialist."""

    name = "business_activity_agent"
    agent_id = "BUSINESS_ACTIVITY_AGENT"
    structure_relative_path = "src/templates/business-activity-structure.md"
    guidance_relative_path = "src/templates/business-activity-guidance.md"
    prompt_name = "business_activity"


class FinancialAnalysis(SpecialistAgent):
    """Financial analysis specialist."""

    name = "financial_analysis_agent"
    agent_id = "FINANCIAL_ANALYSIS_AGENT"
    require_citations = False
    structure_relative_path = "src/templates/financial-analysis-structure.md"
    guidance_relative_path = "src/templates/financial-analysis-guidance.md"
    prompt_name = "financial_analysis"


class CreditRelationshipAnalysis(SpecialistAgent):
    """Credit relationship specialist using T24 and CIC database tools."""

    name = "credit_relationship_agent"
    agent_id = "CREDIT_RELATIONSHIP_AGENT"
    # Section 1 of the report is this bank's own relationship; section 2 is
    # every other institution, from the bureau when no CIC file was uploaded.
    query_tools = [
        t24.get_internal_facilities,
        t24.get_internal_credit_quality,
        cic.get_bureau_credit_report,
    ]
    structure_relative_path = "src/templates/credit-relationship-structure.md"
    guidance_relative_path = "src/templates/credit-relationship-guidance.md"
    prompt_name = "credit_relationship"


class CreditProposalAnalysis(SpecialistAgent):
    """Credit proposal specialist."""

    name = "credit_proposal_agent"
    agent_id = "CREDIT_PROPOSAL_AGENT"
    # Only the existing-limit column: what this bank has already granted. The
    # repayment history behind section 1.2 is the relationship agent's business,
    # and splitting the queries per tool is what lets this one ask for less.
    query_tools = [t24.get_internal_facilities]
    structure_relative_path = "src/templates/credit-proposal-structure.md"
    guidance_relative_path = "src/templates/credit-proposal-guidance.md"
    prompt_name = "credit_proposal"


SPECIALIST_BY_AGENT: dict[str, type[SpecialistAgent]] = {
    cls.agent_id: cls
    for cls in (
        BusinessActivityAnalysis,
        FinancialAnalysis,
        CreditRelationshipAnalysis,
        CreditProposalAnalysis,
    )
}


if len(SPECIALIST_BY_AGENT) != 4:
    raise ValueError(
        f"SPECIALIST_BY_AGENT holds {len(SPECIALIST_BY_AGENT)} entries for 4 "
        f"classes — two classes declare the same agent_id"
    )
for _agent_id, _cls in SPECIALIST_BY_AGENT.items():
    for _query_tool in _cls.query_tools:
        _extras = getattr(_query_tool, "extras", None) or {}
        if not _extras.get("heading"):
            raise ValueError(
                f"{_agent_id} / {_query_tool.name!r}: extras['heading'] is missing"
            )
        for _type_id in _extras.get("superseded_by", ()):
            if get_type(_type_id) is None:
                raise ValueError(
                    f"{_agent_id} / {_query_tool.name!r}: superseded_by "
                    f"{_type_id!r} is not a document_type in the matrix"
                )
        _visible = list(_query_tool.tool_call_schema.model_fields)
        if _visible:
            raise ValueError(
                f"{_agent_id} / {_query_tool.name!r}: argument {_visible} is not "
                f"an InjectedToolArg — a model can see it and fill it in"
            )
