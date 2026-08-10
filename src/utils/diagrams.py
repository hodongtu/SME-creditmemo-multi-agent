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
from dataclasses import dataclass

from src.utils.graph_svg import render_svg

MERMAID_BLOCK = re.compile(
    r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
# "flowchart LR" / "graph TD" ...
_HEADER = re.compile(r"^\s*(?:flowchart|graph)\s+(LR|RL|TD|TB|BT)\b", re.IGNORECASE)
# A --> B, A -->|nhãn| B, A --- B, A -.-> B, A ==> B, and mermaid's other way of
# writing an edge label: A -- nhãn --> B, A == nhãn ==> B.
#
# The inline-label forms must come FIRST. Tried in the other order, "-{2,3}>"
# matches the leading "--" of "-- 65% -->" and the label text is then read as
# part of the next node id, which collapses the whole line into one box.
_CONNECTOR = re.compile(
    r"\s*(?:"
    r"-{2}\s*(?P<dashlabel>[^|>=-][^>]*?)\s*-{2,3}>"
    r"|={2}\s*(?P<eqlabel>[^|>=-][^>]*?)\s*={2,}>"
    r"|-{2,3}>|-\.->|={2,}>|-{3}"
    r")\s*(?:\|(?P<pipelabel>[^|]*)\|\s*)?"
)
_NODE_ID = re.compile(r"\s*(?P<id>[A-Za-z0-9_]+)\s*")
_OPEN_TO_CLOSE = {"[": "]", "(": ")", "{": "}"}
# classDef hilite fill:#C6E0B4,stroke:#333
_CLASSDEF = re.compile(r"^classDef\s+(?P<name>[A-Za-z0-9_]+)\s+(?P<body>.+)$")
# class A,B hilite
_CLASS_APPLY = re.compile(r"^class\s+(?P<ids>[A-Za-z0-9_,\s]+?)\s+(?P<name>[A-Za-z0-9_]+)\s*$")
# style A fill:#eee
_STYLE_DECL = re.compile(r"^style\s+(?P<id>[A-Za-z0-9_]+)\s+(?P<body>.+)$")
# The ":::hilite" suffix, which follows the node's label when it has one
# ("VNM[Vinamilk]:::hilite"), so it has to be consumed while scanning rather
# than matched against the bare id.
_INLINE_CLASS = re.compile(r"\s*:::(?P<name>[A-Za-z0-9_]+)")
# subgraph id[Tiêu đề]  |  subgraph Tiêu đề
_SUBGRAPH = re.compile(
    r"^subgraph\s+(?:(?P<id>[A-Za-z0-9_]+)\s*\[(?P<title>.+?)\]|(?P<plain>.+?))\s*$"
)


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


def _scan_class(text: str, start: int) -> tuple[str | None, int]:
    """Read a ":::className" suffix at ``start``, if present."""

    match = _INLINE_CLASS.match(text, start)
    if not match:
        return None, start
    return match.group("name"), match.end()


def _scan_line(
    line: str,
) -> tuple[list[tuple[str, str | None, str | None]], list[tuple[int, str]]]:
    """Split ``A[x] -->|l| B[y]:::cls --> C`` into its nodes and the links.

    Chains must be walked sequentially — a plain regex scan would treat each
    destination as consumed and silently drop the rest of the chain.
    """

    nodes: list[tuple[str, str | None, str | None]] = []
    links: list[tuple[int, str]] = []
    pos = 0
    match = _NODE_ID.match(line, pos)
    if not match:
        return [], []
    label, pos = _scan_label(line, match.end())
    node_class, pos = _scan_class(line, pos)
    nodes.append((match.group("id"), label, node_class))

    while pos < len(line):
        connector = _CONNECTOR.match(line, pos)
        if not connector:
            break
        pos = connector.end()
        match = _NODE_ID.match(line, pos)
        if not match:
            break
        label, pos = _scan_label(line, match.end())
        node_class, pos = _scan_class(line, pos)
        edge_label = (
            connector.group("pipelabel")
            or connector.group("dashlabel")
            or connector.group("eqlabel")
            or ""
        )
        links.append((len(nodes) - 1, edge_label.strip()))
        nodes.append((match.group("id"), label, node_class))
    return nodes, links


# Styled to match how mermaid itself draws a flowchart, because the same report
# is read as markdown (where mermaid renders for real) and as PDF (where it
# cannot, so this stands in). Colours are mermaid's default theme; boxes size to
# their text and the chain centres, as mermaid lays them out; arrows are drawn
# rather than typed, since a "→" glyph next to a real diagram looks like a
# fallback. The report templates only ever emit a single linear chain, which is
# exactly the case CSS can reproduce faithfully.
DIAGRAM_CSS = """
.mmd{margin:14px 0}
/* nowrap + shrinkable nodes: a wrapped row would leave the last box stretched
   across its own line, which reads worse than slightly narrower boxes. */
.mmd-row{display:flex;align-items:stretch;flex-wrap:nowrap;margin:6px 0;
  justify-content:center}
.mmd-col{display:flex;flex-direction:column;align-items:center;margin:6px 0}
.mmd-node{border:1px solid #9370DB;border-radius:5px;background:#ECECFF;
  color:#111;padding:8px 14px;text-align:center;font-size:9.5pt;line-height:1.35;
  flex:0 1 auto;min-width:0;overflow-wrap:break-word}
.mmd-col .mmd-node{flex:0 0 auto;min-width:150px;font-size:10pt}
/* Shaft + head built from pseudo-elements: no image, no external SVG, and
   WeasyPrint renders border-triangles exactly. */
.mmd-arrow{flex:0 0 auto;align-self:center;position:relative;
  width:36px;min-height:14px;text-align:center}
.mmd-arrow::before{content:"";position:absolute;left:2px;top:6px;
  width:24px;height:2px;background:#333}
.mmd-arrow::after{content:"";position:absolute;left:26px;top:1px;
  border-top:6px solid transparent;border-bottom:6px solid transparent;
  border-left:9px solid #333}
.mmd-col .mmd-arrow{width:14px;min-height:36px}
.mmd-col .mmd-arrow::before{left:6px;top:2px;width:2px;height:24px}
.mmd-col .mmd-arrow::after{left:1px;top:26px;border-left:6px solid transparent;
  border-right:6px solid transparent;border-top:9px solid #333;border-bottom:0}
/* Edge labels (A -->|Thanh toán| B) are not in any template yet but the parser
   emits them, so they keep a visible slot instead of vanishing: the label sits
   above the arrow, which drops to the bottom of the box. */
.mmd-arrow-labelled{width:auto;min-width:64px;max-width:110px;padding:0 4px 14px}
.mmd-arrow-labelled::before{left:50%;margin-left:-13px;top:auto;bottom:6px}
.mmd-arrow-labelled::after{left:50%;margin-left:11px;top:auto;bottom:1px}
.mmd-col .mmd-arrow-labelled{width:auto;min-width:0;padding:0 0 0 14px}
.mmd-col .mmd-arrow-labelled::before{left:6px;margin-left:0;top:2px;bottom:auto}
.mmd-col .mmd-arrow-labelled::after{left:1px;margin-left:0;top:26px;bottom:auto}
.mmd-elabel{display:block;font-size:8pt;color:#456;line-height:1.2;
  text-align:center;overflow-wrap:break-word}
/* Hub-and-spokes: one company box beside a stacked column of its partners, the
   shape mermaid draws for the partner diagrams in sections 4 and 5. */
.mmd-fan{display:flex;align-items:center;justify-content:center;margin:6px 0}
.mmd-fan-hub{flex:0 1 auto;min-width:0}
.mmd-fan-spokes{display:flex;flex-direction:column;flex:0 1 auto;min-width:0;
  align-items:stretch}
.mmd-fan-row{display:flex;align-items:center;margin:3px 0;flex-wrap:nowrap}
.mmd-fan-row .mmd-node{flex:0 1 auto}
/* Rows stretch to the widest spoke, so pinning them to the hub side lines the
   arrowheads up on one axis instead of leaving a ragged gap. */
.mmd-fan-out .mmd-fan-row{justify-content:flex-start}
.mmd-fan-in .mmd-fan-row{justify-content:flex-end}
"""


def _label_html(text: str) -> str:
    """Escape label text, keeping mermaid's <br/> line breaks.

    Shared by node and edge labels so the two behave identically — edge labels
    used to be escaped wholesale, which printed a literal "<br/>" where a node
    in the same diagram would have broken the line.
    """

    parts = [part.strip() for part in re.split(r"<br\s*/?>", text) if part.strip()]
    return "<br>".join(html.escape(part) for part in parts)


def _clean_label(raw: str | None, fallback: str) -> str:
    """Strip mermaid node brackets and quotes, keeping the visible text."""

    if not raw:
        return fallback
    text = raw.strip()
    text = re.sub(r"^[\[\({]+", "", text)
    text = re.sub(r"[\]\)}]+$", "", text)
    text = text.strip().strip('"').strip("'").strip()
    return _label_html(text) or fallback


@dataclass(frozen=True)
class Flowchart:
    """A parsed mermaid flowchart.

    A dataclass rather than a widening tuple: styling and grouping took the
    parse result past the point where positional unpacking stays readable.
    """

    direction: str
    order: list[str]
    labels: dict[str, str]
    edges: list[tuple[str, str, str]]
    # node id -> style, resolved from classDef/class/:::/style declarations.
    node_style: dict[str, dict[str, str]]
    # (title, member node ids) for each subgraph, in declaration order.
    groups: list[tuple[str, list[str]]]

    @property
    def vertical(self) -> bool:
        return self.direction in {"TD", "TB", "BT"}


def _parse_style_pairs(text: str) -> dict[str, str]:
    """Read mermaid's "fill:#eee,stroke:#333" style body into a dict."""

    style: dict[str, str] = {}
    for chunk in text.split(","):
        key, _, value = chunk.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key in {"fill", "stroke", "color"} and value:
            style[key] = value
    return style


def _parse(source: str) -> Flowchart:
    """Parse one mermaid flowchart body."""

    direction = "LR"
    labels: dict[str, str] = {}
    order: list[str] = []
    edges: list[tuple[str, str, str]] = []
    class_styles: dict[str, dict[str, str]] = {}
    node_classes: dict[str, str] = {}
    node_style: dict[str, dict[str, str]] = {}
    groups: list[tuple[str, list[str]]] = []
    group_stack: list[tuple[str, list[str]]] = []

    def remember(node_id: str, raw_label: str | None) -> None:
        if node_id not in labels:
            labels[node_id] = _clean_label(raw_label, html.escape(node_id))
            order.append(node_id)
        elif raw_label:
            labels[node_id] = _clean_label(raw_label, labels[node_id])
        for _, members in group_stack:
            if node_id not in members:
                members.append(node_id)

    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        header = _HEADER.match(stripped)
        if header:
            direction = header.group(1).upper()
            continue

        subgraph = _SUBGRAPH.match(stripped)
        if subgraph:
            raw_title = subgraph.group("title") or subgraph.group("plain") or ""
            group_stack.append((_label_html(raw_title.strip().strip('"')), []))
            continue
        if stripped == "end":
            if group_stack:
                groups.append(group_stack.pop())
            continue

        class_def = _CLASSDEF.match(stripped)
        if class_def:
            class_styles[class_def.group("name")] = _parse_style_pairs(
                class_def.group("body")
            )
            continue
        class_apply = _CLASS_APPLY.match(stripped)
        if class_apply:
            for node_id in class_apply.group("ids").split(","):
                node_classes[node_id.strip()] = class_apply.group("name")
            continue
        node_style_decl = _STYLE_DECL.match(stripped)
        if node_style_decl:
            node_style[node_style_decl.group("id")] = _parse_style_pairs(
                node_style_decl.group("body")
            )
            continue
        if stripped.startswith("linkStyle"):
            continue

        line_nodes, line_links = _scan_line(stripped)
        if line_links:
            for node_id, raw_label, node_class in line_nodes:
                remember(node_id, raw_label)
                if node_class:
                    node_classes[node_id] = node_class
            for index, edge_label in line_links:
                edges.append(
                    (line_nodes[index][0], edge_label, line_nodes[index + 1][0])
                )
            continue

        line_nodes, _ = _scan_line(stripped)
        if len(line_nodes) == 1 and (line_nodes[0][1] or line_nodes[0][2]):
            node_id, raw_label, node_class = line_nodes[0]
            remember(node_id, raw_label)
            if node_class:
                node_classes[node_id] = node_class

    # Unclosed subgraphs still count: a missing "end" should not discard the
    # grouping the author clearly intended.
    while group_stack:
        groups.append(group_stack.pop())

    for node_id, class_name in node_classes.items():
        style = class_styles.get(class_name)
        if style and node_id not in node_style:
            node_style[node_id] = style

    return Flowchart(
        direction=direction,
        order=order,
        labels=labels,
        edges=edges,
        node_style=node_style,
        groups=[(title, members) for title, members in groups if members],
    )


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


def _fan(
    order: list[str],
    edges: list[tuple[str, str, str]],
) -> tuple[str, str, list[tuple[str, str]]] | None:
    """Detect a hub joined to several spokes, as ("out"|"in", hub, spokes).

    The partner diagrams draw one company against its customers or suppliers,
    each edge carrying that partner's share. Mermaid draws the hub once with the
    edges fanning out; rendering an edge per row instead would repeat the
    company's name on every line, which is precisely the mismatch with the
    markdown view this module exists to avoid.
    """

    if len(edges) < 2:
        return None
    sources = {src for src, _, _ in edges}
    targets = {dst for _, _, dst in edges}
    if len(sources) == 1 and len(targets) == len(edges):
        hub = next(iter(sources))
        if hub in targets:
            return None
        spokes = [(label, dst) for _, label, dst in edges]
        return "out", hub, spokes
    if len(targets) == 1 and len(sources) == len(edges):
        hub = next(iter(targets))
        if hub in sources:
            return None
        spokes = [(label, src) for src, label, _ in edges]
        return "in", hub, spokes
    return None


def _fan_html(
    kind: str,
    hub_label: str,
    spokes: list[tuple[str, str]],
) -> str:
    """One hub box beside a stacked column of spokes, arrows in between."""

    rows = "".join(
        '<div class="mmd-fan-row">'
        + (
            _arrow_html(label, False) + _node_html(spoke_label)
            if kind == "out"
            else _node_html(spoke_label) + _arrow_html(label, False)
        )
        + "</div>"
        for label, spoke_label in spokes
    )
    hub = f'<div class="mmd-fan-hub">{_node_html(hub_label)}</div>'
    spoke_column = f'<div class="mmd-fan-spokes">{rows}</div>'
    body = hub + spoke_column if kind == "out" else spoke_column + hub
    # The direction is carried as a class so the CSS can push every arrow flush
    # against the hub: spoke boxes differ in width, and without it the arrows
    # stop at ragged positions with a gap before the hub.
    return (
        f'<div class="mmd"><div class="mmd-fan mmd-fan-{kind}">{body}</div></div>'
    )


def _node_html(label: str) -> str:
    return f'<div class="mmd-node">{label}</div>'


def _arrow_html(label: str, vertical: bool) -> str:
    """One arrow between two nodes.

    The arrow itself is drawn in CSS (see DIAGRAM_CSS), so no glyph goes in the
    markup — emitting one too would stack a character on top of the drawn
    shape. Labelled arrows carry their own class rather than relying on a
    :has() selector, which keeps the styling independent of how much of
    Selectors Level 4 the PDF renderer implements.
    """

    if not label:
        return '<div class="mmd-arrow"></div>'
    return (
        '<div class="mmd-arrow mmd-arrow-labelled">'
        f'<span class="mmd-elabel">{_label_html(label)}</span>'
        "</div>"
    )


def _render(source: str) -> str | None:
    """Render one mermaid flowchart as HTML, or None if it is not parseable."""

    chart = _parse(source)
    if not chart.labels:
        return None
    order, labels, edges = chart.order, chart.labels, chart.edges
    vertical = chart.vertical

    chain = _linear_chain(order, edges)
    if chain is not None:
        parts = [_node_html(labels[chain[0][0]])]
        for _, label, dst in chain:
            parts.append(_arrow_html(label, vertical))
            parts.append(_node_html(labels[dst]))
        container = "mmd-col" if vertical else "mmd-row"
        return f'<div class="mmd"><div class="{container}">{"".join(parts)}</div></div>'

    fan = _fan(order, edges)
    if fan is not None:
        kind, hub, spokes = fan
        return _fan_html(
            kind,
            labels[hub],
            [(label, labels[node]) for label, node in spokes],
        )

    if not edges:
        # Nodes only: show them as a single row of boxes.
        boxes = "".join(_node_html(labels[node]) for node in order)
        return f'<div class="mmd"><div class="mmd-row">{boxes}</div></div>'

    # Anything wider than a chain or a single fan goes to the layered SVG
    # drawer. Flex-box has no answer for it: the fallback below repeats a hub's
    # name once per edge, which is the mismatch with the markdown view that this
    # module exists to prevent.
    svg = render_svg(chart)
    if svg is not None:
        return svg

    # Last resort, if the layout could not run at all: one row per edge. Ugly,
    # but it still shows every node and every connection.
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
