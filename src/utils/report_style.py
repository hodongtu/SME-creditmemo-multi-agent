"""Print stylesheet + table fitting for the Markdown → PDF export.

Credit memos contain wide financial tables (the asset/capital-structure table is
10 columns), which overflow at a reading-size font. Rather than shrinking the
whole document, ``tag_wide_tables`` marks each table with its column count so
the stylesheet can step the font down only where it is needed.
"""

from __future__ import annotations

import re

from src.utils.diagrams import DIAGRAM_CSS

# Column thresholds at which a table gets a smaller font.
WIDE_TABLE_COLUMNS = 7
EXTRA_WIDE_TABLE_COLUMNS = 9

_TABLE = re.compile(r"<table(?P<attrs>[^>]*)>(?P<body>.*?)</table>", re.DOTALL)
_CELL = re.compile(r"<t[hd]\b")
_ROW = re.compile(r"<tr\b.*?</tr>", re.DOTALL)

REPORT_CSS = f"""
@page {{
    size: A4;
    margin: 11mm 9mm 13mm 9mm;
    @bottom-center {{
        content: counter(page) " / " counter(pages);
        font-size: 8pt;
        color: #666;
    }}
}}
body {{
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 9.5pt;
    line-height: 1.4;
    margin: 0;
}}
h1 {{ font-size: 15pt; margin: 10px 0 6px; }}
h2 {{ font-size: 12.5pt; margin: 12px 0 5px; }}
h3 {{ font-size: 11pt; margin: 10px 0 4px; }}
h4 {{ font-size: 10pt; margin: 8px 0 4px; }}
p, li {{ margin: 4px 0; }}
blockquote {{
    margin: 6px 0; padding: 4px 10px;
    border-left: 3px solid #2f6f9f; background: #f5f8fb;
}}
code {{ font-size: 8.5pt; }}

table {{
    border-collapse: collapse;
    width: 100%;
    margin: 6px 0;
    font-size: 8.5pt;
    /* Let the engine fit columns to content instead of overflowing the page. */
    table-layout: auto;
    page-break-inside: auto;
}}
th, td {{
    border: 1px solid #999;
    padding: 3px 5px;
    vertical-align: top;
    overflow-wrap: break-word;
    word-break: break-word;
}}
th {{ background: #eef2f6; text-align: left; }}
tr {{ page-break-inside: avoid; }}
thead {{ display: table-header-group; }}

/* Wide financial tables step down instead of overflowing the page box. */
table.tbl-wide {{ font-size: 7.5pt; }}
table.tbl-wide th, table.tbl-wide td {{ padding: 2px 3px; }}
table.tbl-xwide {{ font-size: 6.8pt; }}
table.tbl-xwide th, table.tbl-xwide td {{ padding: 2px; letter-spacing: -0.1px; }}
{DIAGRAM_CSS}
"""


def _column_count(table_body: str) -> int:
    """Widest row in the table, in cells."""

    rows = _ROW.findall(table_body)
    if not rows:
        return 0
    return max(len(_CELL.findall(row)) for row in rows)


def tag_wide_tables(html: str) -> str:
    """Add a width class to each table so the stylesheet can shrink wide ones."""

    def replace(match: re.Match[str]) -> str:
        columns = _column_count(match.group("body"))
        if columns >= EXTRA_WIDE_TABLE_COLUMNS:
            css_class = "tbl-xwide"
        elif columns >= WIDE_TABLE_COLUMNS:
            css_class = "tbl-wide"
        else:
            return match.group(0)
        attrs = match.group("attrs")
        if 'class="' in attrs:
            attrs = attrs.replace('class="', f'class="{css_class} ', 1)
        else:
            attrs = f'{attrs} class="{css_class}"'
        return f"<table{attrs}>{match.group('body')}</table>"

    return _TABLE.sub(replace, html or "")
