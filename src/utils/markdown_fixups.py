"""Structural markdown fixes that ``markdown``'s parser is strict about.

Pure text transforms, no LLM call — same spirit as ``citations.py`` but for a
different failure mode. Kept in its own module because it has nothing to do
with citations: it is about paragraph/list boundaries, which any block of
report prose can get wrong regardless of whether it carries a footnote.
"""

from __future__ import annotations

import re

_LIST_MARKER = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")


def ensure_blank_line_before_lists(text: str) -> str:
    """Insert a blank line before a list that starts right after prose.

    Measured against ``markdown==3.10.3``: a "-"/"*"/"1." line with no blank
    line above it is lazy continuation of the previous paragraph whenever that
    line is non-blank, non-list text — the marker renders as a literal
    character inside the ``<p>``, not as a ``<ul>``/``<ol>``. Confirmed with
    "**Nhận định**: ...\\n- điểm 1" rendering with a bare hyphen, and the same
    block with a blank line inserted rendering as a real list.

    A no-op on a list that already follows a blank line or another list item,
    and on anything inside a fenced code block.
    """

    if not text:
        return text

    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
        elif not in_code and _LIST_MARKER.match(line):
            prev = out[-1] if out else ""
            if prev.strip() and not _LIST_MARKER.match(prev):
                out.append("")
        out.append(line)
    return "\n".join(out)
