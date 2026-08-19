"""Lay out a multi-level flowchart and draw it as inline SVG.

The CSS renderer in ``diagrams.py`` covers a single chain and a one-level fan.
Anything wider — a supply chain running suppliers → company → buyers over
several levels — has no sensible flex-box form: it degrades to one row per edge,
repeating the hub's name on every line.

SVG is the way out. WeasyPrint draws inline SVG properly (rounded boxes, fills,
elbowed connectors, arrowheads), so a real diagram reaches the PDF without
pulling Node and a headless browser into the project just to run mermaid.

What this does is the classic layered-graph recipe: assign each node a level,
order the nodes inside each level to keep connectors from crossing, place them,
then route orthogonal edges. It is not a general graph drawer — it assumes the
graph flows in one direction, which is what the report templates produce.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# Geometry, in SVG user units (the whole drawing is scaled to the page later).
NODE_MIN_WIDTH = 96
NODE_MAX_WIDTH = 190
NODE_PADDING_X = 10
NODE_PADDING_Y = 8
LINE_HEIGHT = 17
# SVG user units are CSS px, and WeasyPrint prints them at 72/96 — so a size
# here is 0.75 of what lands on the page. Measured, not assumed: a 20px label
# came out of the PDF at exactly 15.00pt.
PX_TO_PT = 0.75
# 13.5px = 10.1pt against 9.5pt body text: the boxes read a touch larger than
# the prose, which is what a diagram wants. Reliable only because a wrapped
# chain now lays out into the page width instead of being scaled down to reach
# it — before that, this constant was multiplied by an 0.45 fit factor and the
# nine-box supply chain printed at 3.5pt.
FONT_SIZE = 13.5
EDGE_FONT_SIZE = 10.5
RANK_GAP = 74
# Top-down charts need less room between levels: a horizontal gap has to fit an
# edge label *beside* the connector, a vertical one only above and below it.
VERTICAL_RANK_GAP = 46
# What a level needs when nothing is written between it and the next one: room
# for the connector and its arrowhead, no more. RANK_GAP is sized for an edge
# label sitting beside the connector, and on a chain with no labels at all those
# 74s were 36% of the drawing's width — spent on space for text that does not
# exist, and paid for by shrinking the text that does.
BARE_RANK_GAP = 30
BARE_VERTICAL_RANK_GAP = 26
# Vertical room between wrapped rows: enough for the connector to drop out of
# one row, run back to the left and arrive on top of the next.
WRAP_ROW_GAP = 42
NODE_GAP = 14
MARGIN = 8

# No font metrics library is available, so text width is estimated from the
# character count. This is the known-imprecise part of the layout: boxes end up
# slightly wide or slightly narrow rather than hugging the text.
CHAR_WIDTH = FONT_SIZE * 0.52
EDGE_CHAR_WIDTH = EDGE_FONT_SIZE * 0.52
MAX_CHARS_PER_LINE = int((NODE_MAX_WIDTH - 2 * NODE_PADDING_X) / CHAR_WIDTH)

# Usable width of the report page: A4 (210mm) less the 11/9mm margins in
# REPORT_CSS, at 96dpi. The drawing is scaled to this at generation time rather
# than left to CSS max-width — WeasyPrint scales such an SVG's box but not its
# contents, which paints the diagram twice on one page.
PAGE_CONTENT_WIDTH = 726.0
# Below this the labels stop being readable, so the caller is told the diagram
# needs splitting rather than being handed an unreadable picture. In px, like
# every size here: 8px is 6pt on the page.
MIN_READABLE_FONT = 8.0

# The report's own palette (report_style.py) rather than mermaid's purple
# defaults, which made every diagram look pasted in from another document. The
# blue is the same accent the blockquote rule and footnote links already use,
# and the connector grey matches the chart axes in charts.py.
DEFAULT_FILL = "#f2f7fb"
DEFAULT_STROKE = "#2f6f9f"
DEFAULT_TEXT = "#1f2a33"
EDGE_COLOUR = "#5c7285"
GROUP_STROKE = "#b9c6d1"


@dataclass
class _Node:
    node_id: str
    lines: list[str]
    fill: str
    stroke: str
    colour: str
    rank: int = 0
    order: float = 0.0
    # Which wrapped row this node sits in; 0 for every diagram that fits on one.
    row: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2


@dataclass
class _Edge:
    src: str
    dst: str
    label: str
    lines: list[str] = field(default_factory=list)


def _text_lines(label_html: str) -> list[str]:
    """Turn a node label back into plain text lines, wrapping long ones.

    Labels arrive HTML-escaped with <br> separators because the CSS renderer
    consumes them that way; SVG needs the raw characters instead.
    """

    raw = [html.unescape(part) for part in re.split(r"<br\s*/?>", label_html)]
    lines: list[str] = []
    for part in raw:
        part = part.strip()
        if not part:
            continue
        while len(part) > MAX_CHARS_PER_LINE:
            cut = part.rfind(" ", 0, MAX_CHARS_PER_LINE)
            if cut <= 0:
                cut = MAX_CHARS_PER_LINE
            lines.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            lines.append(part)
    return lines or [""]


def _assign_ranks(
    node_ids: list[str],
    edges: list[_Edge],
) -> dict[str, int]:
    """Longest-path layering, ignoring edges that would close a cycle.

    A cycle in the input must not hang the PDF export, and an LLM can certainly
    write one, so back edges are dropped rather than trusted.
    """

    outgoing: dict[str, list[str]] = {node: [] for node in node_ids}
    for edge in edges:
        if edge.src in outgoing and edge.dst in outgoing:
            outgoing[edge.src].append(edge.dst)

    rank = {node: 0 for node in node_ids}
    # Depth-first longest path with an explicit on-stack set: revisiting a node
    # already on the current path means a cycle, and that edge is skipped.
    state: dict[str, int] = {}  # 0 = unvisited, 1 = on stack, 2 = done

    def visit(node: str) -> int:
        if state.get(node) == 1:
            return 0
        if state.get(node) == 2:
            return rank[node]
        state[node] = 1
        depth = 0
        for target in outgoing[node]:
            depth = max(depth, visit(target) + 1)
        state[node] = 2
        rank[node] = depth
        return depth

    for node in node_ids:
        visit(node)

    # visit() measured distance to a sink; flip it so rank 0 is a source, which
    # is what "left to right" means to a reader.
    deepest = max(rank.values(), default=0)
    return {node: deepest - value for node, value in rank.items()}


def _order_within_ranks(
    ranks: dict[str, list[str]],
    edges: list[_Edge],
    nodes: dict[str, _Node],
) -> None:
    """Median heuristic, a few sweeps, to keep connectors from crossing."""

    predecessors: dict[str, list[str]] = {node: [] for node in nodes}
    successors: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        if edge.src in nodes and edge.dst in nodes:
            predecessors[edge.dst].append(edge.src)
            successors[edge.src].append(edge.dst)

    for rank_nodes in ranks.values():
        for position, node_id in enumerate(rank_nodes):
            nodes[node_id].order = float(position)

    def median(neighbours: list[str]) -> float | None:
        positions = sorted(nodes[n].order for n in neighbours if n in nodes)
        if not positions:
            return None
        middle = len(positions) // 2
        if len(positions) % 2:
            return positions[middle]
        return (positions[middle - 1] + positions[middle]) / 2

    rank_keys = sorted(ranks)
    for sweep in range(4):
        keys = rank_keys if sweep % 2 == 0 else list(reversed(rank_keys))
        for key in keys:
            neighbours = predecessors if sweep % 2 == 0 else successors
            for node_id in ranks[key]:
                value = median(neighbours[node_id])
                if value is not None:
                    nodes[node_id].order = value
            ranks[key].sort(key=lambda n: nodes[n].order)
            for position, node_id in enumerate(ranks[key]):
                nodes[node_id].order = float(position)


def _labelled_ranks(edges: list["_Edge"], nodes: dict[str, _Node]) -> set[int]:
    """Levels followed by a gap that has to hold an edge label."""

    return {
        nodes[edge.src].rank
        for edge in edges
        if edge.lines and edge.src in nodes
    }


def _place(
    ranks: dict[int, list[str]],
    nodes: dict[str, _Node],
    vertical: bool = False,
    labelled: set[int] | None = None,
) -> tuple[float, float]:
    """Assign coordinates and return the drawing size.

    The two directions are the same layout with the axes swapped: levels advance
    along one axis, the nodes of a level spread along the other, and each level
    is centred against the largest one so the diagram reads balanced instead of
    hanging off one edge.

    The gap after a level is wide only when an edge leaving it carries a label.
    """

    for node in nodes.values():
        width = max(len(line) for line in node.lines) * CHAR_WIDTH + 2 * NODE_PADDING_X
        node.width = min(NODE_MAX_WIDTH, max(NODE_MIN_WIDTH, width))
        node.height = len(node.lines) * LINE_HEIGHT + 2 * NODE_PADDING_Y

    # Extent of each level along the cross axis.
    spans: dict[int, float] = {}
    for key, rank_nodes in ranks.items():
        sizes = [nodes[n].width if vertical else nodes[n].height for n in rank_nodes]
        spans[key] = sum(sizes) + NODE_GAP * max(0, len(rank_nodes) - 1)
    widest = max(spans.values(), default=0.0)

    labelled = labelled or set()
    wide_gap = VERTICAL_RANK_GAP if vertical else RANK_GAP
    bare_gap = BARE_VERTICAL_RANK_GAP if vertical else BARE_RANK_GAP

    def gap_after(rank_key: int) -> float:
        return wide_gap if rank_key in labelled else bare_gap

    along = MARGIN
    last_gap = bare_gap
    for key in sorted(ranks):
        rank_nodes = ranks[key]
        thickness = max(
            nodes[n].height if vertical else nodes[n].width for n in rank_nodes
        )
        across = MARGIN + (widest - spans[key]) / 2
        for node_id in rank_nodes:
            node = nodes[node_id]
            if vertical:
                node.x = across
                node.y = along + (thickness - node.height) / 2
                across += node.width + NODE_GAP
            else:
                node.x = along + (thickness - node.width) / 2
                node.y = across
                across += node.height + NODE_GAP
        last_gap = gap_after(key)
        along += thickness + last_gap

    extent = along - last_gap + MARGIN
    if vertical:
        return widest + 2 * MARGIN, extent
    return extent, widest + 2 * MARGIN


def _is_linear_chain(nodes: dict[str, _Node], edges: list["_Edge"]) -> bool:
    """True when the diagram is one unbranched run of boxes.

    Only this shape can be wrapped onto a second row without the picture losing
    its meaning: there is exactly one path through it, so a reader who reaches
    the end of a row has only one place to continue. A branching diagram wrapped
    the same way would put siblings on different rows and imply an order between
    them that the data does not have.
    """

    if not edges:
        return False
    out_count: dict[str, int] = {}
    in_count: dict[str, int] = {}
    for edge in edges:
        out_count[edge.src] = out_count.get(edge.src, 0) + 1
        in_count[edge.dst] = in_count.get(edge.dst, 0) + 1
    return (
        len(edges) == len(nodes) - 1
        and all(count <= 1 for count in out_count.values())
        and all(count <= 1 for count in in_count.values())
    )


def _place_wrapped(
    order: list[str],
    nodes: dict[str, _Node],
    budget: float,
) -> tuple[float, float]:
    """Lay a chain out over as many rows as the page width needs.

    This is the chart module's contract applied to flowcharts: the page width is
    fixed and the content arranges itself into it, rather than the drawing being
    scaled down until it fits. charts.py packs more months into the same width by
    moving points closer together; a chain does it by starting a new row.

    Before this, the nine-box supply chain in Business Activity's section 1 came
    out 1,649px wide, was scaled to 45% to reach the page, and printed at 4.7pt
    against 9.5pt body text.
    """

    rows: list[list[str]] = []
    current: list[str] = []
    used = 0.0
    for node_id in order:
        width = nodes[node_id].width
        addition = width if not current else width + BARE_RANK_GAP
        if current and used + addition > budget:
            rows.append(current)
            current, used = [node_id], width
        else:
            current.append(node_id)
            used += addition
    if current:
        rows.append(current)

    y = MARGIN
    widest = 0.0
    for row_index, row in enumerate(rows):
        height = max(nodes[n].height for n in row)
        x = MARGIN
        for node_id in row:
            node = nodes[node_id]
            node.row = row_index
            node.x = x
            node.y = y + (height - node.height) / 2
            x += node.width + BARE_RANK_GAP
        widest = max(widest, x - BARE_RANK_GAP)
        y += height + WRAP_ROW_GAP

    return widest + MARGIN, y - WRAP_ROW_GAP + MARGIN


def _edge_ends(
    src: _Node,
    dst: _Node,
    vertical: bool,
) -> tuple[float, float, float, float]:
    """Where a connector leaves the source and meets the target."""

    if vertical:
        return src.cx, src.y + src.height, dst.cx, dst.y
    return src.x + src.width, src.cy, dst.x, dst.cy


def _wrap_edge_path(src: _Node, dst: _Node) -> str:
    """Connector from the end of one wrapped row to the start of the next.

    Leaves the source downwards and arrives on top of the target, so the arrow
    reads as "continue below" rather than as a link back up the chain. Direction
    matters more here than anywhere else in these diagrams: section 1 is a supply
    chain, and its guidance is explicit that inputs must not appear to flow from
    the output end.
    """

    mid_y = src.y + src.height + (dst.y - (src.y + src.height)) / 2
    return (
        f"M{src.cx:.1f} {src.y + src.height:.1f} "
        f"L{src.cx:.1f} {mid_y:.1f} "
        f"L{dst.cx:.1f} {mid_y:.1f} "
        f"L{dst.cx:.1f} {dst.y:.1f}"
    )


def _edge_path(src: _Node, dst: _Node, vertical: bool = False) -> str:
    """Orthogonal connector: out of the source, across, into the target."""

    if src.row != dst.row:
        return _wrap_edge_path(src, dst)

    x1, y1, x2, y2 = _edge_ends(src, dst, vertical)
    if vertical:
        if abs(x1 - x2) < 0.5:
            return f"M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}"
        mid = y1 + (y2 - y1) / 2
        return (
            f"M{x1:.1f} {y1:.1f} L{x1:.1f} {mid:.1f} "
            f"L{x2:.1f} {mid:.1f} L{x2:.1f} {y2:.1f}"
        )
    if abs(y1 - y2) < 0.5:
        return f"M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}"
    mid = x1 + (x2 - x1) / 2
    return (
        f"M{x1:.1f} {y1:.1f} L{mid:.1f} {y1:.1f} "
        f"L{mid:.1f} {y2:.1f} L{x2:.1f} {y2:.1f}"
    )


def render_svg(chart) -> str | None:
    """Draw a parsed flowchart as inline SVG, or None if it has no edges."""

    if not chart.edges:
        return None

    nodes: dict[str, _Node] = {}
    for node_id in chart.order:
        style = chart.node_style.get(node_id, {})
        nodes[node_id] = _Node(
            node_id=node_id,
            lines=_text_lines(chart.labels[node_id]),
            fill=style.get("fill", DEFAULT_FILL),
            stroke=style.get("stroke", DEFAULT_STROKE),
            colour=style.get("color", DEFAULT_TEXT),
        )

    edges = [
        _Edge(src, dst, label, _text_lines(label) if label else [])
        for src, label, dst in chart.edges
        if src in nodes and dst in nodes
    ]
    if not edges:
        return None

    rank_of = _assign_ranks(list(nodes), edges)
    for node_id, rank in rank_of.items():
        nodes[node_id].rank = rank
    ranks: dict[int, list[str]] = {}
    for node_id in chart.order:
        ranks.setdefault(nodes[node_id].rank, []).append(node_id)

    _order_within_ranks(ranks, edges, nodes)
    # "flowchart TD" must come out top-down here too. Drawn left-to-right it
    # would contradict the markdown view of the same report, which is the exact
    # mismatch this renderer exists to prevent.
    vertical = bool(getattr(chart, "vertical", False))
    labelled = _labelled_ranks(edges, nodes)
    width, height = _place(ranks, nodes, vertical, labelled)
    # Too wide for the page, and shaped so that wrapping keeps its meaning: lay
    # it out into the page width instead of shrinking it to reach the page.
    if (
        not vertical
        and width > PAGE_CONTENT_WIDTH
        and not labelled
        and _is_linear_chain(nodes, edges)
    ):
        width, height = _place_wrapped(chart.order, nodes, PAGE_CONTENT_WIDTH - 2 * MARGIN)

    out_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    in_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    for edge in edges:
        out_degree[edge.src] += 1
        in_degree[edge.dst] += 1

    # Scale here, not in CSS. Explicit width AND height attributes too, since
    # width="100%" with height:auto makes WeasyPrint compute zero height and
    # draw nothing at all.
    scale = min(1.0, PAGE_CONTENT_WIDTH / width) if width else 1.0
    draw_width, draw_height = width * scale, height * scale

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{draw_width:.0f}" height="{draw_height:.0f}" '
        f'font-family="inherit" role="img">',
        '<defs><marker id="mmdarrow" markerWidth="8" markerHeight="6" refX="8" '
        'refY="3" orient="auto" markerUnits="userSpaceOnUse">'
        f'<polygon points="0 0, 8 3, 0 6" fill="{EDGE_COLOUR}"/></marker></defs>',
    ]

    for edge in edges:
        src, dst = nodes[edge.src], nodes[edge.dst]
        parts.append(
            f'<path d="{_edge_path(src, dst, vertical)}" fill="none" '
            f'stroke="{EDGE_COLOUR}" stroke-width="1.1" '
            f'marker-end="url(#mmdarrow)"/>'
        )
        if edge.lines:
            x1, y1, x2, y2 = _edge_ends(src, dst, vertical)
            if vertical:
                # Beside the connector, not on it: a top-down connector is
                # vertical, so anything centred on it lands on the line.
                label_x = (x1 + x2) / 2 + 4
                label_y = (y1 + y2) / 2
                anchor = "start"
            elif in_degree[edge.dst] > 1 and out_degree[edge.src] <= 1:
                # Put the label at whichever end of the connector fans out,
                # because that is the end where the edges are far apart. Labels
                # bunched at the shared end land on top of each other and on the
                # connector bundle: seven suppliers feeding one node share a
                # bend exactly.
                label_x, label_y, anchor = x1 + 5, y1 - 4, "start"
            elif out_degree[edge.src] > 1 and in_degree[edge.dst] <= 1:
                label_x, label_y, anchor = x2 - 5, y2 - 4, "end"
            else:
                label_x = x1 + (x2 - x1) / 2
                label_y = (y1 + y2) / 2 if abs(y1 - y2) > 0.5 else y1 - 4
                anchor = "middle"
            label_y -= (len(edge.lines) - 1) * (EDGE_FONT_SIZE + 1)
            for index, line in enumerate(edge.lines):
                parts.append(
                    f'<text x="{label_x:.1f}" '
                    f'y="{label_y + index * (EDGE_FONT_SIZE + 1):.1f}" '
                    f'font-size="{EDGE_FONT_SIZE}" fill="#445566" '
                    f'text-anchor="{anchor}">{html.escape(line)}</text>'
                )

    for node in nodes.values():
        parts.append(
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="6" fill="{node.fill}" '
            f'stroke="{node.stroke}" stroke-width="0.9"/>'
        )
        first = node.y + NODE_PADDING_Y + LINE_HEIGHT * 0.78
        for index, line in enumerate(node.lines):
            parts.append(
                f'<text x="{node.cx:.1f}" y="{first + index * LINE_HEIGHT:.1f}" '
                f'font-size="{FONT_SIZE}" fill="{node.colour}" '
                f'text-anchor="middle">{html.escape(line)}</text>'
            )

    parts.append("</svg>")
    # Fitting the page always wins over legibility, because clipping would drop
    # content outright. A chain that is too wide now wraps instead of shrinking,
    # so reaching this point means a *branching* diagram too wide for A4.
    #
    # Said on the page rather than in an HTML comment. The comment version was
    # written to "trace a rendered PDF back to the cause" and did the opposite:
    # the reader saw 4.7pt text with no explanation, and nobody writing the
    # report ever saw the comment at all. Same rule as every other guard here —
    # a check that fires silently is a check nobody acts on.
    note = ""
    if FONT_SIZE * scale < MIN_READABLE_FONT:
        note = (
            f'<p class="mmd-note">Sơ đồ đã thu nhỏ còn {scale * 100:.0f}% để vừa '
            f"khổ giấy, chữ nhỏ hơn mức đọc thoải mái. Sơ đồ có {len(ranks)} tầng "
            f"và nhiều nhánh — tách thành hai sơ đồ sẽ đọc được.</p>"
        )
    return f'<div class="mmd mmd-svg">{"".join(parts)}{note}</div>'
