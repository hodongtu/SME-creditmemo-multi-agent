"""One-time (re-runnable) conversion of industry reference decks to text.

Reads every ``docs/industries/*.pptx`` (source decks — kept local, ``docs/*``
is gitignored except ``*.md``, same convention as ``document_matrix.xlsx``),
extracts each with ``extract_pptx_text``, and writes the result to
``src/knowledge/industries/<id>.txt`` — one file per industry, so selecting an
industry at runtime is a direct file read, no parsing needed. Also maintains
``src/knowledge/industries_manifest.yaml``, the catalogue
``select_industry()`` shows the LLM.

Safe to re-run: an existing manifest entry's ``display_name`` is NEVER
overwritten (you are expected to hand-edit it after the first run, same as
``document_matrix.yaml``), only ``char_count`` is refreshed. Run this again
whenever a deck is added, removed, or replaced:

    python scripts/ingest_industry_knowledge.py

Pure offline text extraction (no OCR, no LLM call) — 30 decks finishes in
seconds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.common import normalize_text  # noqa: E402
from src.utils.extractors import extract_pptx_text  # noqa: E402

SOURCE_DIR = PROJECT_ROOT / "docs" / "industries"
OUTPUT_DIR = PROJECT_ROOT / "src" / "knowledge" / "industries"
MANIFEST_PATH = PROJECT_ROOT / "src" / "knowledge" / "industries_manifest.yaml"


def _slug(filename_stem: str) -> str:
    """Filesystem/LLM-safe id: lowercase ASCII, words joined by underscore."""

    return normalize_text(filename_stem).replace(" ", "_") or "nganh"


def _display_name(filename_stem: str) -> str:
    """A readable default name from the filename, until hand-edited."""

    return filename_stem.replace("_", " ").replace("-", " ").strip().title()


def ingest() -> list[dict]:
    if not SOURCE_DIR.exists():
        print(f"Không tìm thấy thư mục nguồn: {SOURCE_DIR}")
        return []

    existing: dict[str, dict] = {}
    if MANIFEST_PATH.exists():
        loaded = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or []
        existing = {item["id"]: item for item in loaded}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for pptx_path in sorted(SOURCE_DIR.glob("*.pptx")):
        industry_id = _slug(pptx_path.stem)
        text = extract_pptx_text(str(pptx_path))
        (OUTPUT_DIR / f"{industry_id}.txt").write_text(text, encoding="utf-8")

        prior = existing.get(industry_id, {})
        manifest.append(
            {
                "id": industry_id,
                # Preserve a hand-edited name across re-runs; only a brand-new
                # id gets the derived default.
                "display_name": prior.get("display_name") or _display_name(pptx_path.stem),
                "source_file": pptx_path.name,
                "char_count": len(text),
            }
        )
        print(f"  {pptx_path.name} -> {industry_id}.txt ({len(text)} ký tự)")

    MANIFEST_PATH.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Đã ghi {len(manifest)} ngành vào {MANIFEST_PATH}")
    return manifest


if __name__ == "__main__":
    ingest()
