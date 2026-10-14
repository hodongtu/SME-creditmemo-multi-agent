"""Detail-ledger spreadsheets (sổ chi tiết) into one JSON keyed by account."""

import json
import os
import re
from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
)
from src.utils.common import normalize_text
from src.utils.reading.extractors import (
    SHEET_ROW_CAP,
    read_sheets,
    row_cap_marker,
)
from src.prompts import system_prompt


REQUIRED_TOP_LEVEL_KEYS = {"accounts"}

LEDGER_BLOCK_CHAR_BUDGET = 40_000
RESIDUAL_LABEL = "Các đối tác còn lại"
RESIDUAL_ITEM_LABEL = "Các mặt hàng còn lại"
LABEL_KEYS = ("counterparty_name", "counterparty_code", "item_name")

def _label_positions(columns: list[str]) -> list[int]:
    """Indexes of the text columns, in the order they appear."""

    return [i for i, c in enumerate(columns) if c in LABEL_KEYS]

TOTAL_ROW_NAMES = {"tong", "tong cong", "cong", "total", "sum"}
TOP_ROWS = 5
# Below this, a coincidence with the worked example means nothing.
EXAMPLE_FIGURE_FLOOR = 1_000_000
TOP_ROW_CRITERIA: dict[str, tuple[str, ...]] = {
    "receivable": ("debit_movement", "closing_debit", "closing_credit"),
    "payable": ("credit_movement", "closing_debit", "closing_credit"),
    "inventory": ("outflow_value", "closing_value"),
    "borrowing": ("closing_credit",),
    "other_payable": ("closing_credit",),
    "other_receivable": ("closing_debit",),
    "cash": ("closing_debit",),
    "fixed_asset": ("closing_debit",),
    "equity": ("closing_credit",),
    "unknown": ("closing_debit", "closing_credit"),
    "": ("closing_debit", "closing_credit"),
}


# ── Fitting the record to the prompt's character budget ───────────────────

def render_record(record: dict[str, Any], indent: int = 2) -> str:
    """The record as the agent sees it: framed by indent, one detail row per line."""

    def encode(value: Any, depth: int) -> str:
        pad = " " * (indent * depth)
        inner = pad + " " * indent
        if isinstance(value, list) and value and all(isinstance(v, list) for v in value):
            rows = ",\n".join(
                inner + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in value
            )
            return f"[\n{rows}\n{pad}]"

        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            body = ",\n".join(inner + encode(item, depth + 1) for item in value)
            return f"[\n{body}\n{pad}]"
        if isinstance(value, dict):
            body = ",\n".join(
                f"{inner}{json.dumps(key, ensure_ascii=False)}: {encode(item, depth + 1)}"
                for key, item in value.items()
            )
            return f"{{\n{body}\n{pad}}}" if value else "{}"
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return encode(record, 0)


def _row_value(row: list[Any], index: int) -> float:
    """One column of a row as a magnitude, for ranking. Missing reads as zero."""

    if index >= len(row):
        return 0.0
    value = row[index]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return abs(float(value))


def _magnitude(row: list[Any], label_at: set[int]) -> float:
    """How big this row is, across every column rather than one of them."""

    return max(
        (abs(v) for i, v in enumerate(row)
         if i not in label_at
         and isinstance(v, (int, float)) and not isinstance(v, bool)),
        default=0.0,
    )


def _residual_label(columns: list[str]) -> str:
    """What the remainder is called: counterparties on a debt ledger, items on a
    stock ledger. One label for both would call a list of products "đối tác"."""

    return RESIDUAL_ITEM_LABEL if "item_name" in columns else RESIDUAL_LABEL


_RESIDUAL_NAME = re.compile(
    rf"^(?:{re.escape(RESIDUAL_LABEL)}|{re.escape(RESIDUAL_ITEM_LABEL)}) \((\d+)\)$"
)


def _residual_count(row: list[Any], columns: list[str]) -> int | None:
    """How many detail rows a remainder row stands for; None for a detail row."""

    label_at = _label_positions(columns)
    if not label_at or label_at[-1] >= len(row):
        return None
    match = _RESIDUAL_NAME.match(str(row[label_at[-1]] or ""))
    return int(match.group(1)) if match else None


def _residual_row(
    columns: list[str],
    values: dict[str, Any],
    count: int,
) -> list[Any]:
    """A remainder row: the label in the last label column, figures where known."""

    label_at = _label_positions(columns)
    return [
        f"{_residual_label(columns)} ({count})" if label_at and index == label_at[-1]
        else "" if index in label_at
        else values.get(column)
        for index, column in enumerate(columns)
    ]


def _aggregate(rows: list[list[Any]], columns: list[str]) -> list[Any]:
    """One row summing every column of the rows it stands in for.

    A remainder row among them counts for the rows IT stands for, not one, and
    a figure it does not know (None) leaves the sum unknown rather than quietly
    understated — merging it would otherwise report the rest of a 143-row sheet
    as the handful of rows that happened to be listed.
    """

    label_at = set(_label_positions(columns))
    count = sum(_residual_count(row, columns) or 1 for row in rows)
    values: dict[str, Any] = {}
    for index, column in enumerate(columns):
        if index in label_at:
            continue
        cells = [row[index] if index < len(row) else None for row in rows]
        if any(cell is None and _residual_count(row, columns) is not None
               for cell, row in zip(cells, rows)):
            values[column] = None
            continue
        numeric = [c for c in cells
                   if isinstance(c, (int, float)) and not isinstance(c, bool)]
        values[column] = round(sum(numeric)) if numeric else None
    return _residual_row(columns, values, count)


def add_remainder_rows(record: dict[str, Any]) -> None:
    """Close every top-N list with one row for everything it leaves out.

    The model returns the five largest rows and the account's "totals", which
    cover every detail row on the sheet. The rest is then plain subtraction —
    totals minus the rows listed — so it is done here rather than asked of the
    model: that would cost output tokens for arithmetic, and add one more place
    for a figure to go wrong. By construction the listed rows plus this one add
    up to "totals", so a share column built on them sums to exactly 100%.

    A column whose total the model did not give is left None, never 0: an
    unknown remainder printed as nothing would read as "the top five are the
    whole account". Likewise when the listed rows already exceed the total — the
    slice check names that inconsistency, and a negative "rest" would only hide
    it. Lists that already end in a remainder row are left alone.
    """

    for account in (record.get("accounts") or {}).values():
        if not isinstance(account, dict):
            continue
        columns = account.get("item_columns") or []
        totals = account.get("totals") if isinstance(account.get("totals"), dict) else {}
        declared = account.get("item_count")
        if not columns or not isinstance(declared, int) or isinstance(declared, bool):
            continue
        label_at = set(_label_positions(columns))
        for ranking in account.get("rankings") or []:
            if not isinstance(ranking, dict):
                continue
            rows = [r for r in ranking.get("items") or [] if isinstance(r, list)]
            if any(_residual_count(r, columns) is not None for r in rows):
                continue
            remaining = declared - len(rows)
            if remaining <= 0:
                continue
            values: dict[str, Any] = {}
            for index, column in enumerate(columns):
                if index in label_at:
                    continue
                total = totals.get(column)
                if not isinstance(total, (int, float)) or isinstance(total, bool):
                    values[column] = None
                    continue
                listed = sum(
                    row[index] for row in rows
                    if index < len(row) and isinstance(row[index], (int, float))
                    and not isinstance(row[index], bool)
                )
                rest = total - listed
                values[column] = round(rest) if rest >= -1 else None
            ranking["items"] = rows + [_residual_row(columns, values, remaining)]


def fit_to_budget(
    record: dict[str, Any],
    budget: int = LEDGER_BLOCK_CHAR_BUDGET,
) -> dict[str, Any]:
    """The record trimmed to ``budget`` characters, losing no figures."""

    def shrink(keep: int) -> dict[str, Any]:
        accounts = {}
        for key, account in (record.get("accounts") or {}).items():
            columns = account.get("item_columns") or []
            label_at = set(_label_positions(columns))

            rankings = []
            for ranking in account.get("rankings") or []:
                rows = ranking.get("items") or []
                # The remainder is not a counterparty: it never competes for a
                # place in the list, and it absorbs whatever the trim removes.
                remainder = [r for r in rows if _residual_count(r, columns) is not None]
                detail = [r for r in rows if _residual_count(r, columns) is None]
                if len(detail) <= keep:
                    rankings.append(ranking)
                    continue

                at = columns.index(ranking["sorted_by"]) \
                    if ranking.get("sorted_by") in columns else None
                ordered = sorted(
                    detail,
                    key=(lambda r: _row_value(r, at)) if at is not None
                    else (lambda r: _magnitude(r, label_at)),
                    reverse=True,
                )
                rankings.append({**ranking,
                                 "items": ordered[:keep]
                                 + [_aggregate(ordered[keep:] + remainder, columns)]})
            accounts[key] = {**account, "rankings": rankings}
        return {**record, "accounts": accounts}

    def size(candidate: dict[str, Any]) -> int:
        return len(render_record(candidate))

    if size(record) <= budget:
        return record
    widest = max((len(r.get("items") or [])
                  for a in (record.get("accounts") or {}).values()
                  for r in (a.get("rankings") or [])), default=0)
    # Monotone in `keep`, so a binary search costs ~log2(rows) renders.
    low, high = 0, widest
    while low < high:
        mid = (low + high + 1) // 2
        if size(shrink(mid)) <= budget:
            low = mid
        else:
            high = mid - 1
    return shrink(low)


# ── The pass itself: fence every file, one call, one shared record ─────────

def _render_criteria_table() -> str:
    """The criteria table as prompt text, grouped by the columns they share.

    Written out of TOP_ROW_CRITERIA rather than alongside it: the model ranks by
    what this table says and _note_slice_problems checks against the dict, so two
    hand-kept copies would let a category be graded by a rule it was never given.
    """

    grouped: dict[tuple[str, ...], list[str]] = {}
    for category, columns in TOP_ROW_CRITERIA.items():
        if not category:  # the fallback entry has no name to show the model
            continue
        grouped.setdefault(columns, []).append(category)
    names = [", ".join(cats) for cats in grouped.values()]
    width = max(len(n) for n in names)
    return "\n".join(
        f"  {name:<{width}} : {len(columns)} list"
        f"{'s' if len(columns) > 1 else ''} — {', '.join(columns)}"
        for name, columns in zip(names, grouped)
    )


LEDGER_EXTRACTION_SYSTEM_PROMPT = system_prompt(
    "ledger_extraction", top_row_criteria_table=_render_criteria_table()
)


def build_ledger_extraction_chain(llm: Any):
    """Build the JSON-output extraction chain for detail-ledger workbooks."""

    return build_extraction_chain(LEDGER_EXTRACTION_SYSTEM_PROMPT, llm)


# How much ledger TSV goes into one call. Sized for the case the screen actually
# produces — 4 files of 6 sheets — at 143 detail rows a sheet, which is the depth
# the prompt's own worked example cites ("the returned rows are 5 of 143"):
#
#     24 sheets x (200 + 143 x 86 chars)  =  300,000 chars  =  ~155,000 tokens
#
# measured at 1.93 chars per token with tiktoken on the real sample, not the
# usual 4 — accented Vietnamese and dot-grouped figures both tokenise badly, and
# every "." in 1.210.969.204,91 is its own token.
#
# Raising this is bounded by the MODEL'S INPUT CONTEXT, not by anything here:
# the call also carries ~6,000 tokens of system prompt and must leave
# LLM_LEDGER_MAX_TOKENS for the reply. Env-settable so the ceiling can be fitted
# to the model without a code change.
PAYLOAD_CHAR_BUDGET = int(os.getenv("LEDGER_PAYLOAD_CHARS", "300000"))
# Warn while there is still room to act, rather than only once sheets are gone.
PAYLOAD_WARN_FRACTION = 0.8


def _without_row_counter(content: str) -> str:
    """The sheet's TSV with any row-counter column removed.

    Only a column whose every numeric value is a consecutive integer starting at
    1 — an "Stt" or "TT" and nothing else. The prompt already tells the model to
    ignore these, so dropping them changes no answer; it just stops paying to
    send them. Worth about 9% of the payload on a wide ledger.

    Deliberately strict rather than name-based: a heading match would have to
    guess at every accounting package's wording, and dropping the wrong column
    loses figures in silence. On the sample workbook this fires on the one sheet
    that has such a column and leaves the other five untouched.
    """

    rows = [line.split("\t") for line in content.splitlines()]
    if len(rows) < 6:
        return content
    width = max(len(r) for r in rows)
    drop = set()
    for column in range(width):
        cells = [r[column].strip() if column < len(r) else "" for r in rows]
        digits = [c for c in cells if c.replace(".", "").replace(",", "").isdigit()]
        if len(digits) < 3 or len(digits) < len(cells) * 0.6:
            continue
        if [int(c.replace(".", "").replace(",", "")) for c in digits] == list(
            range(1, len(digits) + 1)
        ):
            drop.add(column)
    if not drop:
        return content

    def trimmed(row: list[str]) -> str:
        rest = [v for i, v in enumerate(row) if i not in drop]
        if not any(cell.strip() for cell in rest):
            # A line whose only text sits in the counter's column is not a
            # counter value: it is a banner. _sheet_grid leaves a full-width
            # merge in column A alone, so the sheet's title, company and
            # period all land exactly where the counter is — and dropping the
            # column took them with it. TK VAY lost four lines that way and its
            # account came back as 341@unknown, for want of the period line.
            # With nothing else on the line there is nothing to misalign.
            return "\t".join(row)
        return "\t".join(rest)

    return "\n".join(trimmed(r) for r in rows)


def read_ledger_sheets(
    documents: list[tuple[str, str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    """Every sheet of every ledger file, and the notes about what did not open.

    Read from the file rather than split back out of ``content``: that string
    carries "--- Sheet: TK VAY (23 dòng x 10 cột, đã bỏ 1 dòng trùng lặp) ---",
    and a regex over it breaks the moment a sheet name contains a dash or the
    parenthetical gains a field — which it did this week. ``read_sheets`` is the
    same reader ``extract_excel_text`` uses, so the two cannot disagree.

    Costs one re-read of each workbook, measured at 29 ms against an LLM call of
    about 25 seconds.

    A file that will not open is named in the notes and left out. It is NOT
    retried by parsing ``content``: falling back to that rebuilds exactly the
    fragile path this replaced.
    """

    sheets: list[dict[str, str]] = []
    notes: list[str] = []
    for filename, _content, path in documents:
        if not path:
            notes.append(f"Could not read {filename}: no file path.")
            continue
        try:
            found = read_sheets(path)
        except Exception as exc:
            notes.append(f"Could not read {filename}: {type(exc).__name__}: {exc}"[:300])
            continue
        sheets += [
            {"filename": filename,
             "sheet_name": sheet.sheet_name,
             "content": _without_row_counter(sheet.content)}
            for sheet in found
        ]
    return sheets, notes


# ── Keeping the years the report needs ────────────────────────────────────

# How many of the most recent years of ledger the report needs. Customers
# routinely upload every year they have — five years, one sheet each — when two
# are asked for, and the older ones spend the input budget on nothing.
LEDGER_YEARS = int(os.getenv("LEDGER_YEARS", "2"))

_FULL_DATE = re.compile(r"(?<!\d)\d{1,2}[/-]\d{1,2}[/-](20[0-4]\d)(?!\d)")
# Words that introduce a period, as normalised tokens. A year only counts as the
# sheet's period when one of these says so.
_PERIOD_WORDS = (
    ("tu", "ngay"), ("den", "ngay"), ("tai", "ngay"), ("nam",), ("nien", "do"),
    ("quy",), ("thang",), ("ky",), ("period",), ("year",),
)
_YEAR_WINDOW = 3


def _is_year(token: str) -> bool:
    return len(token) == 4 and token.isdigit() and 2000 <= int(token) <= 2049


def sheet_years(content: str) -> set[int]:
    """The years a sheet states as its period, read from its own text.

    A year counts when a line says it is a period — "Từ ngày 01/01/2025 đến
    ngày 31/12/2025", "Năm 2024", "Quý 4/2024", "Niên độ kế toán 2024". Only
    when no line declares one do the dates on the detail rows stand in.

    Matched on normalised TOKENS, which settles the two traps for free: the
    figure 2.024.000.000 normalises to "2 024 000 000" and never yields the
    token 2024, and "Việt Nam" carries the word "nam" with no year after it.
    """

    declared: set[int] = set()
    dated: set[int] = set()
    for line in content.splitlines():
        on_line = {int(year) for year in _FULL_DATE.findall(line)}
        dated |= on_line
        tokens = normalize_text(line).split()
        for start in range(len(tokens)):
            for word in _PERIOD_WORDS:
                end = start + len(word)
                if tuple(tokens[start:end]) != word:
                    continue
                declared |= on_line
                declared |= {
                    int(token)
                    for token in tokens[end:end + _YEAR_WINDOW]
                    if _is_year(token)
                }
    return declared or dated


def reporting_cutoff(
    sheet_year_sets: list[set[int]],
    report_years: set[int],
) -> int | None:
    """The oldest year worth sending, or None when no year is known at all.

    The LEDGER_YEARS most recent, counted back from the newest year anything
    in the dossier states — and reaching further back when the financial
    statements cover older years, so the ledger never stops short of the years
    the report analyses. It never moves FORWARD to them: a 2025 ledger beside a
    BCTC for 2023-2024 is the newest data in the dossier, and the reason
    statements lag is exactly why an officer wants it.
    """

    known = set(report_years).union(*sheet_year_sets) if sheet_year_sets else set(report_years)
    if not known:
        return None
    cutoff = max(known) - (LEDGER_YEARS - 1)
    if report_years:
        cutoff = min(cutoff, min(report_years))
    return cutoff


def keep_recent_sheets(
    sheets: list[dict[str, str]],
    report_years: set[int],
) -> tuple[list[dict[str, str]], list[str]]:
    """The sheets worth sending, and a note naming every one that was not.

    A sheet is left out only when EVERY year it states is older than the
    cutoff, so one that spans the boundary — 01/12/2023 to 31/12/2024 — goes.
    A sheet stating no year goes too: losing a ledger costs more than the
    tokens it takes, and it is named so the reader knows the filter could not
    judge it. And if the filter would leave nothing, it sends everything
    instead — it exists to trim excess, not to delete the only ledger there is.
    """

    years = [sheet_years(sheet["content"]) for sheet in sheets]
    cutoff = reporting_cutoff(years, report_years)
    if cutoff is None:
        return sheets, []

    def label(sheet: dict[str, str]) -> str:
        return f"{sheet['filename']} › {sheet['sheet_name'] or '(single sheet)'}"

    kept, old, unknown = [], [], []
    for sheet, stated in zip(sheets, years):
        if not stated:
            kept.append(sheet)
            unknown.append(label(sheet))
        elif max(stated) >= cutoff:
            kept.append(sheet)
        else:
            old.append(f"{label(sheet)} ({', '.join(map(str, sorted(stated)))})")

    notes = []
    if old and not kept:
        return sheets, [
            f"Every ledger sheet is older than {cutoff}, the oldest year the "
            f"report needs; all {len(sheets)} were sent anyway rather than "
            f"leave the report with no ledger."
        ]
    if old:
        notes.append(
            f"Only {cutoff} onwards was sent — the {LEDGER_YEARS} most recent "
            f"years"
            + (f" and every year the financial statements cover "
               f"({', '.join(map(str, sorted(report_years)))})"
               if report_years else "")
            + f". {len(old)} older sheet(s) were left out: "
            f"{', '.join(old[:10])}{' …' if len(old) > 10 else ''}."
        )
    if unknown:
        notes.append(
            f"No reporting year could be read from {len(unknown)} sheet(s), so "
            f"they were sent without being checked: "
            f"{', '.join(unknown[:10])}{' …' if len(unknown) > 10 else ''}."
        )
    return kept, notes


def truncation_notes(sheets: list[dict[str, str]]) -> list[str]:
    """One note per sheet the reader could not read to the end.

    Found in code, not asked of the model: a cut sheet is the one case where
    "totals" cannot be right whatever the model does, because the rows that
    would complete the sum — and often the printed total row itself, which sits
    at the bottom — were never sent. The model is told what the line means too,
    but this note is the one that cannot be forgotten.
    """

    marker = row_cap_marker(SHEET_ROW_CAP)
    return [
        f"INCOMPLETE SHEET: {sheet['filename']} › "
        f"{sheet['sheet_name'] or '(single sheet)'} was read down to "
        f"{SHEET_ROW_CAP:,} rows and has more. Its totals cover only those rows "
        f"and its item_count is at least {SHEET_ROW_CAP:,}, not the sheet's real "
        f"depth."
        for sheet in sheets
        if marker in sheet.get("content", "")
    ]


def _json_payload(sheets: list[dict[str, str]]) -> tuple[str, list[str]]:
    """The sheets as one JSON array, trimmed to the budget at sheet boundaries.

    Whole sheets go, never part of one: half a ledger sheet reads as a complete
    one to the model, and the figures it would report from the surviving rows
    would look like the account's totals.
    """

    kept = list(sheets)
    dropped: list[str] = []
    while kept:
        payload = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
        if len(payload) <= PAYLOAD_CHAR_BUDGET:
            return payload, dropped
        gone = kept.pop()
        dropped.append(f"{gone['filename']} › {gone['sheet_name'] or '(single sheet)'}")
    return "[]", dropped


def sheet_inventory(documents: list[tuple[str, str, str]]) -> list[str]:
    """Every "file › sheet" the payload contains, in reading order."""

    sheets, _notes = read_ledger_sheets(documents)
    return [
        f"{s['filename']} › {s['sheet_name']}" if s["sheet_name"] else s["filename"]
        for s in sheets
    ]


def _drop_total_rows(record: dict[str, Any]) -> None:
    """Remove any printed total row the model left among the detail rows.

    "totals" already holds those figures, so a row named "Tổng" sitting in
    "items" is the same money twice — and it reads as an ordinary counterparty
    to everything downstream, including ``fit_to_budget``'s aggregate row.
    Rewrites ``item_count`` alongside, because the two disagreeing is worse than
    either being wrong on its own.
    """

    for account in (record.get("accounts") or {}).values():
        if not isinstance(account, dict):
            continue
        label_at = _label_positions(account.get("item_columns") or [])
        dropped_labels: set[str] = set()
        for ranking in account.get("rankings") or []:
            if not isinstance(ranking, dict):
                continue
            rows = ranking.get("items") or []
            kept = []
            for row in rows:
                names = [
                    normalize_text(str(row[i])) for i in label_at if i < len(row)
                ]
                hit = [n for n in names if n in TOTAL_ROW_NAMES]
                if hit:
                    dropped_labels.add(hit[0])
                else:
                    kept.append(row)
            if len(kept) != len(rows):
                ranking["items"] = kept
        if dropped_labels:
            # Decremented, not reassigned: item_count is the sheet's own detail
            # row count and a ranking is only the top-N slice of it, so setting
            # it to a list length would shrink the sheet to the size of an
            # excerpt. A printed total was never a detail row, so it comes off.
            declared = account.get("item_count")
            longest = max((len(r.get("items") or [])
                           for r in (account.get("rankings") or [])
                           if isinstance(r, dict)), default=0)
            if isinstance(declared, int):
                account["item_count"] = max(longest, declared - len(dropped_labels))


def _example_figures() -> set[float]:
    """Money-scale numbers the worked example prints, read from the prompt itself.

    Parsed rather than listed so the two cannot drift: change a figure in the
    example and this follows. Only values at or above EXAMPLE_FIGURE_FLOOR count —
    a quantity of 11 in the example says nothing about a real sheet holding 11.
    """

    text = LEDGER_EXTRACTION_SYSTEM_PROMPT
    start = text.find('{\n  "accounts"')
    end = text.find("Return EXACTLY this schema", start)
    if start < 0 or end < 0:
        return set()
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            if abs(node) >= EXAMPLE_FIGURE_FLOOR:
                found.add(float(node))

    try:
        walk(json.loads(text[start:end].strip()))
    except json.JSONDecodeError:
        return set()
    return found


def _note_copied_example(record: dict[str, Any]) -> None:
    """Say so when the answer carries figures that came from the prompt.

    A model under pressure transcribes the example instead of the file, and
    nothing downstream can tell: the figures are well-formed, internally
    consistent, and belong to somebody else. Haiku 4.5 did exactly this on a live
    run, returning the example's "totals" verbatim.

    This went unseen for as long as it did because the example used to be built
    from the sample workbook, so on that dossier a copied answer and a correct
    one were the same bytes. The example now prints repeated digits that no
    ledger produces, which is what makes this check possible at all.

    A note alone would leave the figure in the record for the report to print, so
    the offending cells are blanked here as well: a column that reads as missing
    is recoverable, a column carrying somebody else's balance is not.
    """

    figures = _example_figures()
    if not figures:
        return

    def copied(value: Any) -> bool:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and float(value) in figures)

    seen: dict[str, set[float]] = {}
    for key, account in (record.get("accounts") or {}).items():
        if not isinstance(account, dict):
            continue
        hits: set[float] = set()
        totals = account.get("totals")
        if isinstance(totals, dict):
            for name, value in totals.items():
                if copied(value):
                    hits.add(float(value))
                    totals[name] = None
        for ranking in account.get("rankings") or []:
            if not isinstance(ranking, dict):
                continue
            for row in ranking.get("items") or []:
                if not isinstance(row, list):
                    continue
                for at, cell in enumerate(row):
                    if copied(cell):
                        hits.add(float(cell))
                        row[at] = None
        if hits:
            seen[key] = hits
    if not seen:
        return
    detail = "; ".join(
        f"{key}: {', '.join(f'{v:,.0f}' for v in sorted(values)[:4])}"
        for key, values in seen.items()
    )
    record.setdefault("extraction_notes", []).append(
        "WARNING: figures from the prompt's worked example appear in this record "
        f"— {detail}. Those numbers are illustration, not this customer's ledger, "
        "so the cells holding them have been BLANKED rather than passed on. The "
        "affected columns now read as missing, and every figure in the named "
        "accounts should be treated as unverified until the source file is "
        "checked by hand."
    )


def _note_slice_problems(record: dict[str, Any]) -> None:
    """Check what stays true once each ranking is a top-N slice of one column.

    ``item_count`` used to equal ``len(items)``, and comparing them caught a
    reply cut off mid-array — ``JsonOutputParser`` repairs truncated JSON rather
    than raising, so a cut answer arrives looking whole, and one live run had an
    account declare 26 rows and carry 18. That comparison is now meaningless:
    the two differ by design on every account with more than a handful of rows.

    Five things still hold, and the first three only became checkable when each
    list was cut down to a single criterion:

    * every column named in ``TOP_ROW_CRITERIA`` for this category has a list,
      and every list's ``sorted_by`` is a real column of ``item_columns``;
    * each list is ordered by its own column, largest absolute value first;
    * a counterparty appearing in two lists carries the same figures in both;
    * a list holds at least ``TOP_ROWS`` rows whenever the sheet had that many —
      the report asks for five, and fewer than five from a sheet that has them is
      a failure, not a shorter answer;
    * its column sums cannot exceed the account's own totals. A part is never
      larger than the whole, so exceeding means either the wrong rows were kept
      or ``totals`` is not the account's.

    None of these notices a model that quietly picked the wrong five and ordered
    them correctly. Nothing here can: that would need the rows it did not send.
    """

    problems = []
    for key, account in (record.get("accounts") or {}).items():
        if not isinstance(account, dict):
            continue
        columns = account.get("item_columns") or []
        declared = account.get("item_count")
        totals = account.get("totals") or {}
        rankings = [r for r in (account.get("rankings") or []) if isinstance(r, dict)]

        # A column declared in "item_columns" but absent from "totals" has no
        # denominator at all, and that reads downstream as an account with no
        # figures in that column rather than as a failure. The zero-total check
        # further down cannot see it: there is no key to find a zero in.
        # Label columns are exempt — a counterparty name has no total.
        absent = [
            name for name in columns
            if name not in LABEL_KEYS and name not in totals
            and any(
                isinstance(row[at], (int, float)) and not isinstance(row[at], bool)
                for r in rankings
                for row in (r.get("items") or [])
                if (at := columns.index(name)) < len(row)
            )
        ]
        if absent:
            problems.append(
                f"{key}: {', '.join(absent)} carry figures in the rows but have "
                "no total, so nothing can be divided by them"
            )

        wanted = TOP_ROW_CRITERIA.get(account.get("category") or "", ())
        got_by = [r.get("sorted_by") for r in rankings]
        missing = [c for c in wanted if c not in got_by]
        if missing:
            problems.append(
                f"{key}: missing ranking(s) for {', '.join(missing)} — expected "
                f"{len(wanted)}, got {len(rankings)}"
            )
        for column in got_by:
            if column not in columns:
                problems.append(
                    f"{key}: sorted_by {column!r} is not one of item_columns"
                )

        # Same counterparty, two lists, two sets of figures — one of them is a
        # transcription the model invented on the second pass over the sheet.
        label_at = _label_positions(columns)
        seen: dict[str, tuple[Any, ...]] = {}
        for ranking in rankings:
            for row in ranking.get("items") or []:
                name = "|".join(str(row[i]) for i in label_at if i < len(row))
                if not name:
                    continue
                figures = tuple(row)
                if name in seen and seen[name] != figures:
                    problems.append(
                        f"{key}: rows for {name!r} disagree between rankings"
                    )
                seen.setdefault(name, figures)

        for ranking in rankings:
            column = ranking.get("sorted_by")
            rows = ranking.get("items") or []
            label = f"{key}[{column}]"

            if column in columns:
                at = columns.index(column)
                # A blank in the ranked column is a figure _note_copied_example
                # took out, not a mis-sort: _row_value reads it as zero, and every
                # row below it would then look out of order. Saying so twice about
                # one problem buries the sentence that names the real cause.
                blanked = any(at < len(row) and row[at] is None for row in rows)
                values = [_row_value(row, at) for row in rows]
                if not blanked and any(a < b for a, b in zip(values, values[1:])):
                    problems.append(
                        f"{label}: rows are not sorted by {column} — "
                        f"{[f'{v:,.0f}' for v in values[:6]]}"
                    )

            if isinstance(declared, int) and len(rows) < min(declared, TOP_ROWS):
                problems.append(
                    f"{label}: sheet has {declared} rows but only {len(rows)} "
                    "came back"
                )

            for index, name in enumerate(columns):
                cap = totals.get(name)
                if not isinstance(cap, (int, float)) or isinstance(cap, bool):
                    continue
                got = sum(
                    row[index] for row in rows
                    if index < len(row)
                    and isinstance(row[index], (int, float))
                    and not isinstance(row[index], bool)
                )
                # Rounding in the printed total is normal; a slice genuinely
                # bigger than its account is not.
                if abs(got) > abs(cap) + 1:
                    # A zero total under non-zero rows is not a slice that grew
                    # too large — it is a column the printed "Tổng cộng" row left
                    # blank and the model copied as zero. Worth saying apart,
                    # because the fix is different and the consequence is worse:
                    # every share divided by it is meaningless.
                    if not cap:
                        problems.append(
                            f"{label}.{name}: total is 0 while rows carry "
                            f"{got:,.0f} — the printed total row most likely left "
                            f"this column blank, so it is NOT a usable denominator"
                        )
                    else:
                        problems.append(
                            f"{label}.{name}: rows sum to {got:,.0f}, above the "
                            f"account total of {cap:,.0f}"
                        )

    if problems:
        record["extraction_notes"].append(
            "WARNING: the top-row selection does not hold together — "
            + "; ".join(problems)
            # A reply cut at the token ceiling is refused before it gets here
            # (run_extraction reads finish_reason), so advising a higher
            # ceiling would send the reader after a cause already ruled out.
            + ". The reply finished on its own, so the model kept the wrong "
            "rows rather than running out of room."
        )


def fill_source_files(record: dict[str, Any], documents) -> None:
    """Put back the per-account ``source_files`` the model no longer writes.

    The model used to name the file on every account, and on a one-file dossier
    that was the same string seven times. It is derivable instead: the reader
    already knows which file each sheet came from, and the account names its
    sheet. So the model writes the part only it knows, and the program fills in
    the part it can look up.

    A sheet name that matches nothing leaves the list EMPTY and says so. The
    model does get sheet names wrong — one live run filed a "P.THU KHAC" account
    under sheet "P.TRA KHAC" — and guessing a filename from a name that matched
    nothing would dress that mistake up as provenance.
    """

    by_sheet: dict[str, list[str]] = {}
    for entry in sheet_inventory(documents):
        filename, _, sheet = entry.partition(" › ")
        by_sheet.setdefault(normalize_text(sheet or filename), []).append(filename)

    unmatched = []
    for key, account in (record.get("accounts") or {}).items():
        if not isinstance(account, dict):
            continue
        sheets = account.get("source_sheet_name") or []
        if isinstance(sheets, str):
            sheets = [sheets]
        files: list[str] = []
        for sheet in sheets:
            for name in by_sheet.get(normalize_text(str(sheet)), []):
                if name not in files:
                    files.append(name)
            if normalize_text(str(sheet)) not in by_sheet:
                unmatched.append(f"{key} → {sheet!r}")
        # A .csv names no sheet at all; on a one-file dossier that is unambiguous.
        # Only when the account named NO sheet, never when it named one that
        # matched nothing — falling back there would hand a filename to exactly
        # the case the warning exists to flag.
        all_files = {n for names in by_sheet.values() for n in names}
        if not sheets and len(all_files) == 1:
            files = sorted(all_files)
        account["source_files"] = files
    if unmatched:
        record.setdefault("extraction_notes", []).append(
            "WARNING: source_sheet_name did not match any sheet that was read, so "
            "the source file could not be filled in for: "
            + "; ".join(unmatched[:6])
            + ". The sheet name is the model's, and a wrong one here means the "
            "account may have been read off a different sheet than it claims."
        )


def extract_ledger_batch(
    chain: Any,
    documents: list[tuple[str, str, str]],
    report_years: set[int] | None = None,
) -> list[tuple[dict[str, Any] | None, str]]:
    """Read every ledger file in one call, then share one record between them.

    `report_years` are the years the financial statements cover, when the
    dossier has any; the sheets older than the report needs are left out before
    the call, so the budget is spent on years somebody will read.
    """

    sheets, read_notes = read_ledger_sheets(documents)
    if not sheets:
        reason = "; ".join(read_notes) or "No sheet could be read"
        return [(None, reason[:500]) for _ in documents]

    sheets, year_notes = keep_recent_sheets(sheets, report_years or set())
    payload, dropped = _json_payload(sheets)
    # Name only the files that are in the payload. The prompt makes the model
    # account for every sheet it is told about, so listing a file it never
    # receives asks it to report a missing sheet — or to invent one.
    sent_files = list(dict.fromkeys(s["filename"] for s in json.loads(payload)))
    record, error = run_extraction(
        chain,
        ", ".join(sent_files),
        payload,
        REQUIRED_TOP_LEVEL_KEYS,
        "No ledger extraction LLM configured.",
    )
    if record is None:
        return [(None, error) for _ in documents]

    for key in ("unmapped_columns", "extraction_notes"):
        misplaced = (record.get("accounts") or {}).pop(key, None)
        if isinstance(misplaced, list) and not record.get(key):
            record[key] = misplaced
    record.setdefault("unmapped_columns", [])
    record.setdefault("extraction_notes", [])
    record["extraction_notes"] += read_notes
    record["extraction_notes"] += year_notes
    record["extraction_notes"] += truncation_notes(sheets)
    if dropped:
        record["extraction_notes"].append(
            f"Input exceeded {PAYLOAD_CHAR_BUDGET:,} characters, so {len(dropped)} "
            f"sheet(s) were not sent: {', '.join(dropped[:10])}. Raise "
            f"LEDGER_PAYLOAD_CHARS if the model's context has room for them."
        )
    elif len(payload) > PAYLOAD_CHAR_BUDGET * PAYLOAD_WARN_FRACTION:
        record["extraction_notes"].append(
            f"This dossier used {len(payload):,} of the "
            f"{PAYLOAD_CHAR_BUDGET:,}-character input budget "
            f"({len(payload) / PAYLOAD_CHAR_BUDGET:.0%}) across "
            f"{len(sheets)} sheet(s). Nothing was dropped, but one more file "
            f"would be."
        )

    _note_copied_example(record)
    _note_slice_problems(record)
    _drop_total_rows(record)
    fill_source_files(record, documents)
    # Last, so every check above judges the model's own rows, and the remainder
    # is taken against item_count after _drop_total_rows has corrected it.
    add_remainder_rows(record)

    # Counted against what was SENT. Counting every sheet on disk would blame
    # the model for the ones the year filter and the budget left out — and
    # those are already named above.
    expected = len(sheets) - len(dropped)
    returned = len(record.get("accounts") or {})
    if returned < expected:
        record["extraction_notes"].append(
            f"WARNING: {expected} sheets were sent but only {returned} accounts "
            f"came back. The reply finished on its own — a reply cut at the "
            f"token ceiling is refused before this point — so the model skipped "
            f"or merged sheets, and the figures below are NOT the whole ledger "
            f"that was sent."
        )
    return [(record, "") for _ in documents]
