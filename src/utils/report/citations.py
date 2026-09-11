"""Source citations as markdown footnotes, and the checks that keep them honest."""

import re
from dataclasses import dataclass

from src.utils.common import CODE_FENCE as _FENCE, normalize_text

AGENT_LABEL_PREFIXES: dict[str, str] = {
    "BUSINESS_ACTIVITY_AGENT": "ba",
    "CREDIT_RELATIONSHIP_AGENT": "cr",
    "FINANCIAL_ANALYSIS_AGENT": "fa",
    "CREDIT_PROPOSAL_AGENT": "cp",
}

_DEFINITION = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]?(.*)$")
_REFERENCE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_DEFINITION_START = re.compile(r"\[\^[^\]\s]+\]:")


@dataclass(frozen=True)
class FootnoteAudit:
    """What did not line up between markers and definitions."""

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
    """Mark which lines sit inside a fenced code block."""

    inside = False
    flags: list[bool] = []
    for line in text.splitlines():
        if _FENCE.match(line):
            flags.append(True)
            inside = not inside
            continue
        flags.append(inside)
    return flags


def _split_squeezed_definitions(text: str) -> str:
    """Break a line carrying 2+ footnote definitions into one per line."""

    if not text:
        return text

    flags = _mask_code_fences(text)
    out: list[str] = []
    for line, in_code in zip(text.splitlines(), flags):
        if in_code:
            out.append(line)
            continue
        starts = [m.start() for m in _DEFINITION_START.finditer(line)]
        if len(starts) < 2:
            out.append(line)
            continue
        bounds = starts + [len(line)]
        if starts[0] > 0:
            out.append(line[: starts[0]].strip())
        for start, end in zip(bounds, bounds[1:]):
            out.append(line[start:end].strip())
    return "\n".join(out)


def _canonical_labels(
    seen: dict[str, list[str]],
    referenced: list[str],
) -> dict[str, str]:
    """Map every label to the one label that will represent its source."""

    order = {label: index for index, label in enumerate(referenced)}
    unreferenced_rank = len(order)
    groups: dict[str, list[str]] = {}
    for label, sources in seen.items():
        groups.setdefault(normalize_text(sources[-1]), []).append(label)

    canonical: dict[str, str] = {}
    for members in groups.values():
        if len(members) == 1:
            canonical[members[0]] = members[0]
            continue
        winner = min(members, key=lambda label: order.get(label, unreferenced_rank))
        for label in members:
            canonical[label] = winner
    return canonical


def _rewrite_references(lines: list[str], canonical: dict[str, str]) -> list[str]:
    """Point every marker at its representative label, leaving code blocks alone."""

    flags = _mask_code_fences("\n".join(lines))
    out: list[str] = []
    for line, in_code in zip(lines, flags):
        if in_code:
            out.append(line)
            continue
        out.append(
            _REFERENCE.sub(
                lambda m: f"[^{canonical.get(m.group(1), m.group(1))}]",
                line,
            )
        )
    return out


def namespace_footnotes(text: str, prefix: str) -> str:
    """Prefix every footnote label so one agent's markers cannot collide."""

    if not prefix or not text:
        return text

    text = _split_squeezed_definitions(text)
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
    """Move every definition into one list at the end, ordered by first use."""

    if not text:
        return text, FootnoteAudit([], [], [])

    text = _split_squeezed_definitions(text)
    flags = _mask_code_fences(text)
    lines = text.splitlines()

    seen: dict[str, list[str]] = {}
    body: list[str] = []
    for line, in_code in zip(lines, flags):
        match = None if in_code else _DEFINITION.match(line)
        if match is None:
            body.append(line)
            continue
        label, source = match.group(1), match.group(2).strip()
        seen.setdefault(label, []).append(source)
        end = len(body)
        while end and not body[end - 1].strip():
            end -= 1
        if end and body[end - 1].strip() == "---":
            del body[end - 1:]

    if not seen:
        return text, FootnoteAudit([], [], [])

    while body and body[-1].strip() in {"", "---"}:
        body.pop()

    def _reference_order(source_lines: list[str]) -> list[str]:
        flags = _mask_code_fences("\n".join(source_lines))
        found: list[str] = []
        for line, in_code in zip(source_lines, flags):
            if in_code:
                continue
            for label in _REFERENCE.findall(line):
                if label not in found:
                    found.append(label)
        return found

    duplicates = [
        (label, sources)
        for label, sources in seen.items()
        if len(sources) > 1 and len(set(sources)) > 1
    ]

    canonical = _canonical_labels(seen, _reference_order(body))
    if any(label != winner for label, winner in canonical.items()):
        body = _rewrite_references(body, canonical)
    for label, winner in canonical.items():
        if label != winner:
            del seen[label]

    body_text = "\n".join(body)
    referenced = _reference_order(body)

    orphans = [label for label in referenced if label not in seen]
    unused = [
        (label, sources[-1]) for label, sources in seen.items() if label not in referenced
    ]

    ordered = [label for label in referenced if label in seen]
    ordered += [label for label, _ in unused]
    definitions = [f"[^{label}]: {seen[label][-1]}" for label in ordered]

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
