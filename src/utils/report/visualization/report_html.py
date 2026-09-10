"""Assemble a finished report: the HTML the PDF is made from, and a portable .md."""

import base64
import re

from src.utils.report.visualization.charts import CHART_BLOCK, charts_to_html, line_chart_svg, parse_linechart
from src.utils.report.visualization.diagrams import MERMAID_BLOCK, _render, mermaid_to_html
from src.utils.report.visualization.graph_svg import FONT_STACK
from src.utils.report.visualization.report_style import REPORT_CSS, tag_wide_tables


MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "footnotes")
MARKDOWN_EXTENSION_CONFIGS = {"footnotes": {"USE_DEFINITION_ORDER": False}}

_SVG_TAG = re.compile(r"<svg\b.*?</svg>", re.DOTALL)
_INHERIT_FONT = 'font-family="inherit"'
_EXPLICIT_FONT = 'font-family="{}"'.format(
    ", ".join(f"'{name}'" if " " in name else name for name in FONT_STACK)
)


def build_report_html(markdown_text: str) -> str:
    """The complete HTML document the PDF is rendered from."""

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
    """One SVG as a markdown image."""

    standalone = svg.replace(_INHERIT_FONT, _EXPLICIT_FONT)
    encoded = base64.b64encode(standalone.encode("utf-8")).decode("ascii")
    return f"![{alt}](data:image/svg+xml;base64,{encoded})"


def embed_diagrams(markdown_text: str) -> str:
    """Replace every diagram block with an embedded SVG image, in place."""

    def draw_mermaid(match: re.Match[str]) -> str:
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
