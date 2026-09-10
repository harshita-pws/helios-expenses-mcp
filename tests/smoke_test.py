"""Exercises every tool in-process, without an MCP client. Run: uv run smoke_test.py"""

import asyncio
import json

from helios_expenses import server as srv
from helios_expenses.filters import ExpenseFilter


def show(label, value, depth=900):
    text = json.dumps(value, indent=2, default=str) if not isinstance(value, str) else value
    print(f"\n===== {label} =====")
    print(text[:depth] + (f"\n... [{len(text)} chars]" if len(text) > depth else ""))


async def main():
    show("MCP tools registered", [t.name for t in await srv.mcp.list_tools()])
    show("MCP resources", [str(r.uri) for r in await srv.mcp.list_resources()])
    show("MCP prompts", [p.name for p in await srv.mcp.list_prompts()])

    show("describe_schema", srv.describe_schema(), 3000)
    show("overview", srv.overview(), 2200)

    show(
        "aggregate: spend by department",
        srv.aggregate_expenses(group_by=["department"], limit=10),
    )
    show(
        "aggregate: Q1 meals by employee, top 5",
        srv.aggregate_expenses(
            group_by=["employee_name"],
            metrics=["total_usd", "line_items", "avg_usd", "missing_receipts"],
            filter=ExpenseFilter(category="Meals", month_from="2025-01", month_to="2025-03"),
            limit=5,
        ),
    )
    show(
        "aggregate: grand total, rejected claims",
        srv.aggregate_expenses(
            metrics=["total_usd", "line_items", "trips"],
            filter=ExpenseFilter(approval_status="Rejected"),
        ),
    )

    show(
        "search: big airfare in Tokyo",
        srv.search_expenses(
            filter=ExpenseFilter(city="Tokyo", category="Airfare", min_amount_usd=1500),
            limit=3,
            sort_by="amount_usd",
            descending=True,
        ),
        1600,
    )
    show(
        "search: free-text 'Marriott', reimbursable only",
        srv.search_expenses(filter=ExpenseFilter(text="Marriott", reimbursable_only=True), limit=2),
        1200,
    )

    show(
        "policy_audit: worst breaches",
        srv.policy_audit(reason="over_limit", limit=3),
        1600,
    )
    show(
        "policy_audit: by manager",
        srv.policy_audit(reason="any", group_by=["manager_approver"], limit=5),
    )
    show(
        "policy_audit: approved yet no receipt",
        srv.policy_audit(
            reason="missing_receipt", filter=ExpenseFilter(approval_status="Approved"), limit=2
        ),
        1200,
    )

    show("get_trip TRP-2025-061", srv.get_trip("TRP-2025-061", include_line_items=False), 1800)
    show("get_employee 'Rohit'", srv.get_employee("Rohit"), 2000)
    show("get_employee ambiguous 'a'", srv.get_employee("Diego Ramirez"), 900)

    show("read_sheet category_month", srv.read_sheet("category_month", limit=2), 1500)
    show("read_sheet trips ordered", srv.read_sheet("trips", limit=2, order_by="Total Spend (USD)", descending=True), 1200)

    show("list_values department", srv.list_values("department"))
    show("list_values Currency (header form)", srv.list_values("Currency", with_totals=False))

    show(
        "query_sql: 90th percentile claim per department",
        srv.query_sql(
            """
            WITH ranked AS (
              SELECT department, amount_usd,
                     PERCENT_RANK() OVER (PARTITION BY department ORDER BY amount_usd) AS pr
              FROM expenses
            )
            SELECT department, MIN(amount_usd) AS p90_claim_usd
            FROM ranked WHERE pr >= 0.9 GROUP BY department ORDER BY p90_claim_usd DESC
            """
        ),
    )
    show(
        "query_sql: join trips to expenses",
        srv.query_sql(
            "SELECT t.trip_id, t.employee_name, t.total_spend_usd, SUM(e.amount_usd) AS recomputed "
            "FROM trips t JOIN expenses e ON e.trip_id = t.trip_id "
            "GROUP BY t.trip_id ORDER BY t.total_spend_usd DESC LIMIT 3"
        ),
    )

    for bad in ["DROP TABLE expenses", "SELECT 1; SELECT 2", "PRAGMA table_info(expenses)", "UPDATE expenses SET amount_usd=0"]:
        try:
            srv.query_sql(bad)
            print(f"\n!! FAILED TO BLOCK: {bad}")
        except Exception as e:
            print(f"  blocked {bad!r}: {e}")

    for bad_call, label in [
        (lambda: srv.list_values("nope"), "unknown column"),
        (lambda: srv.aggregate_expenses(metrics=["bogus"]), "unknown metric"),
        (lambda: srv.get_trip("TRP-9999-999"), "missing trip"),
    ]:
        try:
            bad_call()
            print(f"\n!! FAILED TO RAISE: {label}")
        except Exception as e:
            print(f"  rejected {label}: {str(e)[:110]}")

    show("reload_workbook", srv.reload_workbook())
    print("\nAll checks executed.")


asyncio.run(main())
