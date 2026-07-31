# Architecture — canonical vs legacy

This project has **two independent implementations**. Read this before changing code so you
edit the right one.

## ✅ Canonical: the notebook (active development)

`notebooks/local_underwriting_agents.ipynb` is the **single source of truth** for the AI-agent
underwriting workflow. All new work happens here. It orchestrates the full pipeline (OCR →
document classification → specialist agents → credit memo → hallucination judge) inline.

It depends on a small set of **shared, importable modules** — edit these, not their legacy twins:

| Module | Role |
|---|---|
| `src/utils/ocr.py` | PDF → text via pypdfium2 + Tesseract (preprocessing, layout, cache) |
| `src/utils/extractors.py` | PDF/CSV/XLSX text extraction (wraps `ocr.py`) |
| `src/utils/formatting.py` | Monetary formatting (đồng → tỷ VNĐ) |
| `src/utils/common.py` | Notebook helpers (`show_graph`, …) |
| `src/agents/financial_analysis/financial_ratio_calculator.py` | Deterministic ratio pre-computation |
| `src/agents/*/*-template.md` | Markdown output templates read by the notebook's specialist agents |

## 🧊 Legacy: the FastAPI app (frozen — do not develop)

An earlier FastAPI product implements the **same** orchestration independently. It is **kept for
reference but NOT maintained**. Its logic has diverged from the notebook (e.g. document
classification lives in both `supervisor_documents.py` and the notebook — the notebook's copy is
the current one). Do not port notebook changes here unless the app is explicitly revived.

Legacy paths:

- `src/api/` — FastAPI app, streaming chat UI, session store
- `src/agents/supervisor/` — the legacy Supervisor (13 modules)
- `src/core/` — config, SQLite store, reliability
- `src/knowledge_base/` — Chroma/RAG skeleton + admin UI
- `src/tools/` — DB/customer lookup tools
- `src/agents/web_search/`, `src/agents/guardrails/`, `src/agents/skills/`
- `src/agents/*/*_agent.py` — legacy per-agent classes (the notebook redefines these inline)
- `src/utils/flow.py` — unused

## Rule of thumb

- Changing agent behaviour, prompts, routing, classification → **the notebook** (+ shared modules above).
- Touching anything under the "Legacy" list → you are almost certainly in the wrong place.

See `README.md` for how to run the notebook and the legacy app.
