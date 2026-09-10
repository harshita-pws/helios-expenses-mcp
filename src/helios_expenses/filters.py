"""Shared filter model translated into a parameterised SQL WHERE clause."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

StrOrList = str | list[str] | None

# Filter field -> expenses column(s) it matches against.
_COLUMN_MAP: dict[str, tuple[str, ...]] = {
    "employee": ("employee_name", "employee_id"),
    "department": ("department",),
    "job_title": ("job_title",),
    "manager": ("manager_approver",),
    "category": ("expense_category",),
    "trip_id": ("trip_id",),
    "trip_purpose": ("trip_purpose",),
    "city": ("destination_city",),
    "country": ("destination_country",),
    "merchant": ("merchant_vendor",),
    "payment_method": ("payment_method",),
    "currency": ("currency",),
    "approval_status": ("approval_status",),
    "client_code": ("client_project_code",),
}


class ExpenseFilter(BaseModel):
    """Filters for the ``expenses`` table. Every field is optional; omitted
    fields do not constrain the result. Text filters are case-insensitive and
    match either the whole value or a substring of it, so "meals" matches all
    three meal categories and "Rohit" matches "Rohit Malhotra". Passing a list
    means "any of these"."""

    employee: StrOrList = Field(None, description="Employee name or EMP- id")
    department: StrOrList = None
    job_title: StrOrList = None
    manager: StrOrList = Field(None, description="Manager / approver name")
    category: StrOrList = Field(None, description="Expense category, e.g. 'Airfare', 'Meals'")
    trip_id: StrOrList = None
    trip_purpose: StrOrList = None
    city: StrOrList = Field(None, description="Destination city")
    country: StrOrList = Field(None, description="Destination country")
    merchant: StrOrList = None
    payment_method: StrOrList = Field(
        None, description="'Corporate Card', 'Personal Card (Reimbursable)', 'Cash (Reimbursable)'"
    )
    currency: StrOrList = Field(None, description="Local currency code, e.g. 'EUR'")
    approval_status: StrOrList = Field(
        None, description="'Approved', 'Pending Approval' or 'Rejected'"
    )
    client_code: StrOrList = Field(None, description="Client / project code, e.g. 'ACME-2201'")

    month: StrOrList = Field(None, description="Exact month(s) as 'YYYY-MM'")
    month_from: str | None = Field(None, description="Inclusive lower bound, 'YYYY-MM'")
    month_to: str | None = Field(None, description="Inclusive upper bound, 'YYYY-MM'")
    date_from: str | None = Field(None, description="Expense date >= this ISO date")
    date_to: str | None = Field(None, description="Expense date <= this ISO date")

    min_amount_usd: float | None = None
    max_amount_usd: float | None = None

    over_policy: bool | None = Field(None, description="True = only claims above their cap")
    receipt_attached: bool | None = Field(None, description="False = only missing receipts")
    billable: bool | None = Field(None, description="Billable to client")
    reimbursable_only: bool | None = Field(
        None, description="True = only out-of-pocket claims owed back to the employee"
    )
    text: str | None = Field(
        None, description="Substring searched across description, merchant and expense id"
    )

    def where(self, alias: str = "") -> tuple[str, list[Any]]:
        """Build ``(sql_fragment, params)``. The fragment is always truthy."""
        p = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[Any] = []

        for field_name, columns in _COLUMN_MAP.items():
            value = getattr(self, field_name)
            if value is None:
                continue
            values = [value] if isinstance(value, str) else list(value)
            values = [v for v in values if str(v).strip()]
            if not values:
                continue
            ors: list[str] = []
            for v in values:
                for col in columns:
                    ors.append(f'LOWER({p}"{col}") = LOWER(?)')
                    params.append(v)
                    ors.append(f'INSTR(LOWER({p}"{col}"), LOWER(?)) > 0')
                    params.append(v)
            clauses.append("(" + " OR ".join(ors) + ")")

        if self.month is not None:
            months = [self.month] if isinstance(self.month, str) else list(self.month)
            if months:
                marks = ", ".join("?" * len(months))
                clauses.append(f'{p}"month" IN ({marks})')
                params.extend(months)
        for value, op, col in (
            (self.month_from, ">=", "month"),
            (self.month_to, "<=", "month"),
            (self.date_from, ">=", "expense_date"),
            (self.date_to, "<=", "expense_date"),
            (self.min_amount_usd, ">=", "amount_usd"),
            (self.max_amount_usd, "<=", "amount_usd"),
        ):
            if value is not None:
                clauses.append(f'{p}"{col}" {op} ?')
                params.append(value)

        for value, col in (
            (self.over_policy, "over_policy"),
            (self.receipt_attached, "receipt_attached"),
            (self.billable, "billable_to_client"),
        ):
            if value is not None:
                clauses.append(f'LOWER({p}"{col}") = ?')
                params.append("yes" if value else "no")

        if self.reimbursable_only is not None:
            op = ">" if self.reimbursable_only else "<="
            clauses.append(f'{p}"reimbursable_usd" {op} 0')

        if self.text:
            like = f"%{self.text.lower()}%"
            cols = ("description", "merchant_vendor", "expense_id", "expense_category")
            ors = [f'LOWER({p}"{c}") LIKE ?' for c in cols]
            clauses.append("(" + " OR ".join(ors) + ")")
            params.extend([like] * len(cols))

        return (" AND ".join(clauses) if clauses else "1=1"), params

    def describe(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}
