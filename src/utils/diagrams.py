"""Render Mermaid flowcharts as plain HTML/CSS so they survive PDF export.

The agents emit ```mermaid blocks because that renders natively on GitHub and in
VS Code. WeasyPrint has no JavaScript, so a mermaid block would reach the PDF as
a raw ``<pre><code>`` dump. This module rewrites those blocks into flex-box
markup that ``markdown`` passes through untouched and WeasyPrint styles with
``DIAGRAM_CSS``.

Only simple flowcharts are supported (that is all the report templates ask for).
Anything this parser cannot read is left as the original mermaid block rather
than dropped, so content is never lost.
"""

from __future__ import annotations

import html
import re

MERMAID_BLOCK = re.compile(
    r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
# "flowchart LR" / "graph TD" ...
_HEADER = re.compile(r"^\s*(?:flowchart|graph)\s+(LR|RL|TD|TB|BT)\b", re.IGNORECASE)
# A --> B, A -->|nhãn| B, A --- B, A -.-> B, A ==> B
_CONNECTOR = re.compile(
    r"\s*(?:-{2,3}>|-\.->|={2,}>|-{3})\s*(?:\|(?P<edgelabel>[^|]*)\|\s*)?"
)
_NODE_ID = re.compile(r"\s*(?P<id>[A-Za-z0-9_]+)\s*")
_OPEN_TO_CLOSE = {"[": "]", "(": ")", "{": "}"}


def _scan_label(text: str, start: int) -> tuple[str | None, int]:
    """Read a bracketed node label at ``start``, honouring nesting like [[x]]."""

    if start >= len(text) or text[start] not in _OPEN_TO_CLOSE:
        return None, start
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in _OPEN_TO_CLOSE:
            depth += 1
        elif char in _OPEN_TO_CLOSE.values():
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
        index += 1
    return None, start


def _scan_line(line: str) -> tuple[list[tuple[str, str | None]], list[tuple[int, str]]]:
    """Split ``A[x] -->|l| B[y] --> C`` into its nodes and the links between them.

    Chains must be walked sequentially — a plain regex scan would treat each
    destination as consumed and silently drop the rest of the chain.
    """

    nodes: list[tuple[str, str | None]] = []
    links: list[tuple[int, str]] = []
    pos = 0
    match = _NODE_ID.match(line, pos)
    if not match:
        return [], []
    label, pos = _scan_label(line, match.end())
    nodes.append((match.group("id"), label))

    while pos < len(line):
        connector = _CONNECTOR.match(line, pos)
        if not connector:
            break
        pos = connector.end()
        match = _NODE_ID.match(line, pos)
        if not match:
            break
        label, pos = _scan_label(line, match.end())
        links.append((len(nodes) - 1, (connector.group("edgelabel") or "").strip()))
        nodes.append((match.group("id"), label))
    return nodes, links
# Standalone node declaration: A[Nhà cung cấp]
_NODE_DECL = re.compile(
    r"^\s*(?P<id>[A-Za-z0-9_]+)\s*(?P<label>[\[\({].+?[\]\)}])\s*$"
)

DIAGRAM_CSS = """
.mmd{margin:14px 0}
/* nowrap + shrinkable nodes: a wrapped row would leave the last box stretched
   across its own line, which reads worse than slightly narrower boxes. */
.mmd-row{display:flex;align-items:stretch;gap:5px;flex-wrap:nowrap;margin:6px 0}
.mmd-col{display:flex;flex-direction:column;align-items:center;gap:4px;margin:6px 0}
.mmd-node{border:1.5px solid #2f6f9f;border-radius:6px;background:#eef5fb;
  padding:7px 8px;text-align:center;font-size:9.5pt;line-height:1.3;
  flex:1 1 0;min-width:0;overflow-wrap:break-word}
.mmd-col .mmd-node{flex:0 0 auto;min-width:180px;font-size:10pt;padding:7px 12px}
.mmd-arrow{align-self:center;color:#2f6f9f;font-weight:700;font-size:13pt;
  flex:0 0 auto;max-width:78px;text-align:center;padding:0 1px}
.mmd-arrow .mmd-elabel{display:block;font-size:8pt;font-weight:400;color:#456;
  line-height:1.2;text-align:center;overflow-wrap:break-word}
"""


def _clean_label(raw: str | None, fallback: str) -> str:
    """Strip mermaid node brackets and quotes, keeping the visible text."""

    if not raw:
        return fallback
    text = raw.strip()
    text = re.sub(r"^[\[\({]+", "", text)
    text = re.sub(r"[\]\)}]+$", "", text)
    text = text.strip().strip('"').strip("'").strip()
    # Mermaid uses <br/> for line breaks inside a node.
    parts = [part.strip() for part in re.split(r"<br\s*/?>", text) if part.strip()]
    return "<br>".join(html.escape(part) for part in parts) or fallback


def _parse(source: str) -> tuple[str, list[str], dict[str, str], list[tuple[str, str, str]]]:
    """Return (direction, node order, labels, edges) for a mermaid flowchart."""

    direction = "LR"
    labels: dict[str, str] = {}
    order: list[str] = []
    edges: list[tuple[str, str, str]] = []

    def remember(node_id: str, raw_label: str | None) -> None:
        if node_id not in labels:
            labels[node_id] = _clean_label(raw_label, html.escape(node_id))
            order.append(node_id)
        elif raw_label:
            labels[node_id] = _clean_label(raw_label, labels[node_id])

    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        header = _HEADER.match(stripped)
        if header:
            direction = header.group(1).upper()
            continue
        if stripped.startswith(("subgraph", "end", "classDef", "class ", "style ", "linkStyle")):
            continue

        line_nodes, line_links = _scan_line(stripped)
        if line_links:
            for node_id, raw_label in line_nodes:
                remember(node_id, raw_label)
            for index, edge_label in line_links:
                edges.append(
                    (line_nodes[index][0], edge_label, line_nodes[index + 1][0])
                )
            continue

        decl = _NODE_DECL.match(stripped)
        if decl:
            remember(decl.group("id"), decl.group("label"))

    return direction, order, labels, edges


def _linear_chain(
    order: list[str],
    edges: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]] | None:
    """Return the edges in path order when the graph is a single simple chain."""

    if not edges:
        return None
    out: dict[str, list[tuple[str, str, str]]] = {}
    indegree: dict[str, int] = {node: 0 for node in order}
    for src, label, dst in edges:
        out.setdefault(src, []).append((src, label, dst))
        indegree[dst] = indegree.get(dst, 0) + 1
    if any(len(items) > 1 for items in out.values()):
        return None
    if any(count > 1 for count in indegree.values()):
        return None
    starts = [node for node in order if indegree.get(node, 0) == 0]
    if len(starts) != 1:
        return None

    chain: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    current = starts[0]
    while current in out:
        if current in seen:
            return None
        seen.add(current)
        edge = out[current][0]
        chain.append(edge)
        current = edge[2]
    return chain if len(chain) == len(edges) else None


def _node_html(label: str) -> str:
    return f'<div class="mmd-node">{label}</div>'


def _arrow_html(label: str, vertical: bool) -> str:
    glyph = "&#8595;" if vertical else "&#8594;"
    edge_label = (
        f'<span class="mmd-elabel">{html.escape(label)}</span>' if label else ""
    )
    return f'<div class="mmd-arrow">{glyph}{edge_label}</div>'


def _render(source: str) -> str | None:
    """Render one mermaid flowchart as HTML, or None if it is not parseable."""

    direction, order, labels, edges = _parse(source)
    if not labels:
        return None
    vertical = direction in {"TD", "TB", "BT"}

    chain = _linear_chain(order, edges)
    if chain is not None:
        parts = [_node_html(labels[chain[0][0]])]
        for _, label, dst in chain:
            parts.append(_arrow_html(label, vertical))
            parts.append(_node_html(labels[dst]))
        container = "mmd-col" if vertical else "mmd-row"
        return f'<div class="mmd"><div class="{container}">{"".join(parts)}</div></div>'

    if not edges:
        # Nodes only: show them as a single row of boxes.
        boxes = "".join(_node_html(labels[node]) for node in order)
        return f'<div class="mmd"><div class="mmd-row">{boxes}</div></div>'

    # Branching graph: one row per edge stays readable without a layout engine.
    rows = [
        '<div class="mmd-row">'
        + _node_html(labels[src])
        + _arrow_html(label, False)
        + _node_html(labels[dst])
        + "</div>"
        for src, label, dst in edges
    ]
    return f'<div class="mmd">{"".join(rows)}</div>'


def mermaid_to_html(text: str) -> str:
    """Replace ```mermaid blocks with HTML/CSS boxes for non-JS renderers.

    Unparseable blocks are returned untouched so no content is ever lost.
    """

    def replace(match: re.Match[str]) -> str:
        rendered = _render(match.group(1))
        return rendered if rendered else match.group(0)

    return MERMAID_BLOCK.sub(replace, text or "")
