"""Detail-ledger spreadsheets (sổ chi tiết) into one JSON keyed by account."""

import json
import re
from typing import Any

from src.agents.extraction.structured_extraction import (
    build_extraction_chain,
    run_extraction,
)
from src.utils.common import normalize_text
from src.utils.reading.extractors import read_sheets
from src.prompts import system_prompt


REQUIRED_TOP_LEVEL_KEYS = {"accounts"}

LEDGER_BLOCK_CHAR_BUDGET = 40_000
RESIDUAL_LABEL = "Các đối tác còn lại"
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


def _aggregate(rows: list[list[Any]], columns: list[str]) -> list[Any]:
    """One row summing every column of the rows it stands in for."""

    label_at = _label_positions(columns)
    out: list[Any] = []
    for index in range(len(columns)):
        if index in label_at:
            out.append(f"{RESIDUAL_LABEL} ({len(rows)})" if index == label_at[-1] else "")
            continue
        total = sum(
            row[index] for row in rows
            if index < len(row)
            and isinstance(row[index], (int, float))
            and not isinstance(row[index], bool)
        )
        out.append(round(total))
    return out


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
                if len(rows) <= keep:
                    rankings.append(ranking)
                    continue

                at = columns.index(ranking["sorted_by"]) \
                    if ranking.get("sorted_by") in columns else None
                ordered = sorted(
                    rows,
                    key=(lambda r: _row_value(r, at)) if at is not None
                    else (lambda r: _magnitude(r, label_at)),
                    reverse=True,
                )
                rankings.append({**ranking,
                                 "items": ordered[:keep]
                                 + [_aggregate(ordered[keep:], columns)]})
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


PAYLOAD_CHAR_BUDGET = 120_000


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
    return "\n".join(
        "\t".join(v for i, v in enumerate(r) if i not in drop) for r in rows
    )


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
            + ". Either the model kept the wrong rows, or its answer was cut "
            "short (raise the pass's max_tokens_env, e.g. LLM_LEDGER_MAX_TOKENS; "
            "run testing/probe_max_tokens.py for the model's real ceiling)."
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
) -> list[tuple[dict[str, Any] | None, str]]:
    """Read every ledger file in one call, then share one record between them."""

    sheets, read_notes = read_ledger_sheets(documents)
    if not sheets:
        reason = "; ".join(read_notes) or "No sheet could be read"
        return [(None, reason[:500]) for _ in documents]

    payload, dropped = _json_payload(sheets)
    record, error = run_extraction(
        chain,
        ", ".join(filename for filename, _, _ in documents),
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
    if dropped:
        record["extraction_notes"].append(
            f"Input exceeded {PAYLOAD_CHAR_BUDGET:,} characters, so {len(dropped)} "
            f"sheet(s) were not sent: {', '.join(dropped[:10])}."
        )

    _note_copied_example(record)
    _note_slice_problems(record)
    _drop_total_rows(record)
    fill_source_files(record, documents)

    expected = len(sheet_inventory(documents))
    returned = len(record.get("accounts") or {})
    if returned < expected:
        record["extraction_notes"].append(
            f"WARNING: the input held {expected} sheets but only {returned} accounts "
            f"came back. Absent a TRUNCATED warning above, the model skipped sheets "
            f"— the figures below are NOT the whole ledger the customer "
            f"submitted."
        )
    return [(record, "") for _ in documents]
