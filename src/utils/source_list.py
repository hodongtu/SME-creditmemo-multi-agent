"""The "Nguồn thông tin" list at the top of every report, built from filenames.

A folder holding a year of monthly VAT returns produces a dozen lines before the
analysis starts, so the list is collapsed to one line per document type with the
periods compressed: sixteen ``TKT_MM.YYYY.xlsx`` become ``TKT 01-12/2025,
01-04/2026``.

That collapsing used to be asked of the model, in a prompt rule carrying worked
examples. It failed the first time a real folder exercised it: given those
sixteen files the model answered ``TKT 01-04/2025``, which is character for
character the example the rule had shown it. The result is worse than an ugly
list — a credit memo that names four returns when sixteen were read understates
its own evidence, and nothing downstream can tell.

So the arithmetic is here, and the model is handed the finished lines. Two rules
follow from what the failure was:

- **No document may vanish.** Every fallback is "list the files as they are",
  never "shorten as best you can". A long list is a cosmetic problem; a short
  one is a false statement about what was read.
- **Nothing is invented.** The label comes from the filenames themselves, not
  from the document type's name in the matrix — those are regulatory sentences
  ("Tờ khai thuế GTGT năm gần nhất và của các tháng/quý liền kề..."), not
  captions. If the filenames in a group disagree, there is no label to derive
  and the group is listed file by file.
"""

from __future__ import annotations

import re
from itertools import groupby

from src.utils.common import SUPPORTED_EXTENSIONS

# A period is a 4-digit year, optionally preceded by a month or quarter. The
# separator class carries ":" because macOS stores a "/" typed in Finder as a
# colon on disk — "TKT_01/2025.xlsx" on screen is "TKT_01:2025.xlsx" to Python,
# and a pattern that only knew about "/" would read none of these files.
_SEPARATORS = r"[\s._/\-:]"
_PERIOD = re.compile(
    rf"(?:(?:T|Q|QUY|THANG)?\s*(\d{{1,2}})\s*{_SEPARATORS}\s*)?((?:19|20)\d{{2}})",
    re.IGNORECASE,
)


def _parse_period(stem: str) -> tuple[tuple[int, int | None], tuple[int, int]] | None:
    """Read a (year, month) and where it sat, or None when the name has no period.

    Returning the span is what lets the label be derived by subtraction: whatever
    is left of the filename once the period is cut out is the name of the thing.
    """

    match = _PERIOD.search(stem)
    if not match:
        return None
    month = int(match.group(1)) if match.group(1) else None
    # A two-digit run next to a year is only a month if it could be one. "TKT_99"
    # is part of the name, not December of some 99th month.
    if month is not None and not 1 <= month <= 12:
        return None
    return (int(match.group(2)), month), match.span()


def _shared_label(stems_and_spans: list[tuple[str, tuple[int, int]]]) -> str:
    """The filename with its period removed, when every file agrees on it.

    Empty string when they disagree, which the caller reads as "these files have
    no common name" rather than as a label. Deriving one anyway — a longest
    common prefix, say — would caption ``TKT_01`` and ``ToKhaiThue_02`` as "T".
    """

    rests = {
        re.sub(rf"{_SEPARATORS}+", " ", stem[:start] + stem[end:]).strip(" -_.")
        for stem, (start, end) in stems_and_spans
    }
    if len(rests) != 1:
        return ""
    return rests.pop()


def _compress(periods: list[tuple[int, int | None]]) -> str:
    """Render a set of periods as short as it goes without losing any of them.

    Months run together into ranges within their year; whole years run together
    only when none of them carries a month, since "2024-2026" alongside a month
    list would read as a different kind of thing.
    """

    parts: list[str] = []
    for year, group in groupby(sorted(set(periods)), key=lambda period: period[0]):
        months = [month for _, month in group if month is not None]
        if not months:
            parts.append(str(year))
            continue
        runs: list[str] = []
        start = previous = months[0]
        for month in months[1:] + [None]:
            if month == previous + 1:
                previous = month
                continue
            runs.append(f"{start:02d}-{previous:02d}" if start != previous else f"{start:02d}")
            start = previous = month
        parts.append(f"{', '.join(runs)}/{year}")

    if len(parts) > 2 and all(re.fullmatch(r"\d{4}", part) for part in parts):
        years = [int(part) for part in parts]
        if years == list(range(years[0], years[-1] + 1)):
            return f"{years[0]}-{years[-1]}"
    return ", ".join(parts)


def _strip_extension(filename: str) -> str:
    """Drop the extension, but only one the pipeline actually ingests.

    Cutting at the last dot instead would take the year off ``TKT_01.2025`` — a
    file with no extension at all, which these folders do contain. Discovery has
    already rejected everything outside SUPPORTED_EXTENSIONS, so a name reaching
    this list either ends in one of them or ends in nothing.
    """

    stem, dot, extension = filename.rpartition(".")
    if dot and f".{extension.lower()}" in SUPPORTED_EXTENSIONS:
        return stem
    return filename


def _display_names(filenames: list[str]) -> list[str]:
    """Filenames as the report should print them, extensions off.

    Unless the extension is the only thing telling two of them apart: ``BCTC.pdf``
    and ``BCTC.xlsx`` would both render as ``BCTC`` and read as a duplicated line.
    Collapsing them to one entry would be worse — this module's whole rule is that
    no document disappears — so the group keeps its full names instead.
    """

    stripped = [_strip_extension(filename) for filename in filenames]
    if len(set(stripped)) != len(stripped):
        return list(filenames)
    return stripped


def _lines_for_group(filenames: list[str]) -> list[str]:
    """One line for a group of same-type files, or one line each if it cannot be."""

    if len(filenames) == 1:
        return _display_names(filenames)

    parsed = []
    for filename in filenames:
        stem = _strip_extension(filename)
        period = _parse_period(stem)
        if period is None:
            return _display_names(filenames)
        parsed.append((stem, period))

    label = _shared_label([(stem, span) for stem, (_, span) in parsed])
    if not label:
        return _display_names(filenames)
    return [f"{label} {_compress([period for _, (period, _) in parsed])}"]


def build_source_lines(documents: list[tuple[str, str]]) -> list[str]:
    """Collapse (filename, document_type) pairs into the report's source list.

    Grouped by document type rather than by filename shape, because the type is
    already decided by the routing matrix and three BCTC files carry the same id
    whatever they are called. Documents whose type is unknown each stand alone —
    they have nothing in common but their own unknown-ness.

    Input order is preserved between groups so the list follows the order the
    documents were routed in.
    """

    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for index, (filename, document_type) in enumerate(documents):
        key = document_type or f"__unknown_{index}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(filename)

    lines: list[str] = []
    for key in order:
        lines.extend(_lines_for_group(groups[key]))
    return lines
