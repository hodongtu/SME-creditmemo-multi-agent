"""Deterministic credit-need table for the Credit Proposal agent"""

from dataclasses import dataclass, field
from typing import Any

from src.utils.common import normalize_text

DAYS_PER_YEAR = 365

SRC_STATEMENTS = "BCTC"
SRC_PROPOSAL = "đề nghị"
SRC_SURVEY = "khảo sát"
SRC_CIC = "CIC"
SRC_DERIVED = "tính toán"
SRC_DEFAULT = "mặc định"

GUARANTEE_NAME_MARKER = "bao lanh"
GUARANTEE_REVENUE_RATIO = 0.30
GUARANTEE_TYPES: tuple[tuple[str, float, int, tuple[str, ...]], ...] = (
    ("Bảo lãnh dự thầu", 0.05, 90, ("du thau",)),
    ("Bảo lãnh thực hiện hợp đồng", 0.10, 120, ("thuc hien",)),
    ("Bảo lãnh tạm ứng", 0.30, 90, ("tam ung",)),
    ("Bảo lãnh bảo hành", 0.05, 360, ("bao hanh",)),
    ("Bảo lãnh thanh toán/thuế", 0.30, 90, ("thanh toan", "thue")),
)

LC_NAME_TOKENS = ("lc",)
LC_NAME_PHRASES = ("thu tin dung", "tin dung chung tu")
LC_ASSUMPTIONS: tuple[tuple[str, float], ...] = (
    ("import_ratio", 0.50),
    ("lc_share_of_import", 0.50),
    ("sight_share", 0.50),
    ("deferred_share", 0.50),
    ("sight_days", 30),
    ("deferred_days", 180),
)


@dataclass
class Row:
    """One line of the table: a label, the two years, a unit and a source."""

    label: str
    unit: str
    latest: float | None = None
    plan: float | None = None
    source: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        """Round both years to two decimals."""

        for attr in ("latest", "plan"):
            value = getattr(self, attr)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                object.__setattr__(self, attr, round(value, 2))


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
    """Outstanding balance across other credit institutions, from CIC S10A."""

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
    """True when a facility name denotes a letter of credit."""

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
    """Total value the application plans to execute next year, or None."""

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
    """Which of the three blocks the credit application actually asks for."""

    names = [name for name, _amount in _facilities(proposal)]
    if not names:
        return {"loan": True, "guarantee": True, "lc": True, "stated": False}
    guarantee = any(GUARANTEE_NAME_MARKER in name for name in names)
    lc = lc_turnover_from_file(proposal) is not None
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

    year_sources = (
        ((proposal_extraction or {}).get("business_plan"), "plan_year"),
        ((sitevisit_extraction or {}).get("business_plan_next_year"), "year"),
    )
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
                add(Row(label, "VNĐ", None, _balance(stated, 1.0, days),
                        SRC_PROPOSAL, f"Hạn mức đề nghị / (365 / {days} ngày)"))
            else:
                add(Row(label, "VNĐ", None, _balance(contract_value, share, days),
                        base_source or SRC_DEFAULT,
                        f"× {share:.0%} rồi / (365 / {days} ngày)"))

    # --- LC: planning year only, based on projected COGS ---------------------
    if show_lc:
        assumed = {
            key: _pick(_survey_lc(sitevisit_extraction, key), default)
            for key, default in LC_ASSUMPTIONS
        }
        import_ratio, import_src = assumed["import_ratio"]
        lc_share, lc_share_src = assumed["lc_share_of_import"]
        sight_share, sight_share_src = assumed["sight_share"]
        deferred_share, deferred_share_src = assumed["deferred_share"]
        sight_days, sight_days_src = assumed["sight_days"]
        deferred_days, deferred_days_src = assumed["deferred_days"]

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
    if revenue_latest and revenue_plan:
        ratio = revenue_plan / revenue_latest
        if ratio < 0.01 or ratio > 100:
            table.warnings.append(
                f"Nghi sai đơn vị: doanh thu năm kế hoạch ({revenue_plan:,.0f}) "
                f"lệch {ratio:.4g} lần so với năm gần nhất "
                f"({revenue_latest:,.0f}). Kiểm tra 'source_unit' trong hồ sơ "
                f"nguồn ({revenue_src}) trước khi dùng bảng này."
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
    """Loan need = cycle need − equity funding − other lenders."""

    if need is None:
        return None
    return need - (equity or 0.0) - (other or 0.0)


def _balance(turnover: float | None, share: float, days: float) -> float | None:
    """Average outstanding balance from a turnover, a share and a tenor."""

    if turnover is None:
        return None
    return turnover * share * days / DAYS_PER_YEAR
