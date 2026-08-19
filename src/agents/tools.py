"""Database query tools for specialist agents (extracted from the notebook)."""

from __future__ import annotations

import json
from typing import Any

from collections.abc import Callable

from langchain_core.tools import tool


DatabaseExecutor = Callable[[str, dict[str, Any]], Any]
DATABASE_EXECUTOR: DatabaseExecutor | None = None
DEFAULT_QUERY_LIMIT = 20
MAX_QUERY_LIMIT = 100


# Point these table/column names at your own database schema.
TABLE_CONFIGS = {
    "customer_profile": {
        "table": "customer_master",
        "columns": [
            "customer_id",
            "customer_name",
            "tax_code",
            "registration_number",
            "legal_representative",
            "industry_code",
            "industry_name",
            "address",
            "established_date",
            "legal_status",
            "updated_at",
        ],
        "filter_columns": [
            "customer_id",
            "tax_code",
            "customer_name",
            "registration_number",
        ],
        "order_by": "updated_at DESC",
    },
    "financial_statement": {
        "table": "financial_statement",
        "columns": [
            "customer_id",
            "tax_code",
            "report_year",
            "period",
            "revenue",
            "gross_profit",
            "net_profit",
            "total_assets",
            "total_liabilities",
            "equity",
            "short_term_debt",
            "long_term_debt",
            "cash_and_equivalents",
            "inventory",
            "accounts_receivable",
            "accounts_payable",
            "updated_at",
        ],
        "filter_columns": ["customer_id", "tax_code", "report_year", "period"],
        "order_by": "report_year DESC, period DESC",
    },
    "business_activity": {
        "table": "business_activity",
        "columns": [
            "customer_id",
            "tax_code",
            "report_year",
            "main_products",
            "main_customers",
            "main_suppliers",
            "sales_channels",
            "production_capacity",
            "industry_outlook",
            "updated_at",
        ],
        "filter_columns": ["customer_id", "tax_code", "report_year"],
        "order_by": "report_year DESC, updated_at DESC",
    },
    "t24_credit_relationship": {
        "table": "t24_credit_relationship",
        "columns": [
            "customer_id",
            "tax_code",
            "facility_id",
            "facility_type",
            "approved_limit",
            "outstanding_balance",
            "available_limit",
            "currency",
            "interest_rate",
            "overdue_days",
            "repayment_status",
            "start_date",
            "maturity_date",
            "updated_at",
        ],
        "filter_columns": ["customer_id", "tax_code", "facility_id"],
        "order_by": "updated_at DESC",
    },
    "cic_bureau": {
        "table": "cic_bureau",
        "columns": [
            "customer_id",
            "tax_code",
            "bureau_report_date",
            "credit_institution",
            "facility_type",
            "approved_limit",
            "outstanding_balance",
            "overdue_balance",
            "overdue_days",
            "debt_group",
            "collateral_status",
            "note",
            "updated_at",
        ],
        "filter_columns": ["customer_id", "tax_code", "bureau_report_date"],
        "order_by": "bureau_report_date DESC, updated_at DESC",
    },
    "credit_history": {
        "table": "credit_history",
        "columns": [
            "customer_id",
            "tax_code",
            "facility_id",
            "facility_type",
            "approved_limit",
            "outstanding_balance",
            "overdue_days",
            "repayment_status",
            "start_date",
            "maturity_date",
            "updated_at",
        ],
        "filter_columns": ["customer_id", "tax_code", "facility_id"],
        "order_by": "updated_at DESC",
    },
    "collateral": {
        "table": "collateral",
        "columns": [
            "customer_id",
            "tax_code",
            "collateral_id",
            "collateral_type",
            "description",
            "appraised_value",
            "ltv",
            "ownership_status",
            "valuation_date",
            "updated_at",
        ],
        "filter_columns": ["customer_id", "tax_code", "collateral_id"],
        "order_by": "updated_at DESC",
    },
}


def configure_database_executor(executor: DatabaseExecutor | None) -> None:
    """Inject database query function có sẵn vào notebook tools."""

    global DATABASE_EXECUTOR
    DATABASE_EXECUTOR = executor


def get_database_tools() -> list[Any]:
    """Return all database tools that can be attached to agents."""

    return [
        query_customer_profile,
        query_financial_statement_data,
        query_business_activity_data,
        query_t24_credit_relationship_data,
        query_cic_bureau_data,
        query_credit_history_data,
        query_collateral_data,
        query_configured_table,
    ]


def build_select_query(
    logical_table: str,
    filters: dict[str, Any],
    limit: int = DEFAULT_QUERY_LIMIT,
) -> tuple[str, dict[str, Any]]:
    """Build a parameterized SELECT from allowlisted table configs."""

    table_config = TABLE_CONFIGS.get(logical_table)
    if not table_config:
        allowed = ", ".join(sorted(TABLE_CONFIGS))
        raise ValueError(f"Unsupported logical_table. Allowed values: {allowed}")

    cleaned_filters = clean_query_filters(
        filters,
        table_config["filter_columns"],
    )
    if not cleaned_filters:
        raise ValueError("At least one non-empty filter is required.")

    where_clauses = []
    params = {}
    for column, value in cleaned_filters.items():
        param_name = f"p_{column}"
        if column.endswith("name"):
            where_clauses.append(f"LOWER({column}) LIKE LOWER(:{param_name})")
            params[param_name] = f"%{value}%"
        else:
            where_clauses.append(f"{column} = :{param_name}")
            params[param_name] = value

    params["limit"] = min(max(int(limit or DEFAULT_QUERY_LIMIT), 1), MAX_QUERY_LIMIT)
    columns = ", ".join(table_config["columns"])
    sql = (
        f"SELECT {columns} "
        f"FROM {table_config['table']} "
        f"WHERE {' AND '.join(where_clauses)} "
        f"ORDER BY {table_config['order_by']} "
        "LIMIT :limit"
    )
    return sql, params


def clean_query_filters(
    filters: dict[str, Any],
    allowed_columns: list[str],
) -> dict[str, Any]:
    """Keep only allowlisted, non-empty filters."""

    cleaned = {}
    for key, value in (filters or {}).items():
        if key not in allowed_columns or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value.strip() if isinstance(value, str) else value
    return cleaned


def execute_database_query(sql: str, params: dict[str, Any]) -> Any:
    """Run SQL through the injected database executor."""

    if DATABASE_EXECUTOR is None:
        raise RuntimeError(
            "Database executor is not configured. Call "
            "`configure_database_executor(executor)` first."
        )
    return DATABASE_EXECUTOR(sql, params)


def normalize_database_rows(rows: Any) -> list[dict[str, Any]]:
    """Normalize common database result shapes into dictionaries."""

    if rows is None:
        return []
    if isinstance(rows, str):
        return [{"text": rows}]
    if isinstance(rows, dict):
        if isinstance(rows.get("rows"), list):
            return normalize_database_rows(rows["rows"])
        return [rows]

    normalized_rows = []
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append(row)
        elif hasattr(row, "_asdict"):
            normalized_rows.append(row._asdict())
        elif hasattr(row, "_mapping"):
            normalized_rows.append(dict(row._mapping))
        else:
            normalized_rows.append({"value": row})
    return normalized_rows


def format_database_tool_response(
    tool_name: str,
    sql: str,
    params: dict[str, Any],
    rows: Any,
) -> str:
    """Return stable JSON for downstream agents."""

    normalized_rows = normalize_database_rows(rows)
    payload = {
        "status": "success",
        "tool": tool_name,
        "row_count": len(normalized_rows),
        "rows": normalized_rows,
        "query": {"sql": sql, "params": params},
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def format_database_tool_error(tool_name: str, error: Exception) -> str:
    """Return stable JSON error payload for downstream agents."""

    payload = {
        "status": "error",
        "tool": tool_name,
        "error_type": type(error).__name__,
        "error": str(error),
        "rows": [],
    }
    return json.dumps(payload, ensure_ascii=False)


def run_configured_table_query(
    logical_table: str,
    filters: dict[str, Any] | None = None,
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Run a safe read query against one configured logical table."""

    tool_name = f"query_{logical_table}"
    try:
        sql, params = build_select_query(logical_table, filters or {}, limit)
        rows = execute_database_query(sql, params)
        return format_database_tool_response(tool_name, sql, params, rows)
    except Exception as exc:
        return format_database_tool_error(tool_name, exc)


@tool
def query_customer_profile(
    tax_code: str = "",
    customer_id: str = "",
    customer_name: str = "",
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query customer master/profile data by tax code, ID, or name."""

    return run_configured_table_query(
        "customer_profile",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "customer_name": customer_name,
        },
        limit,
    )


@tool
def query_financial_statement_data(
    tax_code: str = "",
    customer_id: str = "",
    report_year: int | None = None,
    period: str = "",
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query structured financial statement metrics for a customer."""

    return run_configured_table_query(
        "financial_statement",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "report_year": report_year,
            "period": period,
        },
        limit,
    )


@tool
def query_business_activity_data(
    tax_code: str = "",
    customer_id: str = "",
    report_year: int | None = None,
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query structured business activity and operating profile data."""

    return run_configured_table_query(
        "business_activity",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "report_year": report_year,
        },
        limit,
    )


@tool
def query_t24_credit_relationship_data(
    tax_code: str = "",
    customer_id: str = "",
    facility_id: str = "",
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query internal T24 credit relationship data for a customer."""

    return run_configured_table_query(
        "t24_credit_relationship",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "facility_id": facility_id,
        },
        limit,
    )


@tool
def query_cic_bureau_data(
    tax_code: str = "",
    customer_id: str = "",
    bureau_report_date: str = "",
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query CIC/bureau credit data for a customer."""

    return run_configured_table_query(
        "cic_bureau",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "bureau_report_date": bureau_report_date,
        },
        limit,
    )


@tool
def query_credit_history_data(
    tax_code: str = "",
    customer_id: str = "",
    facility_id: str = "",
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query loan/facility and repayment history for a customer."""

    return run_configured_table_query(
        "credit_history",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "facility_id": facility_id,
        },
        limit,
    )


@tool
def query_collateral_data(
    tax_code: str = "",
    customer_id: str = "",
    collateral_id: str = "",
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query collateral/security asset records for a customer."""

    return run_configured_table_query(
        "collateral",
        {
            "tax_code": tax_code,
            "customer_id": customer_id,
            "collateral_id": collateral_id,
        },
        limit,
    )


@tool
def query_configured_table(
    logical_table: str,
    filters_json: str,
    limit: int = DEFAULT_QUERY_LIMIT,
) -> str:
    """Query any allowlisted logical table using JSON filters."""

    try:
        filters = json.loads(filters_json or "{}")
        if not isinstance(filters, dict):
            raise ValueError("filters_json must decode to a JSON object.")
    except Exception as exc:
        return format_database_tool_error("query_configured_table", exc)

    return run_configured_table_query(logical_table, filters, limit)


# Backward-compatible tool names from the original project prompts.
check_for_customer = query_customer_profile
get_VIRAC_data_for_customer = query_financial_statement_data

DATABASE_TOOLS = get_database_tools()
FINANCIAL_DATABASE_TOOLS = [
    query_customer_profile,
    query_financial_statement_data,
]
BUSINESS_ACTIVITY_DATABASE_TOOLS = [
    query_customer_profile,
    query_business_activity_data,
]
CREDIT_RELATIONSHIP_DATABASE_TOOLS = [
    query_customer_profile,
    query_t24_credit_relationship_data,
    query_cic_bureau_data,
]
RISK_ASSESSMENT_DATABASE_TOOLS = [
    query_customer_profile,
    query_financial_statement_data,
    query_business_activity_data,
    query_t24_credit_relationship_data,
    query_cic_bureau_data,
    query_credit_history_data,
    query_collateral_data,
]


# Example: uncomment and adapt this to your existing database object.
# def database_executor(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
#     return database.query(sql, params)
#
# configure_database_executor(database_executor)

print("Database tools embedded:", [tool.name for tool in DATABASE_TOOLS])
