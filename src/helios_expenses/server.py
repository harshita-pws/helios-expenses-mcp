"""MCP server exposing the Helios Instruments 2025 travel expense workbook."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .filters import ExpenseFilter
from .store import ExpenseStore, is_read_only_select

WORKBOOK_NAME = "Helios_Business_Travel_Expenses_2025.xlsx"
# data/ is the copy committed to the repo, which is what a deployed instance uses.
BUNDLED_XLSX = Path(__file__).resolve().parents[2] / "data" / WORKBOOK_NAME
LOCAL_XLSX = Path.home() / "Downloads" / WORKBOOK_NAME


def workbook_path() -> Path:
    """HELIOS_XLSX wins, then the copy committed to the repo, then ~/Downloads.

    The bundled copy is looked up both relative to this file (running from a
    source checkout) and relative to the working directory (installed as a
    wheel and launched from the repo root, which is how Render runs it).
    """
    override = os.environ.get("HELIOS_XLSX")
    if override:
        return Path(override).expanduser()
    for candidate in (BUNDLED_XLSX, Path.cwd() / "data" / WORKBOOK_NAME, LOCAL_XLSX):
        if candidate.exists():
            return candidate
    return BUNDLED_XLSX  # report the expected location in the error

# Every tool here only reads the workbook, which lets clients skip approval prompts.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    "helios-expenses",
    version="0.1.0",
    instructions=(
        "Query the Helios Instruments FY2025 business travel & expense register "
        "(2,954 expense claims across 138 trips and 24 employees, all amounts "
        "convertible to USD). Start with `overview` or `describe_schema`. Use "
        "`aggregate_expenses` for any 'total / average / top N by X' question, "
        "`search_expenses` to list individual claims, `policy_audit` for "
        "over-limit or missing-receipt work, and `query_sql` for anything the "
        "curated tools cannot express. The data is read-only."
    ),
)

_store: ExpenseStore | None = None


def store() -> ExpenseStore:
    global _store
    if _store is None:
        _store = ExpenseStore(workbook_path())
    return _store


# --------------------------------------------------------------------- output

_RATE_HINTS = ("rate", "pct")


def _round(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, float):
            out[k] = round(v, 6 if any(h in k for h in _RATE_HINTS) else 2)
        else:
            out[k] = v
    return out


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_round(r) for r in rows]


def _resolve(table: str, name: str) -> str:
    """Resolve a column name, reporting the failure as a ToolError so the caller
    sees the list of valid columns instead of an opaque server error."""
    try:
        return store().resolve_column(table, name)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


DEFAULT_EXPENSE_COLUMNS = [
    "expense_id",
    "expense_date",
    "employee_name",
    "department",
    "trip_id",
    "destination_city",
    "expense_category",
    "merchant_vendor",
    "currency",
    "amount_local",
    "amount_usd",
    "over_policy",
    "receipt_attached",
    "payment_method",
    "approval_status",
]

# Metric name -> SQL aggregate over the expenses table.
METRICS: dict[str, str] = {
    "total_usd": "SUM(amount_usd)",
    "line_items": "COUNT(*)",
    "avg_usd": "AVG(amount_usd)",
    "max_usd": "MAX(amount_usd)",
    "min_usd": "MIN(amount_usd)",
    "total_local": "SUM(amount_local)",
    "reimbursable_usd": "SUM(reimbursable_usd)",
    "vat_usd": "SUM(vat_tax_usd)",
    "net_of_tax_usd": "SUM(net_of_tax_usd)",
    "billable_usd": "SUM(CASE WHEN LOWER(billable_to_client)='yes' THEN amount_usd ELSE 0 END)",
    "over_policy_items": "SUM(CASE WHEN LOWER(over_policy)='yes' THEN 1 ELSE 0 END)",
    "over_policy_usd": "SUM(CASE WHEN LOWER(over_policy)='yes' THEN variance_vs_limit_usd ELSE 0 END)",
    "missing_receipts": "SUM(CASE WHEN LOWER(receipt_attached)='no' THEN 1 ELSE 0 END)",
    "rejected_items": "SUM(CASE WHEN LOWER(approval_status)='rejected' THEN 1 ELSE 0 END)",
    "pending_items": "SUM(CASE WHEN LOWER(approval_status)='pending approval' THEN 1 ELSE 0 END)",
    "trips": "COUNT(DISTINCT trip_id)",
    "employees": "COUNT(DISTINCT employee_id)",
    "avg_days_to_reimburse": "AVG(days_to_reimburse)",
}

DEFAULT_METRICS = ["total_usd", "line_items", "avg_usd", "over_policy_items"]


def _dimension_columns() -> dict[str, str]:
    """Groupable (non-numeric) columns of the expenses table."""
    return {
        name: col.header
        for name, col in store().columns("expenses").items()
        if col.sql_type in ("TEXT", "DATE")
    }


# ---------------------------------------------------------------------- tools


@mcp.tool(annotations=READ_ONLY)
def overview() -> dict[str, Any]:
    """Headline figures for the whole register: coverage, spend, compliance and
    the biggest slices by department, category and destination. Call this first
    when you do not yet know what is in the data."""
    s = store()
    totals = s.query(
        """
        SELECT COUNT(*) AS line_items,
               COUNT(DISTINCT trip_id) AS trips,
               COUNT(DISTINCT employee_id) AS employees,
               MIN(expense_date) AS first_expense,
               MAX(expense_date) AS last_expense,
               SUM(amount_usd) AS total_usd,
               SUM(reimbursable_usd) AS reimbursable_usd,
               SUM(vat_tax_usd) AS vat_usd,
               SUM(CASE WHEN LOWER(billable_to_client)='yes' THEN amount_usd ELSE 0 END) AS billable_usd,
               SUM(CASE WHEN LOWER(over_policy)='yes' THEN 1 ELSE 0 END) AS over_policy_items,
               SUM(CASE WHEN LOWER(over_policy)='yes' THEN variance_vs_limit_usd ELSE 0 END) AS over_policy_usd,
               SUM(CASE WHEN LOWER(receipt_attached)='no' THEN 1 ELSE 0 END) AS missing_receipts,
               AVG(days_to_reimburse) AS avg_days_to_reimburse
        FROM expenses
        """
    )[0]

    def top(col: str, n: int = 5) -> list[dict[str, Any]]:
        return _rows(
            s.query(
                f'SELECT "{col}" AS name, SUM(amount_usd) AS total_usd, COUNT(*) AS line_items '
                f'FROM expenses GROUP BY "{col}" ORDER BY total_usd DESC LIMIT ?',
                (n,),
            )
        )

    return {
        "workbook": s.path.name,
        "totals": _round(totals),
        "approval_mix": _rows(
            s.query(
                "SELECT approval_status, COUNT(*) AS line_items, SUM(amount_usd) AS total_usd "
                "FROM expenses GROUP BY approval_status ORDER BY total_usd DESC"
            )
        ),
        "top_departments": top("department"),
        "top_categories": top("expense_category", 6),
        "top_destinations": top("destination_city"),
        "monthly_totals": _rows(
            s.query(
                "SELECT month, SUM(amount_usd) AS total_usd, COUNT(*) AS line_items "
                "FROM expenses GROUP BY month ORDER BY month"
            )
        ),
        "tables": {t.name: t.rows for t in s.tables.values()},
    }


@mcp.tool(annotations=READ_ONLY)
def describe_schema() -> str:
    """Full schema of every table built from the workbook: column names, types,
    the original spreadsheet header and the allowed values of each low-cardinality
    column. Read this before writing `query_sql`."""
    return store().schema_text()


@mcp.tool(annotations=READ_ONLY)
def list_values(
    dimension: Annotated[str, Field(description="Column of the expenses table, e.g. 'employee_name'")],
    with_totals: Annotated[bool, Field(description="Include spend and line-item counts")] = True,
) -> list[dict[str, Any]] | list[str]:
    """List the distinct values of one column, so you can filter on exact
    spellings (employees, categories, cities, client codes, ...)."""
    s = store()
    col = _resolve("expenses", dimension)
    if not with_totals:
        return [
            r["value"]
            for r in s.query(
                f'SELECT DISTINCT "{col}" AS value FROM expenses '
                f'WHERE "{col}" IS NOT NULL ORDER BY 1'
            )
        ]
    return _rows(
        s.query(
            f'SELECT "{col}" AS value, COUNT(*) AS line_items, SUM(amount_usd) AS total_usd '
            f'FROM expenses WHERE "{col}" IS NOT NULL GROUP BY 1 ORDER BY total_usd DESC'
        )
    )


@mcp.tool(annotations=READ_ONLY)
def search_expenses(
    filter: ExpenseFilter | None = None,
    limit: Annotated[int, Field(ge=1, le=500, description="Max rows returned")] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
    sort_by: Annotated[str, Field(description="Any expenses column")] = "expense_date",
    descending: bool = False,
    columns: Annotated[
        list[str] | None,
        Field(description="Columns to return; pass ['*'] for all 37. Defaults to a compact set."),
    ] = None,
) -> dict[str, Any]:
    """List individual expense claims matching a filter. Returns the matching
    row count and totals alongside the page of rows, so you can tell whether the
    page is the whole answer."""
    s = store()
    f = filter or ExpenseFilter()
    where, params = f.where()

    if columns and columns != ["*"]:
        cols = [_resolve("expenses", c) for c in columns]
    elif columns == ["*"]:
        cols = list(s.columns("expenses"))
    else:
        cols = DEFAULT_EXPENSE_COLUMNS
    select = ", ".join(f'"{c}"' for c in cols)
    order = _resolve("expenses", sort_by)

    summary = s.query(
        f"SELECT COUNT(*) AS matching_rows, SUM(amount_usd) AS total_usd, "
        f"SUM(reimbursable_usd) AS reimbursable_usd FROM expenses WHERE {where}",
        params,
    )[0]
    rows = s.query(
        f'SELECT {select} FROM expenses WHERE {where} '
        f'ORDER BY "{order}" {"DESC" if descending else "ASC"}, expense_id ASC '
        f"LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return {
        "filter": f.describe(),
        **_round(summary),
        "returned": len(rows),
        "offset": offset,
        "truncated": offset + len(rows) < (summary["matching_rows"] or 0),
        "rows": _rows(rows),
    }


@mcp.tool(annotations=READ_ONLY)
def aggregate_expenses(
    group_by: Annotated[
        list[str] | None,
        Field(description="Columns to group by, e.g. ['department', 'expense_category']. Empty = grand total."),
    ] = None,
    metrics: Annotated[
        list[str] | None,
        Field(description=f"Any of: {', '.join(METRICS)}. Defaults to {DEFAULT_METRICS}."),
    ] = None,
    filter: ExpenseFilter | None = None,
    sort_by: Annotated[str | None, Field(description="A metric or group_by column")] = None,
    descending: bool = True,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """Group-and-total the register: spend by department, category by month, top
    employees by policy breaches, average claim by city, and so on. This answers
    most quantitative questions — prefer it over pulling rows and adding them up."""
    s = store()
    f = filter or ExpenseFilter()
    where, params = f.where()

    dims = [_resolve("expenses", d) for d in (group_by or [])]
    chosen = metrics or DEFAULT_METRICS
    unknown = [m for m in chosen if m not in METRICS]
    if unknown:
        raise ToolError(f"Unknown metric(s) {unknown}. Available: {', '.join(METRICS)}")

    select_parts = [f'"{d}" AS "{d}"' for d in dims]
    select_parts += [f'{METRICS[m]} AS "{m}"' for m in chosen]
    sql = f"SELECT {', '.join(select_parts)} FROM expenses WHERE {where}"
    if dims:
        sql += " GROUP BY " + ", ".join(f'"{d}"' for d in dims)

    order = sort_by or (chosen[0] if chosen else dims[0])
    if order not in chosen and order not in dims:
        order = _resolve("expenses", order)
        if order not in dims:
            raise ToolError(f"Cannot sort by {sort_by!r}: not a selected metric or group_by column")
    sql += f' ORDER BY "{order}" {"DESC" if descending else "ASC"} LIMIT ?'

    rows = s.query(sql, [*params, limit])
    return {
        "filter": f.describe(),
        "group_by": dims,
        "metrics": chosen,
        "groups": len(rows),
        "rows": _rows(rows),
    }


@mcp.tool(annotations=READ_ONLY)
def policy_audit(
    reason: Annotated[
        Literal["any", "over_limit", "missing_receipt", "both"],
        Field(description="'both' = claims that are over limit AND lack a receipt"),
    ] = "any",
    filter: ExpenseFilter | None = None,
    group_by: Annotated[
        list[str] | None,
        Field(description="Summarise breaches by these columns instead of listing rows"),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """The audit view: claims that exceed their category cap or have no receipt
    attached, with the variance against the limit. Pass `group_by` for a summary
    (e.g. by employee or manager) instead of individual rows."""
    s = store()
    f = filter or ExpenseFilter()
    where, params = f.where()

    over = "LOWER(over_policy)='yes'"
    missing = "LOWER(receipt_attached)='no'"
    reason_sql = {
        "any": f"({over} OR {missing})",
        "over_limit": over,
        "missing_receipt": missing,
        "both": f"({over} AND {missing})",
    }[reason]
    where = f"{where} AND {reason_sql}"

    summary = s.query(
        f"SELECT COUNT(*) AS flagged_items, SUM(amount_usd) AS flagged_usd, "
        f"SUM(CASE WHEN {over} THEN 1 ELSE 0 END) AS over_limit_items, "
        f"SUM(CASE WHEN {over} THEN variance_vs_limit_usd ELSE 0 END) AS excess_usd, "
        f"SUM(CASE WHEN {missing} THEN 1 ELSE 0 END) AS missing_receipt_items "
        f"FROM expenses WHERE {where}",
        params,
    )[0]

    if group_by:
        dims = [_resolve("expenses", d) for d in group_by]
        sel = ", ".join(f'"{d}"' for d in dims)
        rows = s.query(
            f"SELECT {sel}, COUNT(*) AS flagged_items, SUM(amount_usd) AS flagged_usd, "
            f"SUM(CASE WHEN {over} THEN 1 ELSE 0 END) AS over_limit_items, "
            f"SUM(CASE WHEN {over} THEN variance_vs_limit_usd ELSE 0 END) AS excess_usd, "
            f"SUM(CASE WHEN {missing} THEN 1 ELSE 0 END) AS missing_receipt_items "
            f"FROM expenses WHERE {where} GROUP BY {sel} ORDER BY excess_usd DESC LIMIT ?",
            [*params, limit],
        )
    else:
        rows = s.query(
            "SELECT expense_id, expense_date, employee_name, department, manager_approver, "
            "trip_id, expense_category, merchant_vendor, amount_usd, policy_limit_usd, "
            "variance_vs_limit_usd, over_policy, receipt_attached, approval_status "
            f"FROM expenses WHERE {where} "
            "ORDER BY variance_vs_limit_usd DESC, amount_usd DESC LIMIT ?",
            [*params, limit],
        )

    return {
        "reason": reason,
        "filter": f.describe(),
        **_round(summary),
        "returned": len(rows),
        "rows": _rows(rows),
    }


@mcp.tool(annotations=READ_ONLY)
def get_trip(
    trip_id: Annotated[str, Field(description="e.g. 'TRP-2025-061' (case-insensitive)")],
    include_line_items: bool = True,
) -> dict[str, Any]:
    """Everything about one trip: the Trip Summary row, a category breakdown and
    (optionally) every line item on it."""
    s = store()
    tid = trip_id.strip()
    header = s.query(
        "SELECT * FROM trips WHERE LOWER(trip_id) = LOWER(?)", (tid,)
    )
    totals = s.query(
        """
        SELECT trip_id, employee_name, department, destination_city, destination_country,
               trip_purpose, MIN(trip_start) AS trip_start, MAX(trip_end) AS trip_end,
               COUNT(*) AS line_items, SUM(amount_usd) AS total_usd,
               SUM(reimbursable_usd) AS reimbursable_usd,
               SUM(CASE WHEN LOWER(over_policy)='yes' THEN 1 ELSE 0 END) AS over_policy_items
        FROM expenses WHERE LOWER(trip_id) = LOWER(?) GROUP BY trip_id
        """,
        (tid,),
    )
    if not header and not totals:
        raise ToolError(f"No trip {trip_id!r}. Use list_values('trip_id') to see the ids.")

    result: dict[str, Any] = {
        "trip_id": tid,
        "summary_sheet_row": _round(header[0]) if header else None,
        "computed_totals": _round(totals[0]) if totals else None,
        "by_category": _rows(
            s.query(
                "SELECT expense_category, COUNT(*) AS line_items, SUM(amount_usd) AS total_usd "
                "FROM expenses WHERE LOWER(trip_id) = LOWER(?) "
                "GROUP BY expense_category ORDER BY total_usd DESC",
                (tid,),
            )
        ),
    }
    if include_line_items:
        select = ", ".join(f'"{c}"' for c in DEFAULT_EXPENSE_COLUMNS)
        result["line_items"] = _rows(
            s.query(
                f"SELECT {select} FROM expenses WHERE LOWER(trip_id) = LOWER(?) "
                "ORDER BY expense_date, expense_id",
                (tid,),
            )
        )
    return result


@mcp.tool(annotations=READ_ONLY)
def get_employee(
    employee: Annotated[str, Field(description="Name, partial name, or EMP- id")],
) -> dict[str, Any]:
    """One traveller's profile: the Employee Summary row, their trips, their
    spend by category and their policy breaches."""
    s = store()
    who = employee.strip()
    match = s.query(
        "SELECT DISTINCT employee_id, employee_name, department, job_title, manager_approver "
        "FROM expenses WHERE LOWER(employee_id) = LOWER(?) OR LOWER(employee_name) = LOWER(?) "
        "OR INSTR(LOWER(employee_name), LOWER(?)) > 0",
        (who, who, who),
    )
    if not match:
        raise ToolError(f"No employee matching {employee!r}. Try list_values('employee_name').")
    if len(match) > 1:
        return {
            "ambiguous": True,
            "message": f"{employee!r} matches {len(match)} employees; call again with one of these.",
            "candidates": match,
        }
    person = match[0]
    emp_id = person["employee_id"]
    sheet_row = s.query("SELECT * FROM employees WHERE LOWER(employee_id) = LOWER(?)", (emp_id,))

    return {
        "employee": person,
        "summary_sheet_row": _round(sheet_row[0]) if sheet_row else None,
        "computed_totals": _round(
            s.query(
                """
                SELECT COUNT(*) AS line_items, COUNT(DISTINCT trip_id) AS trips,
                       SUM(amount_usd) AS total_usd, AVG(amount_usd) AS avg_claim_usd,
                       SUM(reimbursable_usd) AS reimbursable_usd,
                       SUM(CASE WHEN LOWER(over_policy)='yes' THEN 1 ELSE 0 END) AS over_policy_items,
                       SUM(CASE WHEN LOWER(receipt_attached)='no' THEN 1 ELSE 0 END) AS missing_receipts
                FROM expenses WHERE employee_id = ?
                """,
                (emp_id,),
            )[0]
        ),
        "by_category": _rows(
            s.query(
                "SELECT expense_category, COUNT(*) AS line_items, SUM(amount_usd) AS total_usd "
                "FROM expenses WHERE employee_id = ? GROUP BY 1 ORDER BY total_usd DESC",
                (emp_id,),
            )
        ),
        "by_month": _rows(
            s.query(
                "SELECT month, SUM(amount_usd) AS total_usd FROM expenses "
                "WHERE employee_id = ? GROUP BY 1 ORDER BY 1",
                (emp_id,),
            )
        ),
        "trips": _rows(
            s.query(
                "SELECT trip_id, MIN(trip_start) AS trip_start, MAX(trip_end) AS trip_end, "
                "destination_city, trip_purpose, COUNT(*) AS line_items, "
                "SUM(amount_usd) AS total_usd FROM expenses WHERE employee_id = ? "
                "GROUP BY trip_id ORDER BY trip_start",
                (emp_id,),
            )
        ),
    }


@mcp.tool(annotations=READ_ONLY)
def read_sheet(
    table: Annotated[
        Literal["trips", "employees", "category_month", "policy_exceptions", "expenses"],
        Field(description="Which sheet-backed table to page through"),
    ],
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
    order_by: str | None = None,
    descending: bool = False,
) -> dict[str, Any]:
    """Read one of the workbook's own tabs verbatim — the pre-built Trip Summary,
    Employee Summary, Category by Month matrix or Policy Exceptions audit list.
    Use this when the user asks about "the summary tab" rather than the raw data."""
    s = store()
    if table not in s.tables:
        raise ToolError(f"No table {table!r}. Available: {', '.join(s.tables)}")
    total = s.scalar(f'SELECT COUNT(*) FROM "{table}"')
    sql = f'SELECT * FROM "{table}"'
    if order_by:
        col = _resolve(table, order_by)
        sql += f' ORDER BY "{col}" {"DESC" if descending else "ASC"}'
    rows = s.query(sql + " LIMIT ? OFFSET ?", (limit, offset))
    sheet_total = s.tables[table].total_row
    return {
        "table": table,
        "sheet": s.tables[table].sheet,
        "total_rows": total,
        "offset": offset,
        "returned": len(rows),
        "rows": _rows(rows),
        "sheet_total_row": _round(sheet_total) if sheet_total else None,
    }


@mcp.tool(annotations=READ_ONLY)
def query_sql(
    sql: Annotated[str, Field(description="A single read-only SELECT/WITH statement")],
    limit: Annotated[int, Field(ge=1, le=1000, description="Row cap applied to the result")] = 200,
) -> dict[str, Any]:
    """Run an arbitrary read-only SQL query (SQLite dialect) against the workbook
    tables for anything the other tools cannot express — window functions, joins
    between tabs, percentiles. Call `describe_schema` first for exact column
    names. Writes, multiple statements and PRAGMA are rejected."""
    ok, cleaned = is_read_only_select(sql)
    if not ok:
        raise ToolError(cleaned)
    try:
        rows = store().query(f"SELECT * FROM ({cleaned}) LIMIT ?", (limit,))
    except sqlite3.Error as exc:
        raise ToolError(
            f"SQL error: {exc}. Call describe_schema for the exact table and column names."
        ) from exc
    return {
        "sql": cleaned,
        "returned": len(rows),
        "truncated": len(rows) == limit,
        "rows": _rows(rows),
    }


@mcp.tool(annotations=READ_ONLY)
def reload_workbook() -> dict[str, Any]:
    """Re-read the .xlsx from disk. Use after the spreadsheet has been edited."""
    s = store()
    s.load()
    return {
        "reloaded": str(s.path),
        "loaded_at": s.loaded_at,
        "tables": {t.name: t.rows for t in s.tables.values()},
    }


# ------------------------------------------------------------------ resources


@mcp.resource("helios://schema", mime_type="text/plain")
def schema_resource() -> str:
    """Table and column schema derived from the workbook."""
    return store().schema_text()


@mcp.resource("helios://readme", mime_type="text/plain")
def readme_resource() -> str:
    """The workbook's own README tab: grain, derivations, assumptions."""
    return store().readme_text()


@mcp.resource("helios://policy-limits", mime_type="text/plain")
def policy_limits_resource() -> str:
    """Per-claim policy cap for each expense category, in USD."""
    rows = store().query(
        "SELECT expense_category, MIN(policy_limit_usd) AS base_limit_usd, "
        "MAX(policy_limit_usd) AS max_applied_usd, COUNT(*) AS line_items "
        "FROM expenses GROUP BY 1 ORDER BY base_limit_usd DESC"
    )
    lines = [
        "Per-claim policy limits (USD). Lodging and car rental caps are multiplied",
        "by the number of nights/days on the trip, so max_applied exceeds the base.",
        "",
        f"{'Category':<28}{'Base':>10}{'Max applied':>14}{'Claims':>9}",
    ]
    for r in rows:
        lines.append(
            f"{r['expense_category']:<28}{r['base_limit_usd']:>10.0f}"
            f"{r['max_applied_usd']:>14.0f}{r['line_items']:>9}"
        )
    return "\n".join(lines)


# -------------------------------------------------------------------- prompts


@mcp.prompt()
def audit_review(scope: str = "the whole year") -> str:
    """Walk through a policy-exception review for a period, employee or department."""
    return (
        f"Run a travel-expense compliance review for {scope} using the helios-expenses tools.\n"
        "1. Call `overview` to size the register and the compliance rate.\n"
        "2. Call `policy_audit` grouped by employee, then by category, to find where the "
        "breaches concentrate.\n"
        "3. List the ten largest individual breaches with `policy_audit` (no group_by).\n"
        "4. Separately report claims missing receipts that were nonetheless approved.\n"
        "Finish with a short table of findings and the total USD over limit."
    )


@mcp.prompt()
def spend_summary(dimension: str = "department") -> str:
    """Produce a spend summary broken down by a dimension, with month-on-month trend."""
    return (
        f"Summarise FY2025 Helios travel spend by {dimension}.\n"
        f"Use `aggregate_expenses` with group_by=['{dimension}'] and metrics "
        "['total_usd','line_items','avg_usd','over_policy_items'], then again with "
        f"group_by=['month','{dimension}'] for the trend. Call out the top three "
        "contributors, any month with an unusual spike, and how much of the spend is "
        "billable to clients."
    )


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Any) -> Any:
    """Liveness probe for hosted deployments: confirms the workbook is loaded."""
    from starlette.responses import JSONResponse

    s = store()
    return JSONResponse(
        {
            "status": "ok",
            "workbook": s.path.name,
            "loaded_at": s.loaded_at,
            "rows": {t.name: t.rows for t in s.tables.values()},
        }
    )


def main() -> None:
    xlsx = workbook_path()
    if not xlsx.exists():
        raise SystemExit(f"Workbook not found: {xlsx}\nSet HELIOS_XLSX to the .xlsx path.")
    store()  # fail fast on a bad workbook rather than on the first tool call
    mcp.run()


if __name__ == "__main__":
    main()
