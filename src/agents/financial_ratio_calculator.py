"""Pre-compute financial ratios from BCTC structured-extraction JSON.

Line items and their per-year values come from bctc_extraction (see
bctc_extraction.py) — an LLM extraction pass over BCTC raw OCR text — not from
parsing raw OCR text directly here.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from src.agents.bctc_extraction import (
    normalize_period_label,
    resolve_report_years,
)


BALANCE_SHEET = "balance_sheet"
INCOME_STATEMENT = "income_statement"
CASH_FLOW = "cash_flow_statement"


@dataclass
class MetricDefinition:
    """Describe one extractable financial statement line item.

    ``statements`` restricts where a metric may be read from — without it
    "Lợi nhuận sau thuế chưa phân phối" (retained earnings, mã 421 on the
    balance sheet) is picked up as net profit. ``codes`` are Mã số values that
    corroborate a label match, and ``exclude`` blocks labels that merely contain
    an alias ("Tài sản ngắn hạn **khác**", "Phải trả người bán **dài hạn**").
    """

    key: str
    label: str
    aliases: tuple[str, ...]
    statements: tuple[str, ...] = (BALANCE_SHEET,)
    codes: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass
class RatioDefinition:
    """Describe one computed ratio and its formula."""

    key: str
    label: str
    formula: str
    unit: str
    compute: Callable[[dict[str, float]], float | None]


class FinancialRatioCalculator:
    """Extract statement line items by year and compute custom financial ratios."""

    METRICS: tuple[MetricDefinition, ...] = (
        MetricDefinition(
            "net_revenue",
            "Doanh thu thuần",
            ("doanh thu thuần", "doanh thu thuan"),
            statements=(INCOME_STATEMENT,),
            codes=("10",),
        ),
        MetricDefinition(
            "gross_revenue",
            "Doanh thu bán hàng và cung cấp dịch vụ",
            ("doanh thu bán hàng", "doanh thu ban hang"),
            statements=(INCOME_STATEMENT,),
            codes=("01",),
        ),
        MetricDefinition(
            "cogs",
            "Giá vốn hàng bán",
            ("giá vốn hàng bán", "gia von hang ban"),
            statements=(INCOME_STATEMENT,),
            codes=("11",),
        ),
        MetricDefinition(
            "gross_profit",
            "Lợi nhuận gộp",
            ("lợi nhuận gộp", "loi nhuan gop"),
            statements=(INCOME_STATEMENT,),
            codes=("20",),
        ),
        MetricDefinition(
            "financial_expense",
            "Chi phí tài chính",
            ("chi phí tài chính", "chi phi tai chinh"),
            statements=(INCOME_STATEMENT,),
            codes=("22",),
        ),
        MetricDefinition(
            "interest_expense",
            "Chi phí lãi vay",
            ("chi phí lãi vay", "chi phi lai vay"),
            statements=(INCOME_STATEMENT,),
            codes=("23",),
        ),
        MetricDefinition(
            "profit_before_tax",
            "Lợi nhuận trước thuế",
            ("lợi nhuận trước thuế", "loi nhuan truoc thue"),
            # The cash-flow statement opens with the same figure; accept it as a
            # fallback when the income statement row was not extracted.
            statements=(INCOME_STATEMENT, CASH_FLOW),
            codes=("50",),
        ),
        MetricDefinition(
            "net_profit",
            "Lợi nhuận sau thuế",
            ("lợi nhuận sau thuế", "loi nhuan sau thue"),
            # Never the balance sheet: mã 421 "Lợi nhuận sau thuế chưa phân
            # phối" is retained earnings, not the period's net profit.
            statements=(INCOME_STATEMENT,),
            codes=("60",),
            exclude=("chưa phân phối", "chua phan phoi"),
        ),
        MetricDefinition(
            "current_assets",
            "Tài sản ngắn hạn",
            ("tài sản ngắn hạn", "tai san ngan han"),
            codes=("100",),
            exclude=("tài sản ngắn hạn khác", "tai san ngan han khac"),
        ),
        MetricDefinition(
            "cash",
            "Tiền và tương đương tiền",
            (
                "tiền và các khoản tương đương tiền",
                "tien va cac khoan tuong duong tien",
            ),
            codes=("110",),
        ),
        MetricDefinition(
            "accounts_receivable",
            "Phải thu khách hàng",
            (
                "phải thu ngắn hạn của khách hàng",
                "phai thu ngan han cua khach hang",
                "phải thu khách hàng",
                "phai thu khach hang",
            ),
            codes=("131",),
        ),
        MetricDefinition(
            "prepaid_suppliers",
            "Trả trước người bán",
            (
                "trả trước cho người bán ngắn hạn",
                "tra truoc cho nguoi ban ngan han",
                "trả trước người bán",
                "tra truoc nguoi ban",
            ),
            codes=("132",),
        ),
        MetricDefinition(
            "other_receivables",
            "Phải thu khác",
            (
                "phải thu ngắn hạn khác",
                "phai thu ngan han khac",
                "phải thu khác",
                "phai thu khac",
            ),
            codes=("136",),
        ),
        MetricDefinition(
            "inventory",
            "Hàng tồn kho",
            ("hàng tồn kho", "hang ton kho"),
            codes=("140", "141"),
        ),
        MetricDefinition(
            "total_assets",
            "Tổng tài sản",
            (
                "tổng cộng tài sản",
                "tong cong tai san",
                "tổng tài sản",
                "tong tai san",
            ),
            codes=("270",),
        ),
        MetricDefinition(
            "current_liabilities",
            "Nợ ngắn hạn",
            ("nợ ngắn hạn", "no ngan han"),
            codes=("310",),
        ),
        MetricDefinition(
            "accounts_payable",
            "Phải trả người bán",
            (
                "phải trả người bán ngắn hạn",
                "phai tra nguoi ban ngan han",
                "phải trả người bán",
                "phai tra nguoi ban",
            ),
            codes=("311",),
            exclude=(
                "phải trả người bán dài hạn",
                "phai tra nguoi ban dai han",
            ),
        ),
        MetricDefinition(
            "customer_advances",
            "Người mua trả tiền trước",
            (
                "người mua trả tiền trước ngắn hạn",
                "nguoi mua tra tien truoc ngan han",
                "người mua trả tiền trước",
                "nguoi mua tra tien truoc",
            ),
            codes=("312",),
        ),
        MetricDefinition(
            "short_term_debt",
            "Vay và nợ thuê tài chính ngắn hạn",
            ("vay và nợ thuê tài chính ngắn hạn", "vay ngan han"),
            codes=("320",),
        ),
        MetricDefinition(
            "long_term_debt",
            "Vay và nợ thuê tài chính dài hạn",
            ("vay và nợ thuê tài chính dài hạn", "vay dai han"),
            codes=("338",),
        ),
        MetricDefinition(
            "total_liabilities",
            "Nợ phải trả",
            ("nợ phải trả", "no phai tra", "tong no phai tra", "tổng nợ phải trả"),
            codes=("300",),
        ),
        MetricDefinition(
            "equity",
            "Vốn chủ sở hữu",
            ("vốn chủ sở hữu", "von chu so huu"),
            codes=("400", "410"),
        ),
    )

    RATIO_DEFINITIONS: tuple[RatioDefinition, ...] = (
        RatioDefinition(
            "net_working_capital",
            "Vốn lưu động ròng",
            "Tài sản ngắn hạn - Nợ ngắn hạn",
            "value",
            lambda m: _safe_sub(m.get("current_assets"), m.get("current_liabilities")),
        ),
        RatioDefinition(
            "dio",
            "Số ngày tồn kho",
            "Hàng tồn kho cuối kỳ / Giá vốn hàng bán * 365",
            "days",
            lambda m: _safe_div(m.get("inventory"), m.get("cogs"), 365),
        ),
        RatioDefinition(
            "dso",
            "Số ngày phải thu",
            "Phải thu cuối kỳ / Doanh thu thuần * 365",
            "days",
            lambda m: _safe_div(m.get("accounts_receivable"), m.get("net_revenue"), 365),
        ),
        RatioDefinition(
            "prepaid_supplier_days",
            "Số ngày trả trước người bán",
            "Trả trước người bán cuối kỳ / Doanh thu thuần * 365",
            "days",
            lambda m: _safe_div(m.get("prepaid_suppliers"), m.get("net_revenue"), 365),
        ),
        RatioDefinition(
            "receivable_plus_prepaid_days",
            "Số ngày phải thu + trả trước",
            "DSO + Số ngày trả trước người bán",
            "days",
            lambda m: _safe_sum(
                _safe_div(m.get("accounts_receivable"), m.get("net_revenue"), 365),
                _safe_div(m.get("prepaid_suppliers"), m.get("net_revenue"), 365),
            ),
        ),
        RatioDefinition(
            "dpo",
            "Số ngày phải trả",
            "Phải trả cuối kỳ / Giá vốn hàng bán * 365",
            "days",
            lambda m: _safe_div(m.get("accounts_payable"), m.get("cogs"), 365),
        ),
        RatioDefinition(
            "customer_advance_days",
            "Số ngày người mua trả trước",
            "Người mua trả trước cuối kỳ / Giá vốn hàng bán * 365",
            "days",
            lambda m: _safe_div(m.get("customer_advances"), m.get("cogs"), 365),
        ),
        RatioDefinition(
            "payable_plus_advance_days",
            "Số ngày phải trả + NMTTT",
            "DPO + Số ngày người mua trả trước",
            "days",
            lambda m: _safe_sum(
                _safe_div(m.get("accounts_payable"), m.get("cogs"), 365),
                _safe_div(m.get("customer_advances"), m.get("cogs"), 365),
            ),
        ),
        RatioDefinition(
            "cash_conversion_cycle",
            "Số ngày thiếu tiền (CCC)",
            "DSO + DIO + Số ngày trả trước người bán - DPO - Số ngày người mua trả trước",
            "days",
            lambda m: _safe_cash_conversion_cycle(m),
        ),
        RatioDefinition(
            "current_ratio",
            "Chỉ số thanh toán hiện hành",
            "Tài sản ngắn hạn / Nợ ngắn hạn",
            "x",
            lambda m: _safe_div(m.get("current_assets"), m.get("current_liabilities")),
        ),
        RatioDefinition(
            "quick_ratio_custom",
            "Chỉ số thanh toán nhanh",
            "(Tài sản ngắn hạn - Trả trước người bán - Phải thu khác) / Nợ ngắn hạn",
            "x",
            lambda m: _safe_div(
                _safe_sub(
                    m.get("current_assets"),
                    m.get("prepaid_suppliers"),
                    m.get("other_receivables"),
                ),
                m.get("current_liabilities"),
            ),
        ),
        RatioDefinition(
            "asset_turnover",
            "Vòng quay tài sản",
            "Doanh thu thuần / Tổng tài sản cuối kỳ",
            "x",
            lambda m: _safe_div(m.get("net_revenue"), m.get("total_assets")),
        ),
        RatioDefinition(
            "revenue_growth",
            "Tỷ lệ tăng trưởng doanh thu",
            "(Doanh thu thuần năm hiện tại - năm trước) / năm trước",
            "%",
            lambda m: None,
        ),
        RatioDefinition(
            "gross_margin",
            "Biên LN gộp",
            "Lợi nhuận gộp / Doanh thu thuần",
            "%",
            lambda m: _safe_div(m.get("gross_profit"), m.get("net_revenue")),
        ),
        RatioDefinition(
            "ros",
            "ROS",
            "Lợi nhuận sau thuế / Doanh thu thuần",
            "%",
            lambda m: _safe_div(m.get("net_profit"), m.get("net_revenue")),
        ),
        RatioDefinition(
            "roe",
            "ROE",
            "Lợi nhuận sau thuế / Vốn chủ sở hữu",
            "%",
            lambda m: _safe_div(m.get("net_profit"), m.get("equity")),
        ),
        RatioDefinition(
            "roa",
            "ROA",
            "Lợi nhuận sau thuế / Tổng tài sản cuối kỳ",
            "%",
            lambda m: _safe_div(m.get("net_profit"), m.get("total_assets")),
        ),
        RatioDefinition(
            "liabilities_to_equity",
            "Nợ phải trả/VCSH",
            "Nợ phải trả / Vốn chủ sở hữu",
            "x",
            lambda m: _safe_div(m.get("total_liabilities"), m.get("equity")),
        ),
        RatioDefinition(
            "debt_to_equity",
            "Nợ vay/VCSH",
            "(Vay ngắn hạn + Vay dài hạn) / Vốn chủ sở hữu",
            "x",
            lambda m: _safe_div(
                _safe_sum(
                    m.get("short_term_debt"),
                    m.get("long_term_debt"),
                ),
                m.get("equity"),
            ),
        ),
        RatioDefinition(
            "liabilities_to_assets",
            "Tổng nợ phải trả/Tổng tài sản",
            "Nợ phải trả / Tổng tài sản",
            "%",
            lambda m: _safe_div(m.get("total_liabilities"), m.get("total_assets")),
        ),
        RatioDefinition(
            "ebitda_to_interest",
            "EBITDA/Lãi vay",
            "(Lợi nhuận trước thuế + Chi phí lãi vay) / Chi phí lãi vay; chưa cộng khấu hao nếu tài liệu không có",
            "x",
            lambda m: _safe_div(
                _safe_sum(m.get("profit_before_tax"), m.get("interest_expense")),
                m.get("interest_expense"),
            ),
        ),
    )

    def build_analysis_block(self, documents: list[dict[str, Any]]) -> str:
        """Return a markdown block with extracted line items and computed ratios."""
        yearly_metrics = self.extract_yearly_metrics(documents)
        if not yearly_metrics:
            return ""

        ratios = self.compute_ratios(yearly_metrics)
        if not ratios:
            return ""

        return self.format_markdown(
            yearly_metrics,
            ratios,
            self.source_files_by_year(documents),
        )

    # A year-on-year swing this large is not a business event, it is a unit that
    # never got scaled. Deliberately looser than the 100x backstop in
    # credit_need_calculator, which compares a plan against the year it was
    # built from — two figures that cannot be far apart. Consecutive BCTC years
    # can be: a company incorporated mid-year books a few hundred million and
    # then a full year at sixty billion, and at 100x that customer gets told its
    # own growth is a data error. 1000 is the smallest gap a real unit mistake
    # can produce, since nghìn đồng is the finest unit resolve_money_multiplier
    # knows, so nothing genuine is caught and nothing spurious is.
    UNIT_ANOMALY_METRICS = ("net_revenue", "total_assets")
    UNIT_ANOMALY_LOW = 0.001
    UNIT_ANOMALY_HIGH = 1000

    def detect_unit_anomalies(
        self,
        yearly_metrics: dict[str, dict[str, float]],
    ) -> list[str]:
        """Flag consecutive years whose magnitudes cannot both be in đồng.

        The BCTC pass reads the unit printed on each statement and scales here,
        but a bundle can carry two statements printed in different units, and a
        model can miss the unit line on one of them. Then one year is a million
        times the other and every ratio spanning the pair is meaningless.

        Says so rather than guessing which year is wrong — either could be, and
        silently rescaling one would turn a visible inconsistency into an
        invisible fabrication.
        """

        warnings: list[str] = []
        years = sorted(yearly_metrics)
        for metric in self.UNIT_ANOMALY_METRICS:
            for earlier, later in zip(years, years[1:]):
                a = yearly_metrics.get(earlier, {}).get(metric)
                b = yearly_metrics.get(later, {}).get(metric)
                if not a or not b:
                    continue
                ratio = b / a
                if self.UNIT_ANOMALY_LOW < ratio < self.UNIT_ANOMALY_HIGH:
                    continue
                warnings.append(
                    f"NGHI SAI ĐƠN VỊ: {metric} {later} ({b:,.0f}) lệch "
                    f"{ratio:.4g} lần so với {earlier} ({a:,.0f}). Nhiều khả "
                    f"năng một trong hai bảng in bằng triệu/tỷ đồng mà không "
                    f"ghi rõ đơn vị. KHÔNG dùng tăng trưởng hay tỷ lệ bắc cầu "
                    f"giữa hai năm này; nêu rõ nghi vấn đơn vị trong báo cáo."
                )
        return warnings

    def source_files_by_year(
        self,
        documents: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Which uploaded file supplied each year's figures.

        The block is the only place a reader meets these numbers, and every
        figure in a credit memo has to be attributable to a document. Without
        this the agent is required to cite a source it was never told, and the
        only identifier in front of it is the block's own heading — which is how
        an internal heading ends up printed in a customer-facing report.

        Kept per year rather than one flat list because two statements usually
        overlap by a year, and a merged list would attribute a column to a file
        that never carried it.
        """

        by_year: dict[str, set[str]] = {}
        for document in documents:
            extraction = document.get("bctc_extraction")
            if not isinstance(extraction, dict):
                continue
            filename = str(document.get("filename") or "").strip()
            if not filename:
                continue
            current_year, previous_year = resolve_report_years(extraction)
            for statement_key in self.STATEMENT_KEYS:
                statement = extraction.get(statement_key)
                if not isinstance(statement, dict):
                    continue
                for line_item in statement.get("line_items") or []:
                    if not isinstance(line_item, dict):
                        continue
                    values = line_item.get("values")
                    if not isinstance(values, dict):
                        continue
                    for year in values:
                        label = normalize_period_label(
                            year, current_year, previous_year
                        )
                        by_year.setdefault(label, set()).add(filename)
        return {year: sorted(names) for year, names in sorted(by_year.items())}

    STATEMENT_KEYS = (BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW)

    # Match scoring: a label that equals an alias outright beats a label that
    # merely contains one; a corroborating Mã số outweighs both; a code with no
    # label support is the weakest signal that is still worth using.
    EXACT_LABEL_SCORE = 60
    ALIAS_BASE_SCORE = 10
    CODE_AGREEMENT_BONUS = 100
    CODE_ONLY_SCORE = 40

    def extract_yearly_metrics(
        self,
        documents: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """Extract financial statement line items by year from bctc_extraction JSON.

        Reads each document's ``bctc_extraction`` (the structured JSON produced
        by the BCTC extraction pass — see bctc_extraction.py), not raw OCR
        text. A document without a successful extraction (not a BCTC, LLM
        failed, or no extraction LLM configured) contributes nothing.
        """
        yearly_metrics: dict[str, dict[str, float]] = {}
        # (year, metric_key) -> match score. Keeping the best-scoring row instead
        # of the first one makes the result independent of line-item order.
        best_score: dict[tuple[str, str], int] = {}

        for document in documents:
            extraction = document.get("bctc_extraction")
            if not isinstance(extraction, dict):
                continue
            # Columns named by position ("Số cuối kỳ") only mean something
            # relative to the statement they came from, so the anchor is read
            # per document rather than once for the whole set.
            current_year, previous_year = resolve_report_years(extraction)

            for statement_key in self.STATEMENT_KEYS:
                statement = extraction.get(statement_key)
                if not isinstance(statement, dict):
                    continue
                for line_item in statement.get("line_items") or []:
                    if not isinstance(line_item, dict):
                        continue
                    label = line_item.get("label") or ""
                    values = line_item.get("values")
                    if not label or not isinstance(values, dict):
                        continue

                    matched = self.match_metric(
                        label,
                        line_item.get("code"),
                        statement_key,
                    )
                    if matched is None:
                        continue
                    metric, score = matched

                    for year, raw_value in values.items():
                        try:
                            value = float(raw_value)
                        except (TypeError, ValueError):
                            continue
                        # Normalized again here, not just at extraction time:
                        # this also covers extractions produced before that
                        # existed, and a mixed spelling silently splits one
                        # year into two columns whose growth ratio then
                        # compares the year against itself. Idempotent.
                        year_key = normalize_period_label(
                            year, current_year, previous_year
                        )
                        slot = (year_key, metric.key)
                        if score <= best_score.get(slot, -1):
                            continue
                        yearly_metrics.setdefault(year_key, {})
                        yearly_metrics[year_key][metric.key] = value
                        best_score[slot] = score

        return dict(sorted(yearly_metrics.items()))

    @classmethod
    def match_metric(
        cls,
        label: str,
        code: Any,
        statement_key: str,
    ) -> tuple[MetricDefinition, int] | None:
        """Map one statement line to a metric, with a confidence score.

        The label is the primary key and the Mã số only corroborates it. Codes
        extracted from poor scans are frequently wrong — in one real sample the
        model returned 311 (phải trả người bán) for "Nợ ngắn hạn" — so letting a
        code override a clear label would introduce errors rather than remove
        them. A code is used on its own only when no label matches at all.
        """

        normalized_label = _normalize_text(label)
        code_text = "" if code is None else str(code).strip()

        candidates: list[tuple[MetricDefinition, int]] = []
        for metric in cls.METRICS:
            if statement_key not in metric.statements:
                continue
            if any(
                _normalize_text(term) in normalized_label
                for term in metric.exclude
            ):
                continue
            hits = [
                _normalize_text(alias)
                for alias in metric.aliases
                if _normalize_text(alias) in normalized_label
            ]
            if not hits:
                continue
            longest = max(hits, key=len)
            # A longer alias is a more specific match than a shorter one.
            score = (
                cls.EXACT_LABEL_SCORE
                if longest == normalized_label
                else cls.ALIAS_BASE_SCORE + len(longest)
            )
            code_rank = _code_rank(code_text, metric.codes)
            if code_rank is not None:
                # Codes are listed aggregate-first ("140" before "141", "400"
                # before "410"), so the earlier code wins a tie deterministically
                # and the roll-up line beats its own sub-line.
                score += cls.CODE_AGREEMENT_BONUS + (len(metric.codes) - code_rank)
            if statement_key == metric.statements[0]:
                score += 1
            candidates.append((metric, score))

        if candidates:
            return max(candidates, key=lambda item: item[1])

        # No label matched — OCR may have mangled it beyond recognition. Fall
        # back to the code alone, which is the only remaining signal.
        for metric in cls.METRICS:
            if statement_key not in metric.statements:
                continue
            if _code_matches(code_text, metric.codes):
                return metric, cls.CODE_ONLY_SCORE
        return None

    def compute_ratios(
        self,
        yearly_metrics: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        """Compute configured financial ratios from extracted yearly metrics."""
        yearly_ratios: dict[str, dict[str, float]] = {}
        previous_net_revenue: float | None = None

        for year in sorted(yearly_metrics):
            metrics = yearly_metrics[year]
            ratio_values: dict[str, float] = {}
            for ratio in self.RATIO_DEFINITIONS:
                if ratio.key == "revenue_growth":
                    value = _safe_growth(metrics.get("net_revenue"), previous_net_revenue)
                else:
                    value = ratio.compute(metrics)
                if value is not None:
                    ratio_values[ratio.key] = value
            if metrics.get("net_revenue") is not None:
                previous_net_revenue = metrics.get("net_revenue")
            yearly_ratios[year] = ratio_values

        return yearly_ratios

    def format_markdown(
        self,
        yearly_metrics: dict[str, dict[str, float]],
        yearly_ratios: dict[str, dict[str, float]],
        source_files: dict[str, list[str]] | None = None,
    ) -> str:
        """Format extracted metrics and computed ratios as markdown for the agent."""
        years = sorted(yearly_metrics)
        lines = [
            "[PRE-COMPUTED FINANCIAL METRICS]",
            "The following figures were calculated deterministically from extracted documents before LLM analysis.",
            "Use these values as the primary source for ratio tables when available.",
            "If a value is missing, say it is unavailable instead of estimating it.",
            "Đơn vị mọi giá trị tiền tệ (line items) trong block này: **tỷ VNĐ** "
            "(đã chia 10^9, 2 chữ số thập phân). Giữ nguyên đơn vị này khi trình bày.",
            "",
        ]
        # Above the numbers, not below them: a reader who has already worked
        # through the table has drawn the conclusion the warning exists to stop.
        anomalies = self.detect_unit_anomalies(yearly_metrics)
        if anomalies:
            lines.extend(anomalies)
            lines.append("")
        if source_files:
            lines.extend(
                [
                    "NGUỒN SỐ LIỆU — dùng tên file dưới đây khi trích dẫn. "
                    "\"[PRE-COMPUTED FINANCIAL METRICS]\" là tên khối kỹ thuật "
                    "trong prompt, TUYỆT ĐỐI không ghi tên khối này vào báo cáo:",
                ]
            )
            lines.extend(
                f"- {year}: {', '.join(names)}"
                for year, names in source_files.items()
            )
            lines.append("")
        lines.append("Important formula notes from the financial analysis template:")
        lines.extend(f"- {ratio.label}: {ratio.formula}" for ratio in self.RATIO_DEFINITIONS)
        lines.extend(
            [
                "",
                "Extracted financial statement line items:",
                self._format_table(years, yearly_metrics, self.METRICS),
            ]
        )
        lines.extend(
            [
                "",
                "Computed financial ratios:",
                self._format_ratio_table(years, yearly_ratios),
            ]
        )

        warnings = self._validation_warnings(years, yearly_metrics)
        if warnings:
            lines.extend(
                [
                    "",
                    "Data quality warnings (possible OCR/extraction errors — verify "
                    "against source before relying on these figures):",
                ]
            )
            lines.extend(f"- {warning}" for warning in warnings)

        lines.append("[/PRE-COMPUTED FINANCIAL METRICS]")
        return "\n".join(lines)

    def _validation_warnings(
        self,
        years: list[str],
        yearly_metrics: dict[str, dict[str, float]],
        tolerance: float = 0.02,
    ) -> list[str]:
        """Flag figures that fail basic accounting sanity checks."""
        warnings: list[str] = []
        for year in years:
            metrics = yearly_metrics.get(year, {})
            assets = metrics.get("total_assets")
            liabilities = metrics.get("total_liabilities")
            equity = metrics.get("equity")

            # Accounting identity: Tổng tài sản = Nợ phải trả + Vốn chủ sở hữu.
            if None not in (assets, liabilities, equity) and assets:
                expected = liabilities + equity
                if abs(assets - expected) / abs(assets) > tolerance:
                    warnings.append(
                        f"{year}: Tổng tài sản ({_format_number(assets, 'value')}) "
                        f"≠ Nợ phải trả + VCSH "
                        f"({_format_number(expected, 'value')}); "
                        "chênh lệch vượt ngưỡng cho phép."
                    )

            # Gross profit should not exceed net revenue.
            revenue = metrics.get("net_revenue")
            gross_profit = metrics.get("gross_profit")
            if None not in (revenue, gross_profit) and revenue and gross_profit > revenue:
                warnings.append(
                    f"{year}: Lợi nhuận gộp "
                    f"({_format_number(gross_profit, 'value')}) lớn hơn "
                    f"Doanh thu thuần ({_format_number(revenue, 'value')})."
                )

            # Any negative that should structurally be non-negative.
            for key, label in (
                ("total_assets", "Tổng tài sản"),
                ("net_revenue", "Doanh thu thuần"),
                ("equity", "Vốn chủ sở hữu"),
            ):
                value = metrics.get(key)
                if value is not None and value < 0:
                    warnings.append(
                        f"{year}: {label} âm ({_format_number(value, 'value')}) "
                        "— bất thường, nghi ngờ lỗi trích xuất."
                    )
        return warnings

    def _format_table(
        self,
        years: list[str],
        values_by_year: dict[str, dict[str, float]],
        definitions: tuple[MetricDefinition, ...],
    ) -> str:
        header = "| Chỉ tiêu | " + " | ".join(years) + " |"
        separator = "|---|" + "|".join("---:" for _ in years) + "|"
        rows = [header, separator]
        for definition in definitions:
            cells = [
                _format_number(values_by_year.get(year, {}).get(definition.key), "value")
                for year in years
            ]
            if any(cell != "N/A" for cell in cells):
                rows.append(f"| {definition.label} | " + " | ".join(cells) + " |")
        return "\n".join(rows)

    def _format_ratio_table(
        self,
        years: list[str],
        yearly_ratios: dict[str, dict[str, float]],
    ) -> str:
        header = "| Chỉ số | Công thức | " + " | ".join(years) + " |"
        separator = "|---|---|" + "|".join("---:" for _ in years) + "|"
        rows = [header, separator]
        for definition in self.RATIO_DEFINITIONS:
            cells = [
                _format_number(yearly_ratios.get(year, {}).get(definition.key), definition.unit)
                for year in years
            ]
            if any(cell != "N/A" for cell in cells):
                rows.append(
                    f"| {definition.label} | {definition.formula} | "
                    + " | ".join(cells)
                    + " |"
                )
        return "\n".join(rows)

def _normalize_text(text: str) -> str:
    """Lowercase and remove Vietnamese accents for robust matching."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _code_rank(code_text: str, codes: tuple[str, ...]) -> int | None:
    """Index of ``code_text`` within a metric's whitelist, else None.

    Leading zeros are ignored because statements write the same code as "01" or
    "1" depending on the template.
    """
    if not code_text or not codes:
        return None
    candidate = code_text.strip()
    variants = {candidate, candidate.lstrip("0") or "0"}
    for index, code in enumerate(codes):
        code = code.strip()
        if code in variants or (code.lstrip("0") or "0") in variants:
            return index
    return None


def _code_matches(code_text: str, codes: tuple[str, ...]) -> bool:
    """Whether a Mã số is in a metric's whitelist."""
    return _code_rank(code_text, codes) is not None


def _safe_div(
    numerator: float | None,
    denominator: float | None,
    multiplier: float = 1.0,
) -> float | None:
    """Safely divide two values."""
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator * multiplier


def _safe_sum(*values: float | None) -> float | None:
    """Return sum only when at least one input value exists."""
    available = [value for value in values if value is not None]
    if not available:
        return None
    return sum(available)


def _safe_sub(*values: float | None) -> float | None:
    """Subtract all following values from the first value."""
    if not values or values[0] is None:
        return None
    result = values[0]
    for value in values[1:]:
        if value is not None:
            result -= value
    return result


def _safe_growth(
    current: float | None,
    previous: float | None,
) -> float | None:
    """Compute growth rate against previous period."""
    if current is None or previous in {None, 0}:
        return None
    return (current - previous) / previous


def _safe_cash_conversion_cycle(metrics: dict[str, float]) -> float | None:
    """Compute CCC using the custom template formula."""
    dso = _safe_div(metrics.get("accounts_receivable"), metrics.get("net_revenue"), 365)
    dio = _safe_div(metrics.get("inventory"), metrics.get("cogs"), 365)
    prepaid_days = _safe_div(metrics.get("prepaid_suppliers"), metrics.get("net_revenue"), 365)
    dpo = _safe_div(metrics.get("accounts_payable"), metrics.get("cogs"), 365)
    advance_days = _safe_div(metrics.get("customer_advances"), metrics.get("cogs"), 365)

    if all(value is None for value in [dso, dio, prepaid_days, dpo, advance_days]):
        return None
    return (
        (dso or 0)
        + (dio or 0)
        + (prepaid_days or 0)
        - (dpo or 0)
        - (advance_days or 0)
    )


def _format_number(value: float | None, unit: str) -> str:
    """Format ratio values for prompt injection into the analysis agent."""
    if value is None:
        return "N/A"
    if unit == "%":
        return f"{value * 100:.1f}%"
    if unit == "days":
        return f"{value:.1f} ngày"
    if unit == "x":
        return f"{value:.2f}x"
    # Monetary values ("value" unit): show in tỷ VNĐ, Vietnamese formatting
    # ('.' thousands, ',' decimal). The unit is stated once in the block header.
    scaled = value / 1_000_000_000
    formatted = f"{scaled:,.2f}"
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
