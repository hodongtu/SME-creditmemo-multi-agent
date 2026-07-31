"""OCR helpers for PDF documents.

Pipeline per page: rasterize -> image preprocessing (grayscale, denoise, deskew,
Otsu threshold, optional upscale) -> layout-aware Tesseract OCR -> post-OCR text
cleanup. Results are cached on disk keyed by file content hash + OCR config so
re-runs of the notebook do not re-OCR the same document.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import pypdfium2 as pdfium
import pytesseract

DEFAULT_TESSERACT_CMD = "/opt/homebrew/bin/tesseract"
# pypdfium2 bundles the pdfium binary, so no system poppler install is required.
PDF_POINTS_PER_INCH = 72.0


def _ocr_config() -> dict[str, object]:
    """Read OCR settings from environment with backward-compatible defaults.

    Image preprocessing is OFF by default: on reasonably rendered PDFs Tesseract's
    own internal thresholding beats external binarization/denoising (which blur
    Vietnamese diacritics). Enable it (and its sub-steps) only for poor scans.
    """
    return {
        "tesseract_cmd": os.getenv("TESSERACT_CMD", DEFAULT_TESSERACT_CMD),
        "dpi": int(os.getenv("OCR_DPI", "300")),
        "lang": os.getenv("OCR_LANG", "vie+eng"),
        "psm": os.getenv("OCR_PSM", "6"),
        "oem": os.getenv("OCR_OEM", "3"),
        "preprocess": os.getenv("OCR_PREPROCESS", "0") != "0",
        "deskew": os.getenv("OCR_DESKEW", "1") != "0",
        "denoise": os.getenv("OCR_DENOISE", "0") != "0",
        "binarize": os.getenv("OCR_BINARIZE", "0") != "0",
        "layout": os.getenv("OCR_LAYOUT", "1") != "0",
        "upscale": float(os.getenv("OCR_UPSCALE", "1.0")),
        "cache_dir": os.getenv("OCR_CACHE_DIR", ""),
    }


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------
def _deskew(gray: np.ndarray, max_angle: float = 15.0) -> np.ndarray:
    """Rotate the image to correct small scan skew, if any is detected."""
    inverted = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(
        inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    coords = np.column_stack(np.where(binary > 0))
    if coords.shape[0] < 50:
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    # Only correct meaningful, plausible skew; skip near-zero / extreme values.
    if abs(angle) < 0.5 or abs(angle) > max_angle:
        return gray

    height, width = gray.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def preprocess_image(
    image: Image.Image,
    deskew: bool = True,
    denoise: bool = False,
    binarize: bool = False,
    upscale: float = 1.0,
) -> Image.Image:
    """Prepare a scanned page image for OCR (opt-in; steps are individually gated).

    Only the steps that empirically help a given scan should be enabled. Denoising
    and forced binarization are OFF by default because they blur small diacritics on
    already-clean renders; enable them for photographed or low-quality scans.
    """
    array = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    if denoise:
        gray = cv2.fastNlMeansDenoising(gray, h=10)
    if deskew:
        gray = _deskew(gray)
    if upscale and upscale > 1.0:
        gray = cv2.resize(
            gray,
            None,
            fx=upscale,
            fy=upscale,
            interpolation=cv2.INTER_CUBIC,
        )
    if binarize:
        _, gray = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
    return Image.fromarray(gray)


# ---------------------------------------------------------------------------
# Layout-aware OCR (keeps table columns separated)
# ---------------------------------------------------------------------------
def image_to_layout_text(
    image: Image.Image,
    lang: str,
    config: str,
    timeout: int | None,
) -> str:
    """OCR a page and rebuild text preserving line and column structure.

    Uses ``image_to_data`` so tokens keep their bounding boxes; tokens are
    grouped into physical lines and separated by whitespace proportional to the
    horizontal gap between them, so financial-statement columns do not collapse.
    Falls back to plain ``image_to_string`` on any failure.
    """
    try:
        data = pytesseract.image_to_data(
            image,
            lang=lang,
            config=config,
            timeout=timeout,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        return pytesseract.image_to_string(
            image, lang=lang, config=config, timeout=timeout
        )

    lines: dict[tuple[int, int, int], list[tuple[int, int, str]]] = {}
    char_widths: list[float] = []
    for index, word in enumerate(data["text"]):
        if not word or not word.strip():
            continue
        try:
            conf = float(data["conf"][index])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        left = data["left"][index]
        width = data["width"][index]
        lines.setdefault(key, []).append((left, left + width, word))
        if word:
            char_widths.append(width / max(len(word), 1))

    if not lines:
        return pytesseract.image_to_string(
            image, lang=lang, config=config, timeout=timeout
        )

    approx_char_width = np.median(char_widths) if char_widths else 10.0
    gap_per_space = max(approx_char_width, 1.0)

    rendered_lines = []
    for key in sorted(lines):
        tokens = sorted(lines[key], key=lambda item: item[0])
        parts = []
        previous_right: int | None = None
        for left, right, word in tokens:
            if previous_right is not None:
                gap = left - previous_right
                spaces = int(gap / gap_per_space) if gap > 0 else 0
                parts.append(" " * max(1, min(spaces, 8)))
            parts.append(word)
            previous_right = right
        rendered_lines.append("".join(parts))
    return "\n".join(rendered_lines)


# ---------------------------------------------------------------------------
# Post-OCR text cleanup
# ---------------------------------------------------------------------------
_NUMERIC_RUN = re.compile("(?<=\\d)[ \t\u00a0](?=\\d{3}\\b)")
_MULTISPACE = re.compile("[ \t\u00a0]{2,}")
_DEHYPHENATE = re.compile(r"(\w)-\n(\w)")


def _line_is_noise(line: str) -> bool:
    """Detect OCR garbage lines dominated by punctuation/symbols."""
    stripped = line.strip()
    if not stripped:
        return False
    meaningful = sum(char.isalnum() for char in stripped)
    return meaningful / len(stripped) < 0.25 and len(stripped) >= 4


def _strip_repeated_headers_footers(pages: list[str]) -> list[str]:
    """Remove header/footer lines that repeat across most pages."""
    if len(pages) < 3:
        return pages

    from collections import Counter

    edge_counter: Counter[str] = Counter()
    for page in pages:
        page_lines = [line.strip() for line in page.splitlines() if line.strip()]
        edges = page_lines[:2] + page_lines[-2:]
        for line in edges:
            if len(line) >= 4:
                edge_counter[line] += 1

    threshold = max(3, int(len(pages) * 0.6))
    repeated = {line for line, count in edge_counter.items() if count >= threshold}
    if not repeated:
        return pages

    cleaned = []
    for page in pages:
        kept = [
            line
            for line in page.splitlines()
            if line.strip() not in repeated
        ]
        cleaned.append("\n".join(kept))
    return cleaned


def clean_ocr_text(text: str) -> str:
    """Normalize raw OCR text before it reaches the LLM and ratio parser."""
    if not text:
        return ""

    # Join words split across a line break by hyphenation.
    text = _DEHYPHENATE.sub(r"\1\2", text)

    cleaned_lines = []
    for line in text.splitlines():
        if _line_is_noise(line):
            continue
        # Merge space-split thousands groups ("1 234 567" -> "1234567").
        line = _NUMERIC_RUN.sub("", line)
        line = _MULTISPACE.sub("  ", line.rstrip())
        cleaned_lines.append(line)

    # Collapse runs of blank lines to a single blank line.
    result_lines: list[str] = []
    for line in cleaned_lines:
        if not line.strip() and result_lines and not result_lines[-1].strip():
            continue
        result_lines.append(line)
    return "\n".join(result_lines).strip()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
def _file_sha256(path: str) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _cache_dir(settings: dict[str, object]) -> Path:
    configured = str(settings.get("cache_dir") or "")
    if configured:
        directory = Path(configured)
    else:
        directory = Path(tempfile.gettempdir()) / "sme_ocr_cache"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(pdf_path: str, settings: dict[str, object]) -> str:
    signature = {
        key: settings[key]
        for key in (
            "dpi", "lang", "psm", "oem", "preprocess",
            "deskew", "denoise", "binarize", "layout", "upscale",
        )
    }
    payload = f"{_file_sha256(pdf_path)}|{json.dumps(signature, sort_keys=True)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PDF rasterization (pypdfium2 — no system poppler dependency)
# ---------------------------------------------------------------------------
def _render_pdf_pages(pdf_path: str, dpi: int) -> list[Image.Image]:
    """Render each PDF page to a PIL image using the bundled pdfium engine."""
    scale = dpi / PDF_POINTS_PER_INCH
    document = pdfium.PdfDocument(pdf_path)
    try:
        images = []
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil().convert("RGB"))
        return images
    finally:
        document.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def ocr_pdf(pdf_path: str, timeout_seconds: float | None = None) -> str:
    """Extract Vietnamese and English text from a PDF via Tesseract OCR."""
    settings = _ocr_config()
    pytesseract.pytesseract.tesseract_cmd = str(settings["tesseract_cmd"])
    timeout = int(timeout_seconds) if timeout_seconds else None

    cache_directory = _cache_dir(settings)
    cache_file = cache_directory / f"{_cache_key(pdf_path, settings)}.txt"
    if cache_file.is_file():
        return cache_file.read_text(encoding="utf-8")

    tess_config = f"--oem {settings['oem']} --psm {settings['psm']}"
    lang = str(settings["lang"])
    upscale = float(settings["upscale"])

    images = _render_pdf_pages(pdf_path, int(settings["dpi"]))

    page_texts = []
    for image in images:
        prepared = (
            preprocess_image(
                image,
                deskew=bool(settings["deskew"]),
                denoise=bool(settings["denoise"]),
                binarize=bool(settings["binarize"]),
                upscale=upscale,
            )
            if settings["preprocess"]
            else image
        )
        if settings["layout"]:
            text = image_to_layout_text(prepared, lang, tess_config, timeout)
        else:
            text = pytesseract.image_to_string(
                prepared, lang=lang, config=tess_config, timeout=timeout
            )
        page_texts.append(clean_ocr_text(text))

    page_texts = _strip_repeated_headers_footers(page_texts)
    full_text = "".join(
        f"\n--- Page {index + 1} ---\n{text}"
        for index, text in enumerate(page_texts)
    )

    try:
        cache_file.write_text(full_text, encoding="utf-8")
    except OSError:
        pass
    return full_text
