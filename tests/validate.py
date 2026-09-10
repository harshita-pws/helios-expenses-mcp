"""Cross-checks tool output against the workbook's own derived tabs."""

from helios_expenses.server import store

s = store()


def close(a, b, tol=0.01):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


print("--- grand total: expenses vs Category by Month TOTAL row ---")
raw = s.scalar("SELECT SUM(amount_usd) FROM expenses")
sheet_total = s.tables["category_month"].total_row["total"]
print(f"  expenses SUM      = {raw:,.2f}")
print(f"  sheet TOTAL/Total = {sheet_total:,.2f}   match={close(raw, sheet_total, 0.5)}")

print("\n--- per-employee spend: expenses vs Employee Summary ---")
bad = s.query(
    """
    SELECT e.employee_id, es.total_spend_usd AS sheet, SUM(e.amount_usd) AS computed
    FROM expenses e JOIN employees es ON es.employee_id = e.employee_id
    GROUP BY e.employee_id
    HAVING ABS(es.total_spend_usd - SUM(e.amount_usd)) > 0.01
    """
)
print(f"  24 employees checked, {len(bad)} mismatches" + (f": {bad}" if bad else ""))

print("\n--- per-trip spend: expenses vs Trip Summary ---")
bad = s.query(
    """
    SELECT e.trip_id, t.total_spend_usd AS sheet, SUM(e.amount_usd) AS computed
    FROM expenses e JOIN trips t ON t.trip_id = e.trip_id
    GROUP BY e.trip_id
    HAVING ABS(t.total_spend_usd - SUM(e.amount_usd)) > 0.01
    """
)
print(f"  138 trips checked, {len(bad)} mismatches" + (f": {bad[:3]}" if bad else ""))

print("\n--- category totals: expenses vs Category by Month ---")
bad = s.query(
    """
    SELECT e.expense_category, cm.total AS sheet, SUM(e.amount_usd) AS computed
    FROM expenses e JOIN category_month cm ON cm.expense_category = e.expense_category
    GROUP BY e.expense_category
    HAVING ABS(cm.total - SUM(e.amount_usd)) > 0.01
    """
)
print(f"  18 categories checked, {len(bad)} mismatches" + (f": {bad}" if bad else ""))

print("\n--- flagged items: expenses vs Policy Exceptions tab ---")
flagged = s.scalar(
    "SELECT COUNT(*) FROM expenses WHERE LOWER(over_policy)='yes' OR LOWER(receipt_attached)='no'"
)
tab = s.scalar("SELECT COUNT(*) FROM policy_exceptions")
over = s.scalar("SELECT COUNT(*) FROM expenses WHERE LOWER(over_policy)='yes'")
missing = s.scalar("SELECT COUNT(*) FROM expenses WHERE LOWER(receipt_attached)='no'")
print(f"  over limit={over}  missing receipt={missing}  union={flagged}  Policy Exceptions tab={tab}")
print(f"  match={flagged == tab}")
orphans = s.scalar(
    "SELECT COUNT(*) FROM policy_exceptions p "
    "LEFT JOIN expenses e ON e.expense_id = p.expense_id WHERE e.expense_id IS NULL"
)
print(f"  exception rows with no matching expense = {orphans}")

print("\n--- derived-column identities on expenses ---")
checks = {
    "amount_usd = amount_local * fx_rate": "ABS(amount_usd - amount_local * fx_rate_to_usd) > 0.01",
    "net = amount - vat": "ABS(net_of_tax_usd - (amount_usd - vat_tax_usd)) > 0.01",
    "variance = amount - limit": "ABS(variance_vs_limit_usd - (amount_usd - policy_limit_usd)) > 0.01",
    "over_policy flag agrees with amount vs limit": (
        "(LOWER(over_policy)='yes') <> (amount_usd > policy_limit_usd)"
    ),
    "corporate card => reimbursable 0": (
        "payment_method = 'Corporate Card' AND reimbursable_usd <> 0"
    ),
}
for label, predicate in checks.items():
    n = s.scalar(f"SELECT COUNT(*) FROM expenses WHERE {predicate}")
    print(f"  {'OK  ' if n == 0 else 'FAIL'} {label}: {n} violating rows")

print("\n--- filter sanity: 'Meals' substring match ---")
from helios_expenses.filters import ExpenseFilter

w, p = ExpenseFilter(category="Meals").where()
n = s.scalar(f"SELECT COUNT(*) FROM expenses WHERE {w}", p)
expect = s.scalar("SELECT COUNT(*) FROM expenses WHERE expense_category LIKE 'Meals%'")
print(f"  filter matched {n} rows, LIKE 'Meals%' matched {expect}, match={n == expect}")

w, p = ExpenseFilter(employee="EMP-1103", month_from="2025-01", month_to="2025-01").where()
print("  EMP-1103 in Jan:", s.scalar(f"SELECT COUNT(*) FROM expenses WHERE {w}", p), "rows")
