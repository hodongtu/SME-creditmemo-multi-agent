"""Deterministic credit-need table for the Credit Proposal agent.

The proposal's core is arithmetic: how much working capital the cash cycle ties
up, how much of it the customer already funds, and what is left to lend. Left to
the model those figures are 25 chances to produce a plausible wrong number, so
they are computed here and handed over as data — the same division of labour
``FinancialRatioCalculator`` already has for the ratios.

Two things drive the design.

**Every row records where it came from.** A real credit application carries next
year's revenue but almost never the guarantee mix or the LC terms, so most of the
guarantee and LC rows run on policy defaults. A reviewer reading "83,4 tỷ đồng
bảo lãnh" must be able to see at a glance whether that came off the customer's
paperwork or out of a default table — printing both the same way would be
presenting an assumption as evidence.

**The planning year falls back down a chain.** Credit application first, then the
site-visit report, then last year's statements. Each rung is a weaker claim than
the one above it, and the source flag says which rung was used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.common import normalize_text

DAYS_PER_YEAR = 365

# Source labels. Ordered strongest to weakest as evidence.
SRC_STATEMENTS = "BCTC"
SRC_PROPOSAL = "đề nghị"
SRC_SURVEY = "khảo sát"
SRC_CIC = "CIC"
SRC_DERIVED = "tính toán"
SRC_DEFAULT = "mặc định"

# Last-resort stand-in for the value of contracts needing a guarantee, used only
# when neither a contract plan nor a growth rate is available.
GUARANTEE_REVENUE_RATIO = 0.30

# (label, share of guarantee turnover, average days outstanding, name keys).
# The shares are policy defaults, used per type only when the credit
# application does not name that facility. The keys match a facility name in
# ``credit_request.facilities``, which is free text the extraction copies off
# the form.
GUARANTEE_TYPES: tuple[tuple[str, float, int, tuple[str, ...]], ...] = (
    ("Bảo lãnh dự thầu", 0.05, 90, ("du thau",)),
    # "thuc hien" rather than "thuc hien hop dong": forms shorten it to
    # "thực hiện HĐ" about as often as they write it out.
    ("Bảo lãnh thực hiện hợp đồng", 0.10, 120, ("thuc hien",)),
    ("Bảo lãnh tạm ứng", 0.30, 90, ("tam ung",)),
    ("Bảo lãnh bảo hành", 0.05, 360, ("bao hanh",)),
    ("Bảo lãnh thanh toán/thuế", 0.30, 90, ("thanh toan", "thue")),
)

# A facility only counts as a guarantee if its name says so. Without this gate
# a limit called "Hạn mức thanh toán quốc tế" would be booked as a payment
# guarantee purely because it contains "thanh toán".
GUARANTEE_NAME_MARKER = "bao lanh"

# Tokens that mark a letter-of-credit facility. Matched on whole tokens: "L/C"
# normalises to the two tokens "l" and "c", and looking for the substring "lc"
# instead would fire inside unrelated words.
LC_NAME_TOKENS = ("lc",)
LC_NAME_PHRASES = ("thu tin dung", "tin dung chung tu")

# Import ratio belongs in the 331 payables ledger (foreign suppliers' credit
# turnover over the period's total). No extraction pass reads that ledger yet,
# so it falls back to this and the row says so.
IMPORT_RATIO_DEFAULT = 0.50
# Of what is imported, the share settled by letter of credit.
LC_SHARE_OF_IMPORT_DEFAULT = 0.50
LC_SIGHT_SHARE_DEFAULT = 0.50
LC_DEFERRED_SHARE_DEFAULT = 0.50
LC_SIGHT_DAYS_DEFAULT = 30
LC_DEFERRED_DAYS_DEFAULT = 180


@dataclass
class Row:
    """One line of the table: a label, the two years, a unit and a source."""

    label: str
    unit: str
    latest: float | None = None
    plan: float | None = None
    source: str = ""
    note: str = ""


@dataclass
class CreditNeedTable:
    latest_year: str = ""
    plan_year: str = ""
    rows: list[Row] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "latest_year": self.latest_year,
            "plan_year": self.plan_year,
            "rows": [
                {
                    "label": row.label,
                    "unit": row.unit,
                    "latest": row.latest,
                    "plan": row.plan,
                    "source": row.source,
                    "note": row.note,
                }
                for row in self.rows
            ],
            "warnings": self.warnings,
        }


def _num(value: Any) -> float | None:
    """A finite number, or None. Booleans are not numbers here."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def total_other_lender_debt(cic_extractions: list[dict[str, Any]]) -> float | None:
    """Outstanding balance across other credit institutions, from CIC S10A.

    Section 2.1 lists one block per institution, and each block may carry both
    per-facility lines *and* a "Tổng cộng" line. Summing everything would count
    those institutions twice, so a total line wins for its own institution and
    the detail lines are only added where no total was printed.
    """

    per_lender: dict[str, dict[str, float]] = {}
    for extraction in cic_extractions:
        for row in (extraction or {}).get("du_no_hien_tai") or []:
            if not isinstance(row, dict):
                continue
            amount = _num(row.get("vnd"))
            if amount is None:
                continue
            lender = str(row.get("tctd") or "").strip() or "(không rõ TCTD)"
            bucket = per_lender.setdefault(lender, {"total": 0.0, "details": 0.0})
            # normalize_text strips the diacritics, so "Tổng cộng", "TỔNG CỘNG"
            # and an OCR pass that dropped the marks all match the same way.
            if "tong cong" in normalize_text(str(row.get("khoan_muc") or "")):
                bucket["total"] = max(bucket["total"], amount)
            else:
                bucket["details"] += amount

    if not per_lender:
        return None
    return sum(
        bucket["total"] or bucket["details"] for bucket in per_lender.values()
    )


def _facilities(proposal: dict[str, Any] | None) -> list[tuple[str, float]]:
    """(normalised name, amount) for every facility the application names."""

    block = (proposal or {}).get("credit_request")
    if not isinstance(block, dict):
        return []
    out: list[tuple[str, float]] = []
    for item in block.get("facilities") or []:
        if not isinstance(item, dict):
            continue
        amount = _num(item.get("amount"))
        if amount is None:
            continue
        out.append((normalize_text(str(item.get("name") or "")), amount))
    return out


def _is_lc_name(name: str) -> bool:
    """True when a facility name denotes a letter of credit.

    Whole tokens, not substrings: "L/C" normalises to the two tokens "l" and
    "c", and searching for "lc" inside the string would fire on unrelated words.
    """

    tokens = name.split()
    return (
        any(token in tokens for token in LC_NAME_TOKENS)
        or ("l" in tokens and "c" in tokens)
        or any(phrase in name for phrase in LC_NAME_PHRASES)
    )


def guarantee_turnover_from_file(
    proposal: dict[str, Any] | None,
) -> dict[str, float]:
    """{guarantee label: turnover} for the types the application actually names.

    Types the form is silent about are simply absent, so the caller falls back
    to the policy share for those and leaves the rest on the customer's own
    figures — the priority runs per line, not per document.
    """

    found: dict[str, float] = {}
    for name, amount in _facilities(proposal):
        if GUARANTEE_NAME_MARKER not in name:
            continue
        for label, _share, _days, keys in GUARANTEE_TYPES:
            if any(key in name for key in keys):
                found[label] = found.get(label, 0.0) + amount
                break
    return found


def lc_turnover_from_file(proposal: dict[str, Any] | None) -> float | None:
    """Total LC limit named in the application, or None if it names none."""

    total = 0.0
    seen = False
    for name, amount in _facilities(proposal):
        if GUARANTEE_NAME_MARKER in name:
            continue
        if _is_lc_name(name):
            total += amount
            seen = True
    return total if seen else None


def planned_contract_value(proposal: dict[str, Any] | None) -> float | None:
    """Total value the application plans to execute next year, or None.

    Section B of the form lists contracts already signed with a "giá trị dự kiến
    thực hiện năm kế hoạch" column; that total is the closest thing the file has
    to the value of work needing a guarantee.
    """

    block = (proposal or {}).get("business_plan")
    if not isinstance(block, dict):
        return None
    total = 0.0
    seen = False
    for section in block.get("sections") or []:
        if not isinstance(section, dict):
            continue
        value = _num(section.get("total_planned_value"))
        if value is not None:
            total += value
            seen = True
    return total if seen else None


def _survey_lc(survey: dict[str, Any] | None, key: str) -> float | None:
    block = (survey or {}).get("lc_terms")
    return _num(block.get(key)) if isinstance(block, dict) else None


def _pick(
    survey_value: float | None,
    default: float,
) -> tuple[float, str]:
    """Site-visit figure when the report stated one, else the policy default."""

    if survey_value is not None:
        return survey_value, SRC_SURVEY
    return default, SRC_DEFAULT


def requested_sections(proposal: dict[str, Any] | None) -> dict[str, bool]:
    """Which of the three blocks the credit application actually asks for.

    The bank's rule is to drop a block the customer raised no need for. Read off
    the facility list rather than guessed: a form asking only for a working
    capital limit should not be answered with a page of guarantee estimates.

    With no application at all every block stays on — there is nothing saying
    the customer does not need them, and silently emitting an empty table would
    read as a system fault rather than an absence of demand.
    """

    names = [name for name, _amount in _facilities(proposal)]
    if not names:
        return {"loan": True, "guarantee": True, "lc": True, "stated": False}
    guarantee = any(GUARANTEE_NAME_MARKER in name for name in names)
    lc = lc_turnover_from_file(proposal) is not None
    # Anything that is neither a guarantee nor an LC is a funded facility.
    loan = any(
        GUARANTEE_NAME_MARKER not in name
        and not _is_lc_name(name)
        for name in names
    )
    return {"loan": loan, "guarantee": guarantee, "lc": lc, "stated": True}


def _plan_value(
    proposal: dict[str, Any] | None,
    survey: dict[str, Any] | None,
    proposal_path: tuple[str, str],
    survey_path: tuple[str, str],
    statements_value: float | None,
) -> tuple[float | None, str]:
    """Walk the planning-year fallback chain, returning (value, source)."""

    block = (proposal or {}).get(proposal_path[0])
    if isinstance(block, dict):
        value = _num(block.get(proposal_path[1]))
        if value is not None:
            return value, SRC_PROPOSAL

    block = (survey or {}).get(survey_path[0])
    if isinstance(block, dict):
        value = _num(block.get(survey_path[1]))
        if value is not None:
            return value, SRC_SURVEY

    if statements_value is not None:
        return statements_value, SRC_STATEMENTS
    return None, ""


def build_credit_need_table(
    yearly_metrics: dict[str, dict[str, float]],
    yearly_ratios: dict[str, dict[str, float]],
    proposal_extraction: dict[str, Any] | None = None,
    sitevisit_extraction: dict[str, Any] | None = None,
    cic_s10a_extractions: list[dict[str, Any]] | None = None,
) -> CreditNeedTable:
    """Build the two-year credit-need table. Never raises on missing inputs."""

    table = CreditNeedTable()
    if not yearly_metrics:
        table.warnings.append("Không có số liệu BCTC — không tính được bảng nhu cầu tín dụng.")
        return table

    latest_year = max(yearly_metrics)
    metrics = yearly_metrics.get(latest_year) or {}
    ratios = (yearly_ratios or {}).get(latest_year) or {}
    table.latest_year = latest_year

    # --- planning-year revenue and COGS, down the fallback chain ------------
    revenue_latest = _num(metrics.get("net_revenue"))
    cogs_latest = _num(metrics.get("cogs"))

    revenue_plan, revenue_src = _plan_value(
        proposal_extraction, sitevisit_extraction,
        ("plan_efficiency", "revenue"),
        ("business_plan_next_year", "net_revenue"),
        revenue_latest,
    )
    cogs_plan, cogs_src = _plan_value(
        proposal_extraction, sitevisit_extraction,
        ("plan_efficiency", "cogs"),
        ("business_plan_next_year", "cogs"),
        cogs_latest,
    )

    # Same fallback order as the figures themselves, so the column heading names
    # the year the numbers under it actually belong to.
    year_sources = (
        ((proposal_extraction or {}).get("business_plan"), "plan_year"),
        ((sitevisit_extraction or {}).get("business_plan_next_year"), "year"),
    )
    # Rendered in the same shape the statement years already come in ("Năm
    # 2025"), because the two sit side by side as column headings and a bare
    # "2026" next to "Năm 2025" reads as a mistake.
    table.plan_year = "Năm kế hoạch"
    for block, key in year_sources:
        if isinstance(block, dict) and str(block.get(key) or "").strip():
            raw = str(block[key]).strip()
            table.plan_year = f"Năm {raw}" if raw.isdigit() else raw
            break

    sections = requested_sections(proposal_extraction)
    show_guarantees = sections["guarantee"]
    show_lc = sections["lc"]

    add = table.rows.append
    add(Row("Doanh thu thuần", "VNĐ", revenue_latest, revenue_plan, revenue_src))
    add(Row("Giá vốn hàng bán", "VNĐ", cogs_latest, cogs_plan, cogs_src))
    add(Row(
        "Tỷ lệ giá vốn/Doanh thu thuần", "%",
        _pct(_div(cogs_latest, revenue_latest)),
        _pct(_div(cogs_plan, revenue_plan)),
        SRC_DERIVED,
    ))

    # --- cash cycle: same days for both years -------------------------------
    ccc = _num(ratios.get("cash_conversion_cycle"))
    for label, key in (
        ("Chu kỳ tiền", "cash_conversion_cycle"),
        ("Số ngày phải thu ngắn hạn", "dso"),
        ("Số ngày hàng tồn kho", "dio"),
        ("Số ngày phải trả ngắn hạn", "dpo"),
    ):
        value = _num(ratios.get(key))
        add(Row(label, "ngày", value, value, SRC_STATEMENTS,
                "Giả định giữ nguyên vòng quay của năm gần nhất"
                if key == "cash_conversion_cycle" else ""))

    # --- the working-capital chain ------------------------------------------
    need_latest = _cycle_need(cogs_latest, ccc)
    need_plan = _cycle_need(cogs_plan, ccc)
    add(Row("Nhu cầu vốn lưu động theo chu kỳ tiền", "VNĐ",
            need_latest, need_plan, SRC_DERIVED,
            "Giá vốn / (365 / chu kỳ tiền)"))

    equity_wc = _num(ratios.get("net_working_capital"))
    add(Row("Vốn chủ sở hữu tham gia tài trợ vốn lưu động", "VNĐ",
            equity_wc, equity_wc, SRC_STATEMENTS,
            "Vốn lưu động ròng = TSNH − Nợ ngắn hạn"))

    other_debt = total_other_lender_debt(cic_s10a_extractions or [])
    add(Row("Nguồn vốn khác (dư nợ tại TCTD khác)", "VNĐ",
            other_debt, other_debt,
            SRC_CIC if other_debt is not None else ""))

    loan_latest = _residual(need_latest, equity_wc, other_debt)
    loan_plan = _residual(need_plan, equity_wc, other_debt)
    note = ""
    if loan_plan is not None and loan_plan < 0:
        note = ("Số âm: nguồn vốn tự có và dư nợ hiện hữu đã đủ tài trợ chu kỳ "
                "tiền, khách hàng chưa phát sinh nhu cầu vay vốn lưu động")
    add(Row("Nhu cầu vốn vay", "VNĐ", loan_latest, loan_plan, SRC_DERIVED, note))

    # --- guarantees: planning year only, per type ---------------------------
    # The fallback runs per line, not per document: a form naming only a bid
    # bond puts that one on the customer's figure and leaves the other four on
    # the policy share, each carrying its own source flag.
    stated_guarantees = guarantee_turnover_from_file(proposal_extraction)
    # Contract value needing a guarantee, down the three tiers the bank states.
    contract_value = planned_contract_value(proposal_extraction)
    if contract_value is not None:
        base_source, base_note = SRC_PROPOSAL, "Tổng giá trị dự kiến thực hiện năm kế hoạch"
    else:
        growth = _num(ratios.get("revenue_growth"))
        if revenue_latest is not None and growth is not None:
            contract_value = revenue_latest * (1 + growth / 100)
            base_source = SRC_STATEMENTS
            base_note = f"Doanh thu năm gần nhất × (1 + tăng trưởng {growth:.1f}%)"
        elif revenue_plan is not None:
            contract_value = revenue_plan * GUARANTEE_REVENUE_RATIO
            base_source = SRC_DEFAULT
            base_note = f"Doanh thu năm kế hoạch × {GUARANTEE_REVENUE_RATIO:.0%}"
        else:
            contract_value, base_source, base_note = None, "", ""

    if show_guarantees:
        add(Row("Giá trị hợp đồng cần bảo lãnh", "VNĐ", None, contract_value,
                base_source, base_note))
        for label, share, days, _keys in GUARANTEE_TYPES:
            stated = stated_guarantees.get(label)
            if stated is not None:
                # The form states a limit while the row is an average balance,
                # so the stated amount goes through the same tenor conversion
                # rather than being dropped in as-is.
                add(Row(label, "VNĐ", None, _balance(stated, 1.0, days),
                        SRC_PROPOSAL, f"Hạn mức đề nghị / (365 / {days} ngày)"))
            else:
                add(Row(label, "VNĐ", None, _balance(contract_value, share, days),
                        base_source or SRC_DEFAULT,
                        f"× {share:.0%} rồi / (365 / {days} ngày)"))

    # --- LC: planning year only, based on projected COGS ---------------------
    if show_lc:
        import_ratio, import_src = _pick(
            _survey_lc(sitevisit_extraction, "import_ratio"), IMPORT_RATIO_DEFAULT)
        lc_share, lc_share_src = _pick(
            _survey_lc(sitevisit_extraction, "lc_share_of_import"),
            LC_SHARE_OF_IMPORT_DEFAULT)
        sight_share, sight_share_src = _pick(
            _survey_lc(sitevisit_extraction, "sight_share"), LC_SIGHT_SHARE_DEFAULT)
        deferred_share, deferred_share_src = _pick(
            _survey_lc(sitevisit_extraction, "deferred_share"),
            LC_DEFERRED_SHARE_DEFAULT)
        sight_days, sight_days_src = _pick(
            _survey_lc(sitevisit_extraction, "sight_days"), LC_SIGHT_DAYS_DEFAULT)
        deferred_days, deferred_days_src = _pick(
            _survey_lc(sitevisit_extraction, "deferred_days"),
            LC_DEFERRED_DAYS_DEFAULT)

        add(Row("Tỷ lệ nhập khẩu", "%", None, import_ratio * 100, import_src,
                "Nguồn chuẩn là sổ chi tiết 331 (phát sinh có của NCC nước ngoài "
                "/ tổng phát sinh có) — chưa có trích xuất sổ này"
                if import_src == SRC_DEFAULT else ""))
        add(Row("Tỷ lệ hàng nhập cần mở LC", "%", None, lc_share * 100, lc_share_src))

        stated_lc = lc_turnover_from_file(proposal_extraction)
        if stated_lc is not None:
            lc_turnover, lc_source = stated_lc, SRC_PROPOSAL
            lc_note = "Hạn mức L/C đề nghị"
        elif cogs_plan is not None:
            lc_turnover = cogs_plan * import_ratio * lc_share
            lc_source = SRC_DERIVED
            lc_note = "Giá vốn dự phóng × tỷ lệ nhập khẩu × tỷ lệ cần LC"
        else:
            lc_turnover, lc_source, lc_note = None, "", ""
        add(Row("Doanh số mở LC dự kiến", "VNĐ", None, lc_turnover, lc_source, lc_note))

        add(Row("Tỷ lệ LC trả ngay/Doanh số mở LC", "%", None,
                sight_share * 100, sight_share_src))
        add(Row("Tỷ lệ LC trả chậm/Doanh số mở LC", "%", None,
                deferred_share * 100, deferred_share_src))
        add(Row("Số ngày trung bình thanh toán LC trả ngay", "ngày", None,
                sight_days, sight_days_src))
        add(Row("Số ngày trung bình thanh toán LC trả chậm", "ngày", None,
                deferred_days, deferred_days_src))
        lc_days = sight_share * sight_days + deferred_share * deferred_days
        add(Row("Số ngày trung bình thanh toán LC bình quân", "ngày", None,
                lc_days, SRC_DERIVED,
                "Bình quân gia quyền theo tỷ lệ trả ngay/trả chậm"))
        add(Row("Số dư LC trung bình", "VNĐ", None,
                _balance(lc_turnover, 1.0, lc_days), SRC_DERIVED,
                "Doanh số mở LC × số ngày bình quân / 365"))

    if sections["stated"]:
        dropped = [
            name for name, shown in (
                ("bảo lãnh", show_guarantees), ("LC", show_lc),
            ) if not shown
        ]
        if dropped:
            table.warnings.append(
                "Giấy đề nghị không nêu nhu cầu "
                + " và ".join(dropped)
                + " — đã bỏ phần này khỏi bảng."
            )
    if ccc is None:
        table.warnings.append(
            "Thiếu chu kỳ tiền — không tính được nhu cầu vốn lưu động."
        )
    if other_debt is None:
        table.warnings.append(
            "Không có dữ liệu CIC — nguồn vốn khác coi như chưa xác định, "
            "nhu cầu vốn vay có thể đang bị tính cao hơn thực tế."
        )
    return table


def _pct(ratio: float | None) -> float | None:
    return None if ratio is None else ratio * 100


def _cycle_need(cogs: float | None, ccc: float | None) -> float | None:
    """Giá vốn / (365 / chu kỳ tiền) — written as the bank states it."""

    if cogs is None or ccc is None or ccc == 0:
        return None
    return cogs / (DAYS_PER_YEAR / ccc)


def _residual(
    need: float | None,
    equity: float | None,
    other: float | None,
) -> float | None:
    """Loan need = cycle need − equity funding − other lenders.

    A missing component counts as zero rather than voiding the row: the customer
    with no CIC record genuinely has no other-lender debt to subtract, and
    blanking the whole line would hide the number the proposal is about. The
    caller warns separately when CIC data was absent.
    """

    if need is None:
        return None
    return need - (equity or 0.0) - (other or 0.0)


def _balance(turnover: float | None, share: float, days: float) -> float | None:
    """Average outstanding balance from a turnover, a share and a tenor."""

    if turnover is None:
        return None
    return turnover * share * days / DAYS_PER_YEAR
