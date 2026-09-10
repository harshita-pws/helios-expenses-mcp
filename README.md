# helios-expenses — MCP server over the FY2025 travel expense workbook

An MCP server that makes `Helios_Business_Travel_Expenses_2025.xlsx` queryable by an
LLM. On startup the workbook is loaded into an **in-memory SQLite database** — one
table per sheet — so questions are answered with SQL aggregates instead of by
streaming thousands of spreadsheet rows into the context window.

The server is **read-only**. It never writes to the `.xlsx`.

## What gets loaded

| Table | Sheet | Rows |
|---|---|---|
| `expenses` | Expense Details | 2,954 claims × 37 columns (the grain: one receipt per row) |
| `trips` | Trip Summary | 138 |
| `employees` | Employee Summary | 24 |
| `category_month` | Category by Month | 18 categories × 12 months |
| `policy_exceptions` | Policy Exceptions | 519 flagged claims |

Headers are normalised to SQL-safe snake_case (`Amount (USD)` → `amount_usd`,
`% Over Limit` → `pct_over_limit`) and the original header is kept for display.
Dates become ISO `YYYY-MM-DD` text, so string comparison equals date comparison.

Each summary sheet ends with a grand-total row (`TOTAL`). Those rows are held aside
during load — otherwise every `SUM` over those tables would double-count — and are
returned separately by `read_sheet` as `sheet_total_row`.

## Tools

| Tool | Use it for |
|---|---|
| `overview` | Headline figures: spend, coverage, compliance rate, top slices. Start here. |
| `describe_schema` | Every table, column, type and the allowed values of each low-cardinality column. |
| `list_values` | Distinct values of one column, with spend, so you can filter on exact spellings. |
| `search_expenses` | List individual claims matching a filter, with the full matching count and total. |
| `aggregate_expenses` | Group-and-total: spend by department, category by month, top N by anything. |
| `policy_audit` | Over-limit and missing-receipt claims, with variance vs the cap. |
| `get_trip` | One trip: summary row, category breakdown, line items. |
| `get_employee` | One traveller: summary row, trips, spend by category and month. |
| `read_sheet` | A workbook tab verbatim, for "what does the summary tab say" questions. |
| `query_sql` | Arbitrary read-only `SELECT`/`WITH` — window functions, joins across tabs. |
| `reload_workbook` | Re-read the file after the spreadsheet has been edited. |

All tools are annotated `readOnlyHint: true`, so clients can run them without
prompting for approval.

### Filtering

`search_expenses`, `aggregate_expenses` and `policy_audit` share one `filter`
object. Every field is optional. Text filters are case-insensitive and match a
whole value *or* a substring of it, so `category: "Meals"` catches all three meal
categories and `employee: "Rohit"` finds `Rohit Malhotra`. A list means "any of".

```jsonc
{
  "filter": {
    "department": "Sales",
    "category": ["Airfare", "Hotel"],
    "city": ["Tokyo", "Singapore"],
    "month_from": "2025-01", "month_to": "2025-03",
    "min_amount_usd": 500,
    "over_policy": true,
    "receipt_attached": false,
    "approval_status": "Approved",
    "text": "Marriott"
  }
}
```

`aggregate_expenses` metrics: `total_usd`, `line_items`, `avg_usd`, `max_usd`,
`min_usd`, `total_local`, `reimbursable_usd`, `vat_usd`, `net_of_tax_usd`,
`billable_usd`, `over_policy_items`, `over_policy_usd`, `missing_receipts`,
`rejected_items`, `pending_items`, `trips`, `employees`, `avg_days_to_reimburse`.

### Safety of `query_sql`

Only a single `SELECT`/`WITH` statement is accepted. Multiple statements, every
write keyword, `ATTACH` and `PRAGMA` are rejected, and results are capped. The
SQLite database is a throwaway in-memory copy, so the workbook cannot be altered
through it.

## Resources and prompts

- `helios://schema` — the schema, as text
- `helios://readme` — the workbook's own README tab (grain, derivations, assumptions)
- `helios://policy-limits` — the per-claim USD cap for each category
- Prompts: `audit_review(scope)`, `spend_summary(dimension)`

## Install and run

```powershell
uv sync
uv run python -m helios_expenses.server   # speaks MCP over stdio
uv run mcp dev server_entry.py            # or poke at it in the MCP Inspector UI
```

`server_entry.py` exists only for the second command: `mcp dev` imports a server
*file* by path, which breaks the package's relative imports, so the shim re-exports
the server object. Use `python -m helios_expenses.server` everywhere else.

The workbook path defaults to `~/Downloads/Helios_Business_Travel_Expenses_2025.xlsx`;
override it with the `HELIOS_XLSX` environment variable.

### Register with Claude Code

`.mcp.json` in this directory already declares the server, so Claude Code picks it
up when started here. To register it globally instead:

```powershell
claude mcp add helios-expenses --scope user -- uv run --directory "C:\Users\Harshita Kumari\Desktop\mcpServer" python -m helios_expenses.server
```

### Register with Claude Desktop

Add the `mcpServers` block from `.mcp.json` to
`%APPDATA%\Claude\claude_desktop_config.json` and restart the app.

## Deploy to Render (free tier)

The free plan serves HTTP only, so a deployed instance uses the **streamable HTTP**
transport instead of stdio. `app.py` is the ASGI entry point; `render.yaml` is a
blueprint Render can read directly.

```powershell
git init
git add -A
git commit -m "MCP server over the Helios expense workbook"
git remote add origin https://github.com/<you>/helios-expenses-mcp.git
git push -u origin main
```

Then in Render: **New → Blueprint**, pick the repo, apply. It reads `render.yaml`
and builds with `pip install .`, starts `uvicorn app:app --host 0.0.0.0 --port $PORT`,
and health-checks `/healthz`. Or configure a Web Service by hand with those two
commands and `Free` as the plan.

The workbook is committed to `data/` (~1 MB), so no external storage is needed —
Render's free tier has no persistent disk.

### Connecting a client to the deployed server

The endpoint has no authentication - anyone with the URL can call it.

```powershell
claude mcp add --transport http helios-expenses https://<your-service>.onrender.com/mcp
```

Verify the deployment from your machine:

```powershell
curl https://<your-service>.onrender.com/healthz
uv run tests/http_test.py https://<your-service>.onrender.com/mcp
```

### Free-tier behaviour worth knowing

- **Cold starts.** A free service spins down after ~15 minutes idle and takes
  roughly 30-60 s to wake. The first MCP call after an idle period can time out
  in the client — retry and it connects. This is the main reason the transport
  runs `stateless_http=True`: no session state to lose across a spin-down.
- **Memory.** Loading the workbook and building the SQLite mirror measured ~83 MB
  RSS locally, well inside the 512 MB limit.
- **Ephemeral disk.** `reload_workbook` re-reads the copy baked into the deploy,
  so to change the data you commit a new `.xlsx` and redeploy.
- **Auth.** There is none. The endpoint is open to anyone who finds the URL,
  and the server logs a warning to that effect at boot. Fine for this synthetic
  dataset; add authentication before pointing this pattern at real data.
- **Host allowlisting.** DNS-rebinding protection is on, and the SDK has no
  wildcard for a whole host, so the deployed hostname must be allowlisted.
  `app.py` reads Render's `RENDER_EXTERNAL_HOSTNAME` automatically; for any other
  host set `MCP_ALLOWED_HOSTS` to a comma-separated list.

### Environment variables

| Variable | Purpose |
|---|---|
| `HELIOS_XLSX` | Path to the workbook. Defaults to `data/`, then `~/Downloads`. |
| `MCP_PATH` | Endpoint path, default `/mcp`. |
| `MCP_ALLOWED_HOSTS` | Extra allowed `Host` values, comma-separated. |
| `PORT` | Injected by Render; used by `python app.py`. |

## Tests

```powershell
uv run tests/validate.py     # reconciles tool output against the workbook's own derived tabs
uv run tests/smoke_test.py   # exercises every tool in-process, incl. rejection paths
uv run tests/client_test.py  # spawns the server and drives it as a real MCP client over stdio
uv run tests/http_test.py    # same, over HTTP: health, landing page, 401, tool calls
```

`http_test.py` needs a server running. Locally:

```powershell
uv run uvicorn app:app --host 127.0.0.1 --port 8765
uv run tests/http_test.py                       # in a second terminal
```

`validate.py` is the one that matters: it checks the server's numbers against the
spreadsheet's own formulas — grand total, per-employee, per-trip and per-category
totals, the flagged-item count, and the derived-column identities
(`amount_usd = amount_local × fx_rate`, `net = amount − vat`,
`variance = amount − limit`, corporate-card claims being non-reimbursable).
All reconcile exactly.

## Layout

```
src/helios_expenses/
  store.py     workbook -> SQLite loader, schema introspection, SQL guard
  filters.py   the shared filter model -> parameterised WHERE clause
  server.py    MCP tools, resources and prompts
app.py         ASGI app for hosted HTTP deployment (host allowlist, /healthz)
server_entry.py  shim so `mcp dev <file>` can import the server
data/          the workbook, committed so deployments are self-contained
render.yaml    Render blueprint (free web service)
```
