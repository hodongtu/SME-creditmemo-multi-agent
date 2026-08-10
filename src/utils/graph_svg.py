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
LINE_HEIGHT = 13
FONT_SIZE = 10.5
EDGE_FONT_SIZE = 8.5
RANK_GAP = 74
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
# needs splitting rather than being handed an unreadable picture.
MIN_READABLE_FONT = 6.0

DEFAULT_FILL = "#ECECFF"
DEFAULT_STROKE = "#9370DB"
DEFAULT_TEXT = "#111111"
EDGE_COLOUR = "#333333"
GROUP_STROKE = "#999999"


@dataclass
class _Node:
    node_id: str
    lines: list[str]
    fill: str
    stroke: str
    colour: str
    rank: int = 0
    order: float = 0.0
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


def _place(ranks: dict[int, list[str]], nodes: dict[str, _Node]) -> tuple[float, float]:
    """Assign coordinates and return the drawing size."""

    for node in nodes.values():
        width = max(len(line) for line in node.lines) * CHAR_WIDTH + 2 * NODE_PADDING_X
        node.width = min(NODE_MAX_WIDTH, max(NODE_MIN_WIDTH, width))
        node.height = len(node.lines) * LINE_HEIGHT + 2 * NODE_PADDING_Y

    column_heights: dict[int, float] = {}
    for key, rank_nodes in ranks.items():
        column_heights[key] = (
            sum(nodes[n].height for n in rank_nodes)
            + NODE_GAP * max(0, len(rank_nodes) - 1)
        )
    tallest = max(column_heights.values(), default=0.0)

    x = MARGIN
    for key in sorted(ranks):
        rank_nodes = ranks[key]
        column_width = max(nodes[n].width for n in rank_nodes)
        # Each column is centred against the tallest one, which reads as a
        # balanced diagram instead of everything hanging off the top edge.
        y = MARGIN + (tallest - column_heights[key]) / 2
        for node_id in rank_nodes:
            node = nodes[node_id]
            node.x = x + (column_width - node.width) / 2
            node.y = y
            y += node.height + NODE_GAP
        x += column_width + RANK_GAP

    width = x - RANK_GAP + MARGIN
    height = tallest + 2 * MARGIN
    return width, height


def _edge_path(src: _Node, dst: _Node) -> str:
    """Orthogonal connector: out of the source, across, into the target."""

    x1, y1 = src.x + src.width, src.cy
    x2, y2 = dst.x, dst.cy
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
    width, height = _place(ranks, nodes)

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
            f'<path d="{_edge_path(src, dst)}" fill="none" stroke="{EDGE_COLOUR}" '
            f'stroke-width="1.3" marker-end="url(#mmdarrow)"/>'
        )
        if edge.lines:
            # Put the label at whichever end of the connector fans out, because
            # that is the end where the edges are far apart. Labels bunched at
            # the shared end land on top of each other and on the connector
            # bundle: seven suppliers feeding one node share a bend exactly.
            x1, y1 = src.x + src.width, src.cy
            x2, y2 = dst.x, dst.cy
            if in_degree[edge.dst] > 1 and out_degree[edge.src] <= 1:
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
            f'height="{node.height:.1f}" rx="4" fill="{node.fill}" '
            f'stroke="{node.stroke}" stroke-width="1"/>'
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
    # content outright. When the squeeze takes the text below readable size the
    # diagram has too many levels for A4 and should be split — recorded as a
    # comment so a rendered PDF can be traced back to the cause.
    note = ""
    if FONT_SIZE * scale < MIN_READABLE_FONT:
        note = (
            f"<!-- diagram scaled to {scale * 100:.0f}%: "
            f"{len(ranks)} levels is too wide for the page, split it -->"
        )
    return f'<div class="mmd mmd-svg">{note}{"".join(parts)}</div>'
