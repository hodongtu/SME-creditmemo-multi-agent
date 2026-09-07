"""Assemble a finished report: the HTML the PDF is made from, and a portable .md.

Both outputs start from the same markdown the agents produced. They differ in
what they do with the ```mermaid and ```linechart blocks in it, because a block
is source code and neither a browser nor a markdown viewer runs mermaid.js:

- ``build_report_html`` turns them into inline SVG inside a self-contained HTML
  document carrying REPORT_CSS. This is what WeasyPrint renders, so it is the
  page exactly as it prints.
- ``embed_diagrams`` turns them into ``![](data:image/svg+xml;base64,…)`` and
  leaves everything else alone, so the file is still markdown and still opens in
  any viewer — with pictures instead of source.

The second one does NOT look like the PDF and cannot: the printed appearance is
twenty-six CSS rules (type sizes, table borders, the wide-table shrink, 8pt
footnotes) and a markdown viewer applies its own stylesheet instead. What
carries over is the drawing.
"""

import base64
import re

from src.utils.report.visualization.charts import CHART_BLOCK, charts_to_html, line_chart_svg, parse_linechart
from src.utils.report.visualization.diagrams import MERMAID_BLOCK, _render, mermaid_to_html
from src.utils.report.visualization.graph_svg import FONT_STACK
from src.utils.report.visualization.report_style import REPORT_CSS, tag_wide_tables

MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "footnotes")
# Number footnotes by where they are REFERENCED, not where they are defined. The
# default does the latter, which makes a reader meet footnote 3 before footnote 1
# whenever the list is ordered any other way.
MARKDOWN_EXTENSION_CONFIGS = {"footnotes": {"USE_DEFINITION_ORDER": False}}

_SVG_TAG = re.compile(r"<svg\b.*?</svg>", re.DOTALL)
# The renderers write font-family="inherit" so a diagram picks up the report's
# type. An <img> has no parent to inherit from — it is its own document — and the
# box widths were measured against this exact stack, so a viewer's default font
# could overflow them.
_INHERIT_FONT = 'font-family="inherit"'
_EXPLICIT_FONT = 'font-family="{}"'.format(
    ", ".join(f"'{name}'" if " " in name else name for name in FONT_STACK)
)


def build_report_html(markdown_text: str) -> str:
    """The complete HTML document the PDF is rendered from.

    Lifted out of a notebook cell unchanged. It lived there, which meant no check
    could reach the pipeline that produces the deliverable, and every preview had
    to reproduce its five steps by hand.
    """

    import markdown as markdown_lib

    body = markdown_lib.markdown(
        charts_to_html(mermaid_to_html(markdown_text or "")),
        extensions=list(MARKDOWN_EXTENSIONS),
        extension_configs=MARKDOWN_EXTENSION_CONFIGS,
    )
    return (
        '<html><head><meta charset="utf-8"><style>'
        + REPORT_CSS
        + "</style></head><body>"
        + tag_wide_tables(body)
        + "</body></html>"
    )


def _data_uri_image(svg: str, alt: str) -> str:
    """One SVG as a markdown image.

    base64 rather than the raw markup: an SVG contains parentheses, and a bare
    ``data:`` URI carrying one ends the ``![](…)`` at the first ``)`` it meets.
    """

    standalone = svg.replace(_INHERIT_FONT, _EXPLICIT_FONT)
    encoded = base64.b64encode(standalone.encode("utf-8")).decode("ascii")
    return f"![{alt}](data:image/svg+xml;base64,{encoded})"


def embed_diagrams(markdown_text: str) -> str:
    """Replace every diagram block with an embedded SVG image, in place.

    Still markdown afterwards, and still self-contained — no sidecar files to
    keep next to it.

    A block that will not render is returned untouched, the same rule
    ``charts_to_html`` and ``mermaid_to_html`` follow. Losing a picture is
    cosmetic; losing the block would delete content from the report.
    """

    def draw_mermaid(match: re.Match[str]) -> str:
        # Through _render, the same entry point the PDF uses. Calling _parse and
        # render_svg directly skips the two passes that sit between them —
        # _percent_only_edge_labels and _auto_color_concentration — so the .md
        # came out with no pink on the concentrated partner and with the absolute
        # figures still on the edge labels. Same shortcut, same bug, twice: the
        # first time was in verify_flowchart_size, which now says so in a comment.
        rendered = _render(match.group(1))
        svg = _SVG_TAG.search(rendered or "")
        return _data_uri_image(svg.group(0), "Sơ đồ") if svg else match.group(0)

    def draw_chart(match: re.Match[str]) -> str:
        spec = parse_linechart(match.group(1))
        rendered = line_chart_svg(spec) if spec else None
        svg = _SVG_TAG.search(rendered or "")
        return _data_uri_image(svg.group(0), "Biểu đồ") if svg else match.group(0)

    text = MERMAID_BLOCK.sub(draw_mermaid, markdown_text or "")
    return CHART_BLOCK.sub(draw_chart, text)
