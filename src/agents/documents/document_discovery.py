"""Finding the files on disk and reading which upload box each came from."""

import hashlib
from functools import lru_cache
from pathlib import Path

from src.agents.documents.document_matrix import load_matrix
from src.utils.common import SUPPORTED_EXTENSIONS
from src.utils.paths import PROJECT_ROOT


def resolve_input_path(raw_path: str) -> Path:
    """Resolve a relative notebook input against the project root."""

    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def compute_file_hash(path: str) -> str:
    """SHA-256 of the file's bytes, used to drop duplicates before extraction."""

    sha256 = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


@lru_cache(maxsize=1)
def box_ids() -> frozenset[str]:
    """The upload box ids, which are verbatim the folder names the screen makes."""
    return frozenset(doc.group_id for doc in load_matrix().types.values())


def group_from_path(path: str) -> str:
    """The upload box a file came from, or "" when no ancestor names one.

    Every ancestor is checked, not just the parent: discovery recurses, so a file
    at ``ho_so_tai_chinh/2025/BCTC.pdf`` used to be found and then lose its box.
    Nearest ancestor wins. "" restores the pre-upload-box behaviour exactly.
    """

    ids = box_ids()
    for parent in Path(path).parents:
        if parent.name in ids:
            return parent.name
    return ""


def discover_documents(input_paths: list[str], max_files: int = 50) -> list[str]:
    """Supported files under the given paths, recursively, de-duplicated.

    Unreadable files are named on the way past rather than dropped in silence: a
    folder of twelve XML tax returns once produced a report built on nothing at
    all, with no line anywhere saying so.
    """

    files = []
    unreadable: list[str] = []

    def consider(item) -> None:
        # Office writes a "~$name.xlsx" lock file beside any open workbook. It
        # carries a supported extension, so it used to be discovered, OCR'd and
        # classified — and 165 bytes of binary scored 0.87 confidence against
        # the real workbook's 0.76, entering the evidence set as required
        # evidence. Skipped in silence like dotfiles: it is Office's temp file,
        # not something the user meant to submit, so it does not belong in the
        # unreadable-files warning either.
        if item.name.startswith("~$"):
            return
        if item.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(str(item))
        elif not item.name.startswith("."):
            unreadable.append(item.name)

    for raw_path in input_paths:
        path = resolve_input_path(raw_path)
        if path.is_file():
            consider(path)
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file():
                    consider(item)

    if unreadable:
        print(
            f"[discover_documents] WARNING: skipped {len(unreadable)} file(s) with "
            f"an unsupported extension: {', '.join(sorted(unreadable)[:10])}"
            f"{' …' if len(unreadable) > 10 else ''}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    seen = set()
    deduped = []
    for file in files:
        if file not in seen:
            deduped.append(file)
            seen.add(file)

    if len(deduped) > max_files:
        dropped = len(deduped) - max_files
        print(
            f"[discover_documents] WARNING: discovered {len(deduped)} documents; "
            f"processing the first {max_files} and DROPPING {dropped}. "
            f"Increase MAX_FILES to include them."
        )
        deduped = deduped[:max_files]
    return deduped
