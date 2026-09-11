"""The "Nguồn dữ liệu" list at the top of every report."""

from src.agents.documents.document_matrix import get_type


MAX_DESCRIPTION_WORDS = 20

def _usable_description(description: str) -> str:
    """The caption a description can serve as, or "" when it cannot."""

    text = " ".join((description or "").split())
    if not text or len(text.split()) > MAX_DESCRIPTION_WORDS:
        return ""
    return text


def _short_label(document_type: str) -> str:
    """What to call a group the classifier gave no usable description for."""

    matched = get_type(document_type) if document_type else None
    return matched.short_label if matched else "Tài liệu khác"


def _cell_safe(text: str) -> str:
    """Escape what would break the markdown table cell this line lands in."""

    return text.replace("|", r"\|")


def _line(caption: str, filenames: list[str]) -> str:
    """One source line: what the document is, in bold, then the files it came from."""

    names = ", ".join(_cell_safe(name) for name in filenames)
    return f"**{_cell_safe(caption)}** —  <em>{names}</em>"


def build_source_lines(
    documents: list[tuple[str, str, str]],
) -> list[str]:
    """Turn (filename, document_type, description) triples into report lines."""

    groups: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    types: dict[str, str] = {}

    for index, (filename, document_type, description) in enumerate(documents):
        key = document_type or f"__unknown_{index}"
        if key not in groups:
            groups[key] = []
            order.append(key)
            types[key] = document_type
        groups[key].append((filename, _usable_description(description)))

    lines: list[str] = []
    for key in order:
        members = groups[key]
        fallback = _short_label(types[key])
        captions = {description or fallback for _, description in members}
        if len(captions) == 1:
            lines.append(_line(captions.pop(), [name for name, _ in members]))
            continue
        for filename, description in members:
            lines.append(_line(description or fallback, [filename]))
    return lines
