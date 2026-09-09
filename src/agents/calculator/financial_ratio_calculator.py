"""Pre-compute financial ratios from FS structured-extraction JSON"""

import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Callable

from src.utils.report.formatting import format_vn_number, render_money

# Declared here rather than imported from prompt_blocks: that module imports
# this one, so the constant has to live on the side that has no dependency.
METRICS_BLOCK_HEADING = "[PRE-COMPUTED FINANCIAL METRICS]"
from src.agents.extraction.financial_statement_extraction import (
    normalize_period_label,
    resolve_report_years,
)


from src.agents.extraction.financial_statement_extraction import (
    iter_line_items,
)

BALANCE_SHEET = "balance_sheet"
INCOME_STATEMENT = "income_statement"
CASH_FLOW = "cash_flow_statement"

DAYS_PER_YEAR = 365


@dataclass
class MetricDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    statements: tuple[str, ...] = (BALANCE_SHEET,)
    codes: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass
class RatioDefinition:
    key: str
    label: str
    formula: str
    unit: str
    compute: Callable[[dict[str, float]], float | None]


class FinancialRatioCalculator:
    METRICS: tuple[MetricDefinition, ...] = (
        MetricDefinition(
            "net_revenue",
            "Doanh thu thuần",
            ("doanh thu thuần",),
            statements=(INCOME_STATEMENT,),
            codes=("10",),
        ),
        MetricDefinition(
            "gross_revenue",
            "Doanh thu bán hàng và cung cấp dịch vụ",
            ("doanh thu bán hàng",),
            statements=(INCOME_STATEMENT,),
            codes=("01",),
        ),
        MetricDefinition(
            "cogs",
            "Giá vốn hàng bán",
            ("giá vốn hàng bán",),
            statements=(INCOME_STATEMENT,),
            codes=("11",),
        ),
        MetricDefinition(
            "gross_profit",
            "Lợi nhuận gộp",
            ("lợi nhuận gộp",),
            statements=(INCOME_STATEMENT,),
            codes=("20",),
        ),
        MetricDefinition(
            "financial_expense",
            "Chi phí tài chính",
            ("chi phí tài chính",),
            statements=(INCOME_STATEMENT,),
            codes=("22",),
        ),
        MetricDefinition(
            "interest_expense",
            "Chi phí lãi vay",
            ("chi phí lãi vay",),
            statements=(INCOME_STATEMENT,),
            codes=("23",),
        ),
        MetricDefinition(
            "profit_before_tax",
            "Lợi nhuận trước thuế",
            ("lợi nhuận trước thuế",),
            statements=(INCOME_STATEMENT, CASH_FLOW),
            codes=("50",),
        ),
        MetricDefinition(
            "net_profit",
            "Lợi nhuận sau thuế",
            ("lợi nhuận sau thuế",),
            statements=(INCOME_STATEMENT,),
            codes=("60",),
            exclude=("chưa phân phối", "chua phan phoi"),
        ),
        MetricDefinition(
            "current_assets",
            "Tài sản ngắn hạn",
            ("tài sản ngắn hạn",),
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
            ("phải thu ngắn hạn của khách hàng", "phải thu khách hàng"),
            codes=("131",),
        ),
        MetricDefinition(
            "prepaid_suppliers",
            "Trả trước người bán",
            ("trả trước cho người bán ngắn hạn", "trả trước người bán"),
            codes=("132",),
        ),
        MetricDefinition(
            "other_receivables",
            "Phải thu khác",
            ("phải thu ngắn hạn khác", "phải thu khác"),
            codes=("136",),
        ),
        MetricDefinition(
            "inventory",
            "Hàng tồn kho",
            ("hàng tồn kho",),
            codes=("140", "141"),
        ),
        MetricDefinition(
            "total_assets",
            "Tổng tài sản",
            ("tổng cộng tài sản", "tổng tài sản"),
            codes=("270",),
        ),
        # The other side of the same identity. Without it the block offered no
        # figure for "Tổng nguồn vốn" at all, and the agent filled that row with
        # "Nợ phải trả" — the only number it had that looked like a total on the
        # capital side. Equal to total_assets by construction, so a report where
        # the two rows differ is reporting a misread, not a business fact.
        MetricDefinition(
            "total_capital",
            "Tổng cộng nguồn vốn",
            ("tổng cộng nguồn vốn", "tổng nguồn vốn"),
            codes=("440",),
        ),
        MetricDefinition(
            "current_liabilities",
            "Nợ ngắn hạn",
            ("nợ ngắn hạn",),
            codes=("310",),
        ),
        MetricDefinition(
            "accounts_payable",
            "Phải trả người bán",
            ("phải trả người bán ngắn hạn", "phải trả người bán"),
            codes=("311",),
            exclude=(
                "phải trả người bán dài hạn",
                "phai tra nguoi ban dai han",
            ),
        ),
        MetricDefinition(
            "customer_advances",
            "Người mua trả tiền trước",
            ("người mua trả tiền trước ngắn hạn", "người mua trả tiền trước"),
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
            ("nợ phải trả", "tong no phai tra"),
            codes=("300",),
        ),
        MetricDefinition(
            "equity",
            "Vốn chủ sở hữu",
            ("vốn chủ sở hữu",),
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
            lambda m: _days(m, "inventory", "cogs"),
        ),
        RatioDefinition(
            "dso",
            "Số ngày phải thu",
            "Phải thu cuối kỳ / Doanh thu thuần * 365",
            "days",
            lambda m: _days(m, "accounts_receivable", "net_revenue"),
        ),
        RatioDefinition(
            "prepaid_supplier_days",
            "Số ngày trả trước người bán",
            "Trả trước người bán cuối kỳ / Doanh thu thuần * 365",
            "days",
            lambda m: _days(m, "prepaid_suppliers", "net_revenue"),
        ),
        RatioDefinition(
            "receivable_plus_prepaid_days",
            "Số ngày phải thu + trả trước",
            "DSO + Số ngày trả trước người bán",
            "days",
            lambda m: _safe_sum(
                _days(m, "accounts_receivable", "net_revenue"),
                _days(m, "prepaid_suppliers", "net_revenue"),
            ),
        ),
        RatioDefinition(
            "dpo",
            "Số ngày phải trả",
            "Phải trả cuối kỳ / Giá vốn hàng bán * 365",
            "days",
            lambda m: _days(m, "accounts_payable", "cogs"),
        ),
        RatioDefinition(
            "customer_advance_days",
            "Số ngày người mua trả trước",
            "Người mua trả trước cuối kỳ / Giá vốn hàng bán * 365",
            "days",
            lambda m: _days(m, "customer_advances", "cogs"),
        ),
        RatioDefinition(
            "payable_plus_advance_days",
            "Số ngày phải trả + NMTTT",
            "DPO + Số ngày người mua trả trước",
            "days",
            lambda m: _safe_sum(
                _days(m, "accounts_payable", "cogs"),
                _days(m, "customer_advances", "cogs"),
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

    @classmethod
    def metrics_from_documents(cls, documents: list[Any]) -> dict[str, dict[str, float]]:
        """Yearly metrics for a list of ClassifiedDocument or their dicts.

        The three lines this replaces — build a calculator, asdict the
        documents, extract — were written out at four call sites across two
        modules. The arithmetic is cheap, so the duplication cost nothing at
        runtime; what it cost was a single place to change. Any filter or
        correction applied to the metrics had to be applied four times, and the
        first time three of the four were updated, the metrics block and the
        credit-need block would disagree inside one report with nothing raising.
        """

        payload = [
            doc if isinstance(doc, dict) else asdict(doc) for doc in documents
        ]
        return cls().extract_yearly_metrics(payload)

    def build_analysis_block(self, documents: list[dict[str, Any]]) -> str:
        """Return a markdown block with extracted line items and computed ratios."""
        yearly_metrics = self.metrics_from_documents(documents)
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

    UNIT_ANOMALY_METRICS = ("net_revenue", "total_assets")
    UNIT_ANOMALY_LOW = 0.001
    UNIT_ANOMALY_HIGH = 1000

    def detect_unit_anomalies(
        self,
        yearly_metrics: dict[str, dict[str, float]],
    ) -> list[str]:
        """Flag consecutive years whose magnitudes cannot both be in đồng."""

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
                    f"SUSPECTED UNIT MISMATCH: {metric} {later} ({b:,.0f}) is "
                    f"{ratio:.4g}x {earlier} ({a:,.0f}). One of the two "
                    f"statements was most likely printed in millions or "
                    f"billions of đồng without saying so. Do NOT compute growth "
                    f"or any ratio spanning these two years; state the unit "
                    f"doubt in the report."
                )
        return warnings

    IDENTITY_TOLERANCE = 0.01

    NON_NEGATIVE_METRICS = (
        ("total_assets", "Tổng tài sản"),
        ("net_revenue", "Doanh thu thuần"),
        ("equity", "Vốn chủ sở hữu"),
    )

    def data_quality_warnings(
        self,
        yearly_metrics: dict[str, dict[str, float]],
    ) -> list[str]:
        """Flag years whose figures contradict the statement's own arithmetic."""

        warnings: list[str] = []
        for year in sorted(yearly_metrics):
            m = yearly_metrics[year]

            def _has(*keys: str) -> bool:
                return all(isinstance(m.get(key), (int, float)) for key in keys)

            def _money(key: str) -> str:
                return _format_number(m[key], "value")

            def _off(actual: float, expected: float) -> bool:
                scale = max(abs(actual), abs(expected))
                return (
                    scale > 0
                    and abs(actual - expected) / scale > self.IDENTITY_TOLERANCE
                )

            if _has("gross_profit", "net_revenue", "cogs"):
                expected = m["net_revenue"] - m["cogs"]
                if _off(m["gross_profit"], expected):
                    warnings.append(
                        f"FIGURES DO NOT RECONCILE ({year}): gross profit reads "
                        f"{_money('gross_profit')}, but net revenue "
                        f"{_money('net_revenue')} minus COGS {_money('cogs')} = "
                        f"{_format_number(expected, 'value')}. At least one of the "
                        f"three was misread — check the original statement first."
                    )
            elif (
                _has("gross_profit", "net_revenue")
                and m["gross_profit"] > m["net_revenue"]
            ):
                warnings.append(
                    f"FIGURES DO NOT RECONCILE ({year}): gross profit "
                    f"{_money('gross_profit')} exceeds net revenue "
                    f"{_money('net_revenue')}. Check the original statement first."
                )

            if (
                _has("net_revenue", "gross_revenue")
                and m["net_revenue"] > m["gross_revenue"]
            ):
                warnings.append(
                    f"FIGURES DO NOT RECONCILE ({year}): net revenue "
                    f"{_money('net_revenue')} exceeds gross revenue "
                    f"{_money('gross_revenue')}, yet revenue deductions cannot be "
                    f"negative. Check the original statement first."
                )

            if _has("total_assets", "total_liabilities", "equity"):
                expected = m["total_liabilities"] + m["equity"]
                if _off(m["total_assets"], expected):
                    warnings.append(
                        f"FIGURES DO NOT RECONCILE ({year}): total assets "
                        f"{_money('total_assets')} differ from liabilities "
                        f"{_money('total_liabilities')} plus equity "
                        f"{_money('equity')} = "
                        f"{_format_number(expected, 'value')}. The balance sheet "
                        f"does not balance — check the original statement first."
                    )

            for key, label in self.NON_NEGATIVE_METRICS:
                if _has(key) and m[key] < 0:
                    warnings.append(
                        f"FIGURES DO NOT RECONCILE ({year}): {label} is negative "
                        f"({_money(key)}) — abnormal, likely an extraction error."
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
            extraction = document.get("financial_statement_extraction")
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
                for _, _, values, _ in iter_line_items(statement):
                    for year in values:
                        label = normalize_period_label(
                            year, current_year, previous_year
                        )
                        by_year.setdefault(label, set()).add(filename)
        return {year: sorted(names) for year, names in sorted(by_year.items())}

    STATEMENT_KEYS = (BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW)

    EXACT_LABEL_SCORE = 60
    ALIAS_BASE_SCORE = 10
    CODE_AGREEMENT_BONUS = 100
    CODE_ONLY_SCORE = 40

    LABEL_TIER = 1
    CODE_ONLY_TIER = 0

    SOURCE_RANK = {"xml": 2, "llm": 1, "": 1}

    def extract_yearly_metrics(
        self,
        documents: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """Extract financial statement line items by year from financial_statement_extraction JSON.

        Reads each document's ``financial_statement_extraction`` (the structured JSON produced
        by the BCTC extraction pass — see financial_statement_extraction.py), not raw OCR
        text. A document without a successful extraction (not a BCTC, LLM
        failed, or no extraction LLM configured) contributes nothing.
        """
        yearly_metrics: dict[str, dict[str, float]] = {}
        best_score: dict[tuple[str, str], tuple[int, int, int]] = {}

        for document in documents:
            extraction = document.get("financial_statement_extraction")
            if not isinstance(extraction, dict):
                continue
            source_rank = self.SOURCE_RANK.get(
                document.get("financial_statement_extraction_source") or "", 1
            )
            current_year, previous_year = resolve_report_years(extraction)

            for statement_key in self.STATEMENT_KEYS:
                statement = extraction.get(statement_key)
                if not isinstance(statement, dict):
                    continue
                for label, code, values, _ in iter_line_items(statement):
                    label = label or ""
                    if not label or not values:
                        continue

                    matched = self.match_metric(
                        label,
                        code,
                        statement_key,
                    )
                    if matched is None:
                        continue
                    metric, tier, score = matched

                    for year, raw_value in values.items():
                        try:
                            value = float(raw_value)
                        except (TypeError, ValueError):
                            continue
                        year_key = normalize_period_label(
                            year, current_year, previous_year
                        )
                        slot = (year_key, metric.key)
                        rank = (source_rank, tier, score)
                        if rank <= best_score.get(slot, (-1, -1, -1)):
                            continue
                        yearly_metrics.setdefault(year_key, {})
                        yearly_metrics[year_key][metric.key] = value
                        best_score[slot] = rank

        return dict(sorted(yearly_metrics.items()))

    @classmethod
    def match_metric(
        cls,
        label: str,
        code: Any,
        statement_key: str,
    ) -> tuple[MetricDefinition, int, int] | None:
        """Map one statement line to a metric, with a tier and a score."""

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
            metric, score = max(candidates, key=lambda item: item[1])
            return metric, cls.LABEL_TIER, score

        # No label matched — OCR may have mangled it beyond recognition. Fall
        # back to the code alone, which is the only remaining signal.
        for metric in cls.METRICS:
            if statement_key not in metric.statements:
                continue
            if _code_rank(code_text, metric.codes) is not None:
                return metric, cls.CODE_ONLY_TIER, cls.CODE_ONLY_SCORE
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
            METRICS_BLOCK_HEADING,
            "Computed by the program from the extracted documents — use these "
            "as the primary source for every ratio table. Money is in đồng; "
            "copy the figure as printed and the program converts it. Where a "
            "value is missing, say so rather than estimating it.",
            "",
        ]
        # Above the numbers, not below them: a reader who has already worked
        # through the table has drawn the conclusion the warning exists to stop.
        warnings = (
            self.detect_unit_anomalies(yearly_metrics)
            + self.data_quality_warnings(yearly_metrics)
        )
        if warnings:
            lines.append(
                "Data quality warnings (possible OCR/extraction errors — verify "
                "against source before relying on these figures):"
            )
            lines.extend(f"- {warning}" for warning in warnings)
            lines.append("")
        if source_files:
            lines.extend(
                [
                    "SOURCE FILES — cite by the filenames below. "
                    "\"[PRE-COMPUTED FINANCIAL METRICS]\" is this block's "
                    "internal label; never write it into the report:",
                ]
            )
            lines.extend(
                f"- {year}: {', '.join(names)}"
                for year, names in source_files.items()
            )
            lines.append("")
        # Công thức KHÔNG in ra ở đây. Bảng "Computed financial ratios" bên
        # dưới đã có cột "Công thức", và đối chiếu cho thấy cả 21 công thức của
        # danh sách này lặp nguyên văn xuống đó — 504 token nói lại điều bảng
        # đang nói.
        lines.extend(
            [
                "",
                "Extracted financial statement line items:",
                self._markdown_table(
                    "Chỉ tiêu", years, self._metric_rows(years, yearly_metrics)
                ),
            ]
        )
        lines.extend(
            [
                "",
                "Computed financial ratios:",
                self._markdown_table(
                    "Chỉ số|Công thức", years,
                    self._ratio_rows(years, yearly_ratios),
                ),
            ]
        )

        lines.append(f"[/{METRICS_BLOCK_HEADING.strip('[]')}]")
        return "\n".join(lines)

    @staticmethod
    def _markdown_table(
        first_column: str,
        years: list[str],
        rows: list[tuple[str, ...]],
    ) -> str:
        """One table builder for both the line items and the ratios."""

        leading = first_column.split("|")
        head = "| " + " | ".join(leading) + " | " + " | ".join(years) + " |"
        rule = "|" + "---|" * len(leading) + "|".join("---:" for _ in years) + "|"
        out = [head, rule]
        for row in rows:
            out.append("| " + " | ".join(row) + " |")
        return "\n".join(out)

    def _metric_rows(
        self,
        years: list[str],
        yearly_metrics: dict[str, dict[str, float]],
    ) -> list[tuple[str, ...]]:
        rows = []
        for definition in self.METRICS:
            cells = [
                _format_number(yearly_metrics.get(year, {}).get(definition.key), "value")
                for year in years
            ]
            # A row nobody could read a figure from is noise in the prompt.
            if any(cell != "N/A" for cell in cells):
                rows.append((definition.label, *cells))
        return rows

    def _ratio_rows(
        self,
        years: list[str],
        yearly_ratios: dict[str, dict[str, float]],
    ) -> list[tuple[str, ...]]:
        rows = []
        for definition in self.RATIO_DEFINITIONS:
            cells = [
                _format_number(yearly_ratios.get(year, {}).get(definition.key), definition.unit)
                for year in years
            ]
            if any(cell != "N/A" for cell in cells):
                rows.append((definition.label, definition.formula, *cells))
        return rows

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


def _days(metrics: dict[str, float], balance: str, flow: str) -> float | None:
    """Turnover days: a balance-sheet figure over a flow, annualised.

    Five ratios are this shape and three of them appear again inside the
    composites below, so the expression was written out eight times.
    """

    return _safe_div(metrics.get(balance), metrics.get(flow), DAYS_PER_YEAR)


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
    days = [
        _days(metrics, "accounts_receivable", "net_revenue"),
        _days(metrics, "inventory", "cogs"),
        _days(metrics, "prepaid_suppliers", "net_revenue"),
        _days(metrics, "accounts_payable", "cogs"),
        _days(metrics, "customer_advances", "cogs"),
    ]
    if all(value is None for value in days):
        return None
    dso, dio, prepaid, dpo, advance = (value or 0 for value in days)
    return dso + dio + prepaid - dpo - advance


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
    # Money stays in đồng, formatted the one way every block formats it. The
    # division that used to be here made this the second of three places that
    # knew about tỷ VNĐ; now only _finalize does.
    return render_money(value)
