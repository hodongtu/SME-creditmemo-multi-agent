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

import html
import re
from dataclasses import dataclass, field
from functools import lru_cache


NODE_MIN_WIDTH = 56
NODE_MAX_WIDTH = 190
NODE_PADDING_X = 14
NODE_PADDING_Y = 11
HEX_INSET = NODE_PADDING_X
LINE_HEIGHT = 17
PX_TO_PT = 0.75
FONT_SIZE = 13.5
EDGE_FONT_SIZE = 11.34
RANK_GAP = 92
VERTICAL_RANK_GAP = 46
BARE_RANK_GAP = 44
BARE_VERTICAL_RANK_GAP = 26
EDGE_LABEL_CLEARANCE = 13
EDGE_LABEL_MARGIN = 4.0
DESCENDER_RATIO = 0.22
ARROW_LENGTH = 8
EDGE_STROKE_WIDTH = 1.4
WRAP_ROW_GAP = 42
NODE_GAP = 14
MARGIN = 8

CHAR_WIDTH = FONT_SIZE * 0.57
EDGE_CHAR_WIDTH = EDGE_FONT_SIZE * 0.57

FONT_STACK = ("Times New Roman", "Times", "Liberation Serif", "DejaVu Serif")
FONT_MEASURE_CORRECTION = 1.0
_MEASURE_SIZE = 64
MAX_CHARS_PER_LINE = int((NODE_MAX_WIDTH - 2 * NODE_PADDING_X) / CHAR_WIDTH)

PAGE_CONTENT_WIDTH = 726.0
MIN_READABLE_FONT = 8.0

DEFAULT_FILL = "#f2f7fb"
DEFAULT_STROKE = "#2f6f9f"
DEFAULT_TEXT = "#1f2a33"
EDGE_COLOUR = "#3d4a55"
EDGE_LABEL_COLOUR = "#445566"
HIGHLIGHT_WEIGHT = "bold"
GROUP_STROKE = "#b9c6d1"
SHADOW_COLOUR = "#1f2a33"
SHADOW_OPACITY = 0.13
SHADOW_OFFSET = 1.5

LEVEL_COLOURS = (
    ("#dfeee7", "#4e9b7c"),   # inputs — green, the start of the chain
    ("#dce7f3", "#2f6f9f"),   # the report's own accent
    ("#e4e9ee", "#5c7285"),   # neutral slate for the middle of a long chain
    ("#f7eeda", "#b5852f"),   # amber, warming toward the output end
    ("#ece4f3", "#7d5ba6"),   # violet — cash in
)

@dataclass
class _Node:
    node_id: str
    lines: list[str]
    fill: str
    stroke: str
    colour: str
    shape: str = "rect"
    rank: int = 0
    order: float = 0.0
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


@lru_cache(maxsize=1)
def _measure_font():
    """The report's font at a large size, or None when none can be loaded."""

    try:
        from matplotlib import font_manager
        from PIL import ImageFont
    except Exception:
        return None
    for name in FONT_STACK:
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=name),
                fallback_to_default=False,
            )
            return ImageFont.truetype(path, size=_MEASURE_SIZE)
        except Exception:
            continue
    return None


def text_width(text: str, font_size: float) -> float:
    """How wide this text will draw, in SVG user units."""

    font = _measure_font()
    if font is None:
        return len(text) * font_size * 0.57
    return font.getlength(text) / _MEASURE_SIZE * font_size * FONT_MEASURE_CORRECTION


def _text_lines(label_html: str, max_width: float = NODE_MAX_WIDTH) -> list[str]:
    """Turn a label back into plain text lines, wrapping long ones."""

    raw = [html.unescape(part) for part in re.split(r"<br\s*/?>", label_html)]
    limit = max_width - 2 * NODE_PADDING_X
    lines: list[str] = []
    for part in raw:
        part = part.strip()
        if not part:
            continue
        current = ""
        for word in part.split():
            while text_width(word, FONT_SIZE) > limit:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], FONT_SIZE) > limit:
                    cut -= 1
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:cut])
                word = word[cut:]
            candidate = f"{current} {word}".strip()
            if current and text_width(candidate, FONT_SIZE) > limit:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)

    return lines or [""]


def _assign_ranks(
    node_ids: list[str],
    edges: list[_Edge],
) -> dict[str, int]:
    """Longest-path layering, ignoring edges that would close a cycle. """

    outgoing: dict[str, list[str]] = {node: [] for node in node_ids}
    for edge in edges:
        if edge.src in outgoing and edge.dst in outgoing:
            outgoing[edge.src].append(edge.dst)

    rank = {node: 0 for node in node_ids}
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

    deepest = max(rank.values(), default=0)
    return {node: deepest - value for node, value in rank.items()}


def _adjacency(
    nodes: dict[str, _Node],
    edges: list[_Edge],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Who feeds each node and who it feeds, keyed by node id."""

    predecessors: dict[str, list[str]] = {node: [] for node in nodes}
    successors: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        if edge.src in nodes and edge.dst in nodes:
            predecessors[edge.dst].append(edge.src)
            successors[edge.src].append(edge.dst)
    return predecessors, successors


def _order_within_ranks(
    ranks: dict[str, list[str]],
    edges: list[_Edge],
    nodes: dict[str, _Node],
) -> None:
    """Median heuristic, a few sweeps, to keep connectors from crossing."""

    predecessors, successors = _adjacency(nodes, edges)

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


def _label_gaps(
    edges: list["_Edge"],
    nodes: dict[str, _Node],
) -> dict[int, float]:
    """How much room each level needs after it for its own edge labels."""

    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    for edge in edges:
        out_degree[edge.src] = out_degree.get(edge.src, 0) + 1
        in_degree[edge.dst] = in_degree.get(edge.dst, 0) + 1

    needed: dict[int, float] = {}
    for edge in edges:
        if not edge.lines or edge.src not in nodes:
            continue
        widest = max(text_width(line, EDGE_FONT_SIZE) for line in edge.lines)
        room = widest + 2 * EDGE_LABEL_CLEARANCE + ARROW_LENGTH

        if out_degree.get(edge.src, 0) > 1 or in_degree.get(edge.dst, 0) > 1:
            room = 2 * (widest + EDGE_LABEL_CLEARANCE) + ARROW_LENGTH
        rank = nodes[edge.src].rank
        needed[rank] = max(needed.get(rank, 0.0), room)
    return needed


def _colour_by_level(chart, nodes: dict[str, _Node]) -> None:
    """Tint each node by the level it sits on, in place."""

    last = max((node.rank for node in nodes.values()), default=0)
    for node_id, node in nodes.items():
        if chart.node_style.get(node_id):
            continue

        index = round(node.rank * (len(LEVEL_COLOURS) - 1) / last) if last else 0
        node.fill, node.stroke = LEVEL_COLOURS[index]


def _align_across_ranks(
    ranks: dict[int, list[str]],
    edges: list["_Edge"],
    nodes: dict[str, _Node],
    vertical: bool,
) -> None:
    """Pull each box level with the boxes it connects to, in place."""

    predecessors, successors = _adjacency(nodes, edges)

    def size(node: _Node) -> float:
        return node.width if vertical else node.height

    def centre(node: _Node) -> float:
        return (node.x if vertical else node.y) + size(node) / 2

    def move_to(node: _Node, value: float) -> None:
        if vertical:
            node.x = value - size(node) / 2
        else:
            node.y = value - size(node) / 2

    rank_keys = sorted(ranks)
    for sweep in range(4):
        keys = rank_keys if sweep % 2 == 0 else list(reversed(rank_keys))
        neighbours = predecessors if sweep % 2 == 0 else successors
        for key in keys:
            rank_nodes = ranks[key]
            wanted = []
            for node_id in rank_nodes:
                linked = [nodes[n] for n in neighbours[node_id] if n in nodes]
                wanted.append(
                    sum(centre(n) for n in linked) / len(linked)
                    if linked
                    else centre(nodes[node_id])
                )

            placed: list[float] = []
            edge_of_previous = None
            for node_id, target in zip(rank_nodes, wanted):
                half = size(nodes[node_id]) / 2
                position = target
                if edge_of_previous is not None:
                    position = max(position, edge_of_previous + NODE_GAP + half)
                placed.append(position)
                edge_of_previous = position + half

            drift = sum(placed) / len(placed) - sum(wanted) / len(wanted)
            for node_id, position in zip(rank_nodes, placed):
                move_to(nodes[node_id], position - drift)


def _size_nodes(nodes: dict[str, _Node], max_width: float = NODE_MAX_WIDTH) -> None:
    """Size every box to its own text, with the same padding all round."""

    for node in nodes.values():
        width = max(text_width(line, FONT_SIZE) for line in node.lines) + 2 * NODE_PADDING_X
        if node.shape == "hexagon":
            width += 2 * HEX_INSET
        node.width = min(max_width, max(NODE_MIN_WIDTH, width))
        node.height = len(node.lines) * LINE_HEIGHT + 2 * NODE_PADDING_Y


def _place(  # noqa: PLR0913
    ranks: dict[int, list[str]],
    nodes: dict[str, _Node],
    vertical: bool = False,
    labelled: dict[int, float] | None = None,
    max_width: float = NODE_MAX_WIDTH,
    edges: list["_Edge"] | None = None,
) -> tuple[float, float]:
    """Assign coordinates and return the drawing size."""

    _size_nodes(nodes, max_width)

    spans: dict[int, float] = {}
    for key, rank_nodes in ranks.items():
        sizes = [nodes[n].width if vertical else nodes[n].height for n in rank_nodes]
        spans[key] = sum(sizes) + NODE_GAP * max(0, len(rank_nodes) - 1)
    widest = max(spans.values(), default=0.0)

    labelled = labelled or {}
    wide_gap = VERTICAL_RANK_GAP if vertical else RANK_GAP
    bare_gap = BARE_VERTICAL_RANK_GAP if vertical else BARE_RANK_GAP

    def gap_after(rank_key: int) -> float:
        if rank_key not in labelled:
            return bare_gap
        return max(wide_gap, labelled[rank_key])

    along = MARGIN
    last_gap = bare_gap
    for key in sorted(ranks):
        rank_nodes = ranks[key]
        thickness = max(
            nodes[n].height if vertical else nodes[n].width for n in rank_nodes
        )
        for node_id in rank_nodes:
            if vertical:
                nodes[node_id].height = thickness
            else:
                nodes[node_id].width = thickness
        across = MARGIN + (widest - spans[key]) / 2
        for node_id in rank_nodes:
            node = nodes[node_id]
            if vertical:
                node.x = across
                node.y = along + (thickness - node.height) / 2
                across += node.width + NODE_GAP
            else:
                node.x = along
                node.y = across
                across += node.height + NODE_GAP
        last_gap = gap_after(key)
        along += thickness + last_gap

    if edges:
        _align_across_ranks(ranks, edges, nodes, vertical)

    extent = along - last_gap + MARGIN
    starts = [(node.x if vertical else node.y) for node in nodes.values()]
    ends = [
        (node.x + node.width) if vertical else (node.y + node.height)
        for node in nodes.values()
    ]
    low, high = min(starts, default=MARGIN), max(ends, default=MARGIN)

    shift = MARGIN - low
    if abs(shift) > 0.01:
        for node in nodes.values():
            if vertical:
                node.x += shift
            else:
                node.y += shift
    across_extent = (high - low) + 2 * MARGIN

    if vertical:
        return across_extent, extent
    return extent, across_extent


def _is_linear_chain(nodes: dict[str, _Node], edges: list["_Edge"]) -> bool:
    """True when the diagram is one unbranched run of boxes."""

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
    """Lay a chain out over as many rows as the page width needs."""

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
    """Connector from the end of one wrapped row to the start of the next."""

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

    def _build(max_width: float):
        built: dict[str, _Node] = {}
        for node_id in chart.order:
            style = chart.node_style.get(node_id, {})
            shape = chart.node_shape.get(node_id, "rect")
            wrap_width = max_width - 2 * HEX_INSET if shape == "hexagon" else max_width
            built[node_id] = _Node(
                node_id=node_id,
                lines=_text_lines(chart.labels[node_id], wrap_width),
                fill=style.get("fill", DEFAULT_FILL),
                stroke=style.get("stroke", DEFAULT_STROKE),
                colour=style.get("color", DEFAULT_TEXT),
                shape=shape,
            )
        built_edges = [
            _Edge(src, dst, label, _text_lines(label) if label else [])
            for src, label, dst in chart.edges
            if src in built and dst in built
        ]
        return built, built_edges

    def _layout(max_width: float):
        built, built_edges = _build(max_width)
        if not built_edges:
            return None
        for node_id, rank in _assign_ranks(list(built), built_edges).items():
            built[node_id].rank = rank
        by_rank: dict[int, list[str]] = {}
        for node_id in chart.order:
            by_rank.setdefault(built[node_id].rank, []).append(node_id)
        _order_within_ranks(by_rank, built_edges, built)
        _colour_by_level(chart, built)

        is_vertical = bool(getattr(chart, "vertical", False))
        gaps = _label_gaps(built_edges, built)
        w, h = _place(by_rank, built, is_vertical, gaps, max_width, built_edges)

        if (
            not is_vertical
            and w > PAGE_CONTENT_WIDTH
            and not gaps
            and _is_linear_chain(built, built_edges)
        ):
            w, h = _place_wrapped(
                chart.order, built, PAGE_CONTENT_WIDTH - 2 * MARGIN
            )
        return built, built_edges, by_rank, is_vertical, w, h

    first = _layout(NODE_MAX_WIDTH)
    if first is None:
        return None
    nodes, edges, ranks, vertical, width, height = first

    if not vertical and width > PAGE_CONTENT_WIDTH:
        for candidate in (160, 140, 120, 100):
            if FONT_SIZE * (PAGE_CONTENT_WIDTH / width) * PX_TO_PT >= MIN_READABLE_FONT:
                break
            retry = _layout(candidate)
            if retry is None:
                break
            nodes, edges, ranks, vertical, width, height = retry

    tallest_label = max((len(edge.lines) for edge in edges), default=0)

    label_overhang = (
        (tallest_label - 1) * (EDGE_FONT_SIZE + 1) / 2 + EDGE_FONT_SIZE
        if tallest_label > 1
        else 0.0
    )

    out_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    in_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    for edge in edges:
        out_degree[edge.src] += 1
        in_degree[edge.dst] += 1

    if label_overhang:
        for node in nodes.values():
            node.y += label_overhang
        height += 2 * label_overhang

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
            f'stroke="{EDGE_COLOUR}" stroke-width="{EDGE_STROKE_WIDTH}" '
            f'marker-end="url(#mmdarrow)"/>'
        )
        if edge.lines:
            x1, y1, x2, y2 = _edge_ends(src, dst, vertical)
            if vertical:
                label_x = (x1 + x2) / 2 + 4
                label_y = (y1 + y2) / 2
                anchor = "start"
            elif in_degree[edge.dst] > 1 and out_degree[edge.src] <= 1:
                trunk = x1 + (x2 - x1) / 2
                half = max(text_width(line, EDGE_FONT_SIZE) for line in edge.lines) / 2
                label_x = min((x1 + trunk) / 2, trunk - EDGE_LABEL_MARGIN - half)
                label_y = y1
                anchor = "middle"
            elif out_degree[edge.src] > 1 and in_degree[edge.dst] <= 1:
                trunk = x1 + (x2 - x1) / 2
                half = max(text_width(line, EDGE_FONT_SIZE) for line in edge.lines) / 2
                label_x = max((trunk + x2 - ARROW_LENGTH) / 2,
                              trunk + EDGE_LABEL_MARGIN + half)
                label_y = y2
                anchor = "middle"
            else:
                label_x = x1 + (x2 - ARROW_LENGTH - x1) / 2
                label_y = y1
                anchor = "middle"
            
            block = (len(edge.lines) - 1) * (EDGE_FONT_SIZE + 1)
            label_y -= (
                block
                + EDGE_LABEL_MARGIN
                + EDGE_FONT_SIZE * DESCENDER_RATIO
                + EDGE_STROKE_WIDTH / 2
            )

            warned = chart.node_style.get(edge.dst) or chart.node_style.get(edge.src)
            label_fill = (warned or {}).get("color") or EDGE_LABEL_COLOUR
            weight = f' font-weight="{HIGHLIGHT_WEIGHT}"' if warned else ""
            for index, line in enumerate(edge.lines):
                parts.append(
                    f'<text x="{label_x:.1f}" '
                    f'y="{label_y + index * (EDGE_FONT_SIZE + 1):.1f}" '
                    f'font-size="{EDGE_FONT_SIZE}" fill="{label_fill}"{weight} '
                    f'text-anchor="{anchor}">{html.escape(line)}</text>'
                )

    def _hexagon(x: float, y: float, width: float, height: float) -> str:
        """Six points: two on the vertical mid-line, four at the flat top/bottom."""

        inset = min(HEX_INSET, width / 3)
        mid = y + height / 2
        return " ".join(f"{px:.1f},{py:.1f}" for px, py in (
            (x, mid), (x + inset, y), (x + width - inset, y),
            (x + width, mid), (x + width - inset, y + height), (x + inset, y + height),
        ))

    for node in nodes.values():
        if node.shape == "hexagon":
            parts.append(
                f'<polygon points="'
                f'{_hexagon(node.x + SHADOW_OFFSET, node.y + SHADOW_OFFSET, node.width, node.height)}" '
                f'fill="{SHADOW_COLOUR}" opacity="{SHADOW_OPACITY}"/>'
            )
            parts.append(
                f'<polygon points="{_hexagon(node.x, node.y, node.width, node.height)}" '
                f'fill="{node.fill}" stroke="{node.stroke}" stroke-width="1.2"/>'
            )
        else:
            parts.append(
                f'<rect x="{node.x + SHADOW_OFFSET:.1f}" y="{node.y + SHADOW_OFFSET:.1f}" '
                f'width="{node.width:.1f}" height="{node.height:.1f}" rx="3" '
                f'fill="{SHADOW_COLOUR}" opacity="{SHADOW_OPACITY}"/>'
            )
            parts.append(
                f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
                f'height="{node.height:.1f}" rx="3" fill="{node.fill}" '
                f'stroke="{node.stroke}" stroke-width="1.2"/>'
            )

        block = len(node.lines) * LINE_HEIGHT
        first = node.y + (node.height - block) / 2 + LINE_HEIGHT * 0.78
        weight = (f' font-weight="{HIGHLIGHT_WEIGHT}"'
                  if chart.node_style.get(node.node_id) else "")
        for index, line in enumerate(node.lines):
            parts.append(
                f'<text x="{node.cx:.1f}" y="{first + index * LINE_HEIGHT:.1f}" '
                f'font-size="{FONT_SIZE}" fill="{node.colour}"{weight} '
                f'text-anchor="middle">{html.escape(line)}</text>'
            )

    parts.append("</svg>")
    return f'<div class="mmd mmd-svg">{"".join(parts)}</div>'
