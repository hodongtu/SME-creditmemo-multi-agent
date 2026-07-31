"""Flow diagram helpers for the underwriting chatbot."""

from pathlib import Path

from langchain_core.runnables.graph_mermaid import draw_mermaid_png


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "src" / "assets"


def get_flow_mermaid() -> str:
    """
    Return a Mermaid diagram for the current SME underwriting chatbot flow.

    This reflects FastAPI, SQLite state, Chroma knowledge-base admin APIs, and
    supervisor/sub-agent orchestration.
    """
    mermaid_code = """
    flowchart TD
        user["User / Browser UI"]
        input{"User action"}

        chat["POST /chat/stream<br/>SSE-compatible text stream"]
        upload["POST /upload/stream<br/>multipart files + SSE-compatible stream"]
        validate["POST /validate"]
        clear["POST /clear-session"]
        kb_ui["GET /knowledge-ui<br/>Chroma dashboard"]

        session["Get or create session_id cookie"]
        sqlite_history[("SQLite<br/>conversation_messages")]
        memory_seed["Seed SQLite history<br/>from frontend fallback if empty"]
        memory_read["Read recent conversation history<br/>from SQLite"]
        clear_memory["Clear session history<br/>in SQLite"]

        validate_files["Validate extension and file size"]
        save_files["Save uploaded files temporarily<br/>uploads/backend"]
        file_hash["Compute file hash per file"]

        supervisor["SupervisorAgent.process"]
        normalize["Normalize query and uploaded file metadata"]
        history_context["Build compact history context"]
        input_guardrails{"Input guardrails pass?"}
        input_block["Return INPUT_GUARDRAILS response"]

        has_files{"Uploaded files?"}
        doc_cache{"Document extraction cache hit?"}
        file_doc_cache[("File cache<br/>.cache/document_cache")]
        extract_docs["Extract document text<br/>PDF OCR / Excel / CSV"]
        classify_docs["Classify each document<br/>rules then LLM fallback"]
        save_doc_cache["Save extraction + classification<br/>to document cache"]
        doc_summary["Build document classification summary"]

        decide["Supervisor route decision<br/>LLM + rule override"]
        route_choice{"Selected route"}

        gap_analysis["Self-Ask evidence gap analysis<br/>inventory + missing evidence + can_proceed"]
        execution_plan["Plan-and-Execute plan<br/>route, agents, required evidence, tool plan"]
        controlled_tools{"Controlled ReAct tool plan?"}
        web_search["WEB_SEARCH_AGENT<br/>Tavily include_domains only"]
        web_context["Summarize web context<br/>registration, tax code, industry, sources"]
        tool_observations["Tool observations metadata<br/>status, summary, limitations"]
        rag_skipped["RAG retrieval<br/>available in Chroma admin, not attached to workflow yet"]

        response_cache{"Response cache hit?"}
        sqlite_response_cache[("SQLite<br/>response_cache")]
        cache_response["Return cached response"]

        chroma_query["KnowledgeBaseService.retrieve<br/>OpenAI query embedding + filters"]
        chroma_db[("Chroma<br/>chunks, embeddings, metadata, retrieval logs")]

        build_input["Build agent input<br/>history + web context + selected docs"]
        financial_ratio_calc["Pre-compute financial ratios<br/>custom formulas from financial-analysis-template.md"]
        conversation["CONVERSATION_AGENT"]
        financial["FINANCIAL_ANALYSIS_AGENT"]
        business["BUSINESS_ACTIVITY_AGENT"]

        risk_workflow["Risk assessment workflow"]
        risk_financial["Run FINANCIAL_ANALYSIS_AGENT"]
        risk_business["Run BUSINESS_ACTIVITY_AGENT"]
        risk_assessment["Run RISK_ASSESSMENT_AGENT<br/>with financial + business outputs"]

        output_guardrails["Output guardrails<br/>sanitize / validate response"]
        hallucination_judge["Evidence-grounded hallucination judge<br/>extract claims + verify against docs, web context, history"]
        reflection_policy["Reflection policy<br/>PASS / WARN / REQUIRE_VALIDATION / BLOCK"]
        reflection_action{"Requires validation or block?"}
        human_validation_flag["Set needs_human_validation<br/>for high-risk output"]
        save_response_cache["Save response cache to SQLite<br/>skip conversation and insufficient-info responses"]
        build_ui_response["Build UI response<br/>agent tags, steps, plan, gaps, tools, reflection"]
        stream_events["Stream events to browser<br/>step + pseudo-token markdown chunks + final metadata"]
        memory_append["Append user + assistant messages<br/>and metadata_json to SQLite history"]
        cleanup_files["Delete temporary uploaded files"]
        response["Render streamed assistant bubble"]

        kb_status["GET /knowledge/status"]
        kb_ingest["POST /knowledge/ingest"]
        kb_query["POST /knowledge/query"]
        kb_file["Local knowledge file<br/>data/knowledge/raw"]
        kb_extract["Extract text<br/>OCR / Excel / CSV"]
        kb_chunk["Chunk text + metadata"]
        kb_embed["OpenAI document embeddings"]

        user --> input
        input -->|Text message| chat
        input -->|Document upload| upload
        input -->|Human validation feedback| validate
        input -->|Clear conversation| clear
        input -->|Knowledge admin| kb_ui

        chat --> session
        chat --> memory_seed
        upload --> session
        upload --> validate_files --> save_files --> file_hash
        validate --> session
        clear --> session --> clear_memory --> sqlite_history
        clear_memory --> response

        session --> memory_read
        memory_seed --> sqlite_history
        sqlite_history --> memory_read
        file_hash --> memory_read
        memory_read --> supervisor

        supervisor --> normalize --> history_context --> input_guardrails
        input_guardrails -->|No| input_block --> build_ui_response
        input_guardrails -->|Yes| has_files

        has_files -->|No| doc_summary
        has_files -->|Yes| doc_cache
        doc_cache -->|Read| file_doc_cache
        doc_cache -->|Hit| doc_summary
        doc_cache -->|Miss| extract_docs --> classify_docs --> save_doc_cache --> file_doc_cache
        save_doc_cache --> doc_summary

        doc_summary --> decide --> gap_analysis --> execution_plan --> controlled_tools
        controlled_tools -->|Web search planned| web_search --> web_context --> tool_observations
        controlled_tools -->|RAG planned| rag_skipped --> tool_observations
        controlled_tools -->|No tool| tool_observations
        tool_observations --> response_cache

        response_cache -->|Read| sqlite_response_cache
        response_cache -->|Hit| cache_response --> build_ui_response
        response_cache -->|Miss| route_choice

        route_choice -->|General chat| conversation
        route_choice -->|Financial analysis| build_input --> financial_ratio_calc --> financial
        route_choice -->|Business activity| build_input --> business
        route_choice -->|Credit memo / risk assessment| risk_workflow

        web_context --> build_input
        gap_analysis --> build_input
        history_context --> build_input
        risk_workflow --> financial_ratio_calc --> risk_financial --> risk_business --> risk_assessment

        conversation --> output_guardrails
        financial --> output_guardrails
        business --> output_guardrails
        risk_assessment --> output_guardrails

        output_guardrails --> hallucination_judge --> reflection_policy --> reflection_action
        reflection_action -->|Yes| human_validation_flag --> save_response_cache
        reflection_action -->|No| save_response_cache
        save_response_cache --> sqlite_response_cache
        save_response_cache --> build_ui_response --> stream_events
        stream_events --> memory_append --> sqlite_history
        memory_append --> cleanup_files --> response

        kb_ui --> kb_status --> chroma_db
        kb_ui --> kb_ingest --> kb_file --> kb_extract --> kb_chunk --> kb_embed --> chroma_db
        kb_ui --> kb_query --> chroma_query --> chroma_db
    """.strip()
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        draw_mermaid_png(mermaid_code, output_file_path=str(ASSETS_DIR / "UW_graph.png"))
    except Exception as exc:
        print(f"Unable to render Mermaid PNG: {exc}")
    return mermaid_code
