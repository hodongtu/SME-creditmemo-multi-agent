"""Source citations as markdown footnotes, and the checks that keep them honest.

Specialists cite a source by writing a marker — ``[^1]`` — beside the figure it
came from, and one definition line per marker at the end of their section. The
memo composer then merges five such reports into one, which is where the naive
version breaks: five agents each numbering from 1 produce five different
``[^1]``, and ``markdown`` resolves a repeated label by silently keeping the
LAST definition and discarding the rest. A citation would then point at another
agent's document with nothing to show it had happened.

So the labels are namespaced per agent before the merge (``[^ba1]``, ``[^cr1]``),
and gathered afterwards by ``consolidate_footnotes`` rather than by the composer
LLM — the same division of labour as heading renumbering, where the prompt asks
and ``_post_process_memo`` enforces.

Three failure modes were measured against ``markdown==3.10.3`` rather than
assumed, and each is reported instead of being silently repaired:

- a repeated label loses every definition but the last, with no warning;
- a marker with no definition renders as literal ``[^7]`` text in the PDF;
- a definition nobody references still appears in the source list.

Numbering is the library's job, ours is the order: it numbers definitions in the
order they are *defined*, so a list assembled in any other order makes the reader
meet footnote 3 before footnote 1. ``consolidate_footnotes`` therefore sorts by
first reference in the body.

Pure text analysis — no LLM call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Which prefix each agent's labels get before the composer merges them. Two
# letters, because the label is visible in the .md source a reviewer reads.
AGENT_LABEL_PREFIXES: dict[str, str] = {
    "BUSINESS_ACTIVITY_AGENT": "ba",
    "CREDIT_RELATIONSHIP_AGENT": "cr",
    # Financial analysis runs with require_citations = False, so it emits no
    # markers at all. Listed anyway: the mapping is the place someone looks to
    # answer "which prefix is this agent", and a missing entry reads as an
    # oversight rather than a deliberate exemption.
    "FINANCIAL_ANALYSIS_AGENT": "fa",
    "CREDIT_PROPOSAL_AGENT": "cp",
    "RISK_ASSESSMENT_AGENT": "ra",
}

# A definition line: "[^label]: source text". Must be anchored at line start —
# an inline "[^1]:" mid-sentence is a reference followed by a colon, not a
# definition, and markdown treats it that way too.
_DEFINITION = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*)$")
# A reference: "[^label]" NOT followed by a colon. The negative lookahead is how
# markdown itself tells the two apart, so the same text splits the same way here.
_REFERENCE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class FootnoteAudit:
    """What did not line up between markers and definitions.

    Empty lists mean a clean report. Nothing here is fixed automatically: a
    citation that does not resolve is a question about the evidence, and the
    reviewer is the one who can answer it.
    """

    orphan_references: list[str]
    unused_definitions: list[tuple[str, str]]
    duplicate_definitions: list[tuple[str, list[str]]]

    @property
    def is_clean(self) -> bool:
        return not (
            self.orphan_references
            or self.unused_definitions
            or self.duplicate_definitions
        )


def _mask_code_fences(text: str) -> list[bool]:
    """Mark which lines sit inside a fenced code block.

    A report carries ```linechart and ```mermaid blocks, and documentation of
    this very syntax would carry a literal "[^1]:" example. Neither is a
    citation, so both are skipped — the same guard ``_post_process_memo`` uses
    before it renumbers headings.
    """

    inside = False
    flags: list[bool] = []
    for line in text.splitlines():
        if _FENCE.match(line):
            # The fence line itself belongs to the block on both sides.
            flags.append(True)
            inside = not inside
            continue
        flags.append(inside)
    return flags


def namespace_footnotes(text: str, prefix: str) -> str:
    """Prefix every footnote label so one agent's markers cannot collide.

    ``[^1]`` becomes ``[^ba1]`` in both references and definitions. Whole
    labels are matched, never substrings: renaming "1" by substring would also
    rewrite the "1" inside "10" and quietly point two footnotes at one source.

    Returns the text unchanged when there is no prefix or no footnote in it.
    """

    if not prefix or not text:
        return text

    flags = _mask_code_fences(text)
    out: list[str] = []
    for line, in_code in zip(text.splitlines(), flags):
        if in_code:
            out.append(line)
            continue
        line = _DEFINITION.sub(
            lambda m: f"[^{prefix}{m.group(1)}]:"
            + (f" {m.group(2)}" if m.group(2) else ""),
            line,
        )
        line = _REFERENCE.sub(lambda m: f"[^{prefix}{m.group(1)}]", line)
        out.append(line)
    return "\n".join(out)


def consolidate_footnotes(text: str) -> tuple[str, FootnoteAudit]:
    """Move every definition into one list at the end, ordered by first use.

    Returns ``(text, audit)`` from a single pass, and the pairing is deliberate.
    Gathering the definitions necessarily collapses repeated labels — keeping
    the last, as markdown does — which destroys the evidence that a collision
    ever happened. A separate checker running afterwards could never see it, so
    the gathering reports what it collapsed.

    Idempotent: consolidating an already-consolidated report is a no-op.
    """

    if not text:
        return text, FootnoteAudit([], [], [])

    flags = _mask_code_fences(text)
    lines = text.splitlines()

    # Pass 1: lift out the definitions, keeping every occurrence so repeats stay
    # visible. Body lines are kept in order and stripped of definitions.
    seen: dict[str, list[str]] = {}
    body: list[str] = []
    for line, in_code in zip(lines, flags):
        match = None if in_code else _DEFINITION.match(line)
        if match is None:
            body.append(line)
            continue
        label, source = match.group(1), match.group(2).strip()
        seen.setdefault(label, []).append(source)
        # Each specialist writes its own "---" above its definition block. Once
        # the definitions are lifted out, that rule introduces nothing — and
        # since only the agents that cited anything wrote one, the leftovers
        # appear between some sections and not others, reading as a layout bug.
        # Drops only a rule that directly preceded definitions; a rule the model
        # put between two paragraphs is followed by prose, not by a definition,
        # so it is never reached here.
        end = len(body)
        while end and not body[end - 1].strip():
            end -= 1
        if end and body[end - 1].strip() == "---":
            del body[end - 1:]

    if not seen:
        # Nothing to gather. Return untouched rather than appending an empty
        # rule — this runs on every response, including plain chat answers.
        return text, FootnoteAudit([], [], [])

    # Lifting the definitions out of an already-consolidated report leaves our
    # own separator behind with nothing under it. Drop trailing rules and blank
    # lines so running this twice does not stack a second "---" onto the first.
    while body and body[-1].strip() in {"", "---"}:
        body.pop()

    # Pass 2: order of first reference in the body, which is the order the
    # reader meets them and therefore the order they must be numbered in.
    body_text = "\n".join(body)
    body_flags = _mask_code_fences(body_text)
    referenced: list[str] = []
    for line, in_code in zip(body.copy(), body_flags):
        if in_code:
            continue
        for label in _REFERENCE.findall(line):
            if label not in referenced:
                referenced.append(label)

    orphans = [label for label in referenced if label not in seen]
    unused = [
        (label, sources[-1]) for label, sources in seen.items() if label not in referenced
    ]
    duplicates = [
        (label, sources)
        for label, sources in seen.items()
        # Only a genuine disagreement is worth reporting: the same source
        # written twice is redundant, not dangerous.
        if len(sources) > 1 and len(set(sources)) > 1
    ]

    # Referenced definitions first, in reading order; then the unreferenced
    # ones, kept rather than dropped so a numbering slip cannot delete evidence.
    ordered = [label for label in referenced if label in seen]
    ordered += [label for label, _ in unused]
    # Last definition wins, matching how markdown resolves a repeated label —
    # so what the audit calls authoritative is what the PDF will show.
    definitions = [f"[^{label}]: {seen[label][-1]}" for label in ordered]

    # The blank line before "---" is load-bearing: a rule directly under a text
    # line is setext syntax and swallows that line into an <h2>.
    consolidated = body_text.rstrip() + "\n\n---\n\n" + "\n".join(definitions) + "\n"
    return consolidated, FootnoteAudit(orphans, unused, duplicates)


def format_footnote_findings(audit: FootnoteAudit) -> list[str]:
    """Render an audit as reader-facing Vietnamese findings."""

    findings: list[str] = []
    for label in audit.orphan_references:
        findings.append(
            f"Chú thích [^{label}] được dùng trong bài nhưng không có dòng định "
            "nghĩa nguồn tương ứng — có thể mô hình đã bịa số hoặc quên ghi nguồn."
        )
    for label, source in audit.unused_definitions:
        findings.append(
            f'Nguồn [^{label}] ("{_excerpt(source)}") không được câu nào trong bài '
            "trích dẫn — có thể là nguồn thừa hoặc lỗi đánh số."
        )
    for label, sources in audit.duplicate_definitions:
        listed = "; ".join(f'"{_excerpt(s)}"' for s in sources)
        findings.append(
            f"Chú thích [^{label}] được định nghĩa {len(sources)} lần với nội dung "
            f"khác nhau ({listed}) — chỉ định nghĩa cuối cùng được giữ, cần rà soát "
            "để không mất nguồn."
        )
    return findings


def _excerpt(text: str, limit: int = 60) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"
