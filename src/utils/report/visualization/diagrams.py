"""Parse Mermaid flowcharts and draw them so they survive PDF export."""

import html
import re
from dataclasses import dataclass, replace

from src.utils.report.visualization.graph_svg import render_svg


MERMAID_BLOCK = re.compile(
    r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_HEADER = re.compile(r"^\s*(?:flowchart|graph)\s+(LR|RL|TD|TB|BT)\b", re.IGNORECASE)
_CONNECTOR = re.compile(
    r"\s*(?:"
    r"-{2}\s*(?P<dashlabel>[^|>=-][^>]*?)\s*-{2,3}>"
    r"|={2}\s*(?P<eqlabel>[^|>=-][^>]*?)\s*={2,}>"
    r"|-{2,3}>|-\.->|={2,}>|-{3}"
    r")\s*(?:\|(?P<pipelabel>[^|]*)\|\s*)?"
)
_NODE_ID = re.compile(r"\s*(?P<id>[A-Za-z0-9_]+)\s*")
_OPEN_TO_CLOSE = {"[": "]", "(": ")", "{": "}"}
_CLASSDEF = re.compile(r"^classDef\s+(?P<name>[A-Za-z0-9_]+)\s+(?P<body>.+)$")
_CLASS_APPLY = re.compile(r"^class\s+(?P<ids>[A-Za-z0-9_,\s]+?)\s+(?P<name>[A-Za-z0-9_]+)\s*$")
_STYLE_DECL = re.compile(r"^style\s+(?P<id>[A-Za-z0-9_]+)\s+(?P<body>.+)$")
_INLINE_CLASS = re.compile(r"\s*:::(?P<name>[A-Za-z0-9_]+)")
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
    """Split ``A[x] -->|l| B[y]:::cls --> C`` into its nodes and the links."""

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


DIAGRAM_CSS = """
/* Diagrams and charts are emitted as bare <svg> with explicit width and height.
   Only the centring is left to CSS: a top-down flowchart is much narrower than
   the page and otherwise sits against the left margin. Sizing stays out of CSS
   on purpose — width:100% with height:auto makes WeasyPrint compute zero height
   and draw nothing. */
svg{display:block;margin:14px auto}
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
.mmd-note{font-size:8pt;color:#8A6D3B;text-align:center;margin:2px 0 0}
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
    """A parsed mermaid flowchart."""

    direction: str
    order: list[str]
    labels: dict[str, str]
    edges: list[tuple[str, str, str]]
    node_style: dict[str, dict[str, str]]
    node_shape: dict[str, str]
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
    node_shape: dict[str, str] = {}
    groups: list[tuple[str, list[str]]] = []
    group_stack: list[tuple[str, list[str]]] = []

    def remember(node_id: str, raw_label: str | None) -> None:
        if node_id not in labels:
            labels[node_id] = _clean_label(raw_label, html.escape(node_id))
            order.append(node_id)
        elif raw_label:
            labels[node_id] = _clean_label(raw_label, labels[node_id])
        if raw_label and raw_label.startswith("{{"):
            node_shape[node_id] = "hexagon"
        elif raw_label:
            node_shape.setdefault(node_id, "rect")
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
        node_shape=node_shape,
        groups=[(title, members) for title, members in groups if members],
    )


_CONCENTRATION_THRESHOLD = 40.0
_PERCENT_LABEL = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_AUTO_WARN_STYLE = {"fill": "#f7e2e5", "stroke": "#c1616f", "color": "#c1616f"}


def _auto_color_concentration(chart: Flowchart) -> Flowchart:
    """Highlight a partner node the model forgot to color despite a high-% edge."""

    degree: dict[str, int] = {}
    for src, _label, dst in chart.edges:
        degree[src] = degree.get(src, 0) + 1
        degree[dst] = degree.get(dst, 0) + 1

    node_style = dict(chart.node_style)
    changed = False
    for src, label, dst in chart.edges:
        match = _PERCENT_LABEL.search(label)
        if not match:
            continue
        value = float(match.group(1).replace(",", "."))
        if value < _CONCENTRATION_THRESHOLD:
            continue
        src_degree, dst_degree = degree.get(src, 0), degree.get(dst, 0)
        if src_degree > dst_degree:
            partner = dst
        elif dst_degree > src_degree:
            partner = src
        else:
            continue
        if node_style.get(partner) != _AUTO_WARN_STYLE:
            node_style[partner] = dict(_AUTO_WARN_STYLE)
            changed = True

    return replace(chart, node_style=node_style) if changed else chart


_BR = re.compile(r"<br\s*/?>")
_MONEY_UNIT = r"(?:tỷ|tỉ|triệu|nghìn|ngàn|tr)"
_MONEY_CUR = r"(?:đồng|đ|vnđ|vnd)"
_MONEY_BODY = (
    rf"\d[\d.,\s]*(?:{_MONEY_UNIT}\s*{_MONEY_CUR}?|{_MONEY_CUR})"
)
_MONEY_ONLY = re.compile(rf"^[\s(]*{_MONEY_BODY}[\s.,)]*$", re.IGNORECASE)
_MONEY_PAREN = re.compile(rf"\s*\(\s*{_MONEY_BODY}\s*\)", re.IGNORECASE)


def _percent_only_edge_labels(chart: Flowchart) -> Flowchart:
    """Drop the absolute figure from an edge label that already carries a %."""

    edges = []
    changed = False
    for src, label, dst in chart.edges:
        lines = _BR.split(label) if label else []
        if len(lines) > 1 and any("%" in line for line in lines):
            kept = [line for line in lines
                    if "%" in line or not _MONEY_ONLY.match(line.strip())]
            if kept and kept != lines:
                label = "<br/>".join(kept)
                changed = True
        if "%" in label:
            stripped = _MONEY_PAREN.sub("", label)
            if stripped.strip() and stripped != label:
                label = stripped
                changed = True
        edges.append((src, label, dst))

    return replace(chart, edges=edges) if changed else chart


def _node_html(label: str) -> str:
    return f'<div class="mmd-node">{label}</div>'


def _arrow_html(label: str, vertical: bool) -> str:
    """One arrow between two nodes."""

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
    chart = _percent_only_edge_labels(chart)
    chart = _auto_color_concentration(chart)
    order, labels, edges = chart.order, chart.labels, chart.edges
    if not edges:
        boxes = "".join(_node_html(labels[node]) for node in order)
        return f'<div class="mmd"><div class="mmd-row">{boxes}</div></div>'

    svg = render_svg(chart)
    if svg is not None:
        return svg

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
