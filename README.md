# helios-expenses — MCP server over the FY2025 travel expense workbook

An MCP server that makes `Helios_Business_Travel_Expenses_2025.xlsx` queryable by an
LLM. On startup the workbook is loaded into an **in-memory SQLite database** — one
table per sheet — so questions are answered with SQL aggregates instead of by
streaming thousands of spreadsheet rows into the context window.

The server is **read-only**. It never writes to the `.xlsx`.
