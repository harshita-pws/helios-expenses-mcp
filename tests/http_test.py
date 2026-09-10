"""Checks the hosted HTTP deployment the way a remote MCP client sees it.

Usage:
    uv run tests/http_test.py                       # against a local uvicorn
    uv run tests/http_test.py https://x.onrender.com/mcp <token>
"""

import asyncio
import os
import sys

try:  # mcp 2.x ships httpx2; fall back to httpx if only that is present
    import httpx2 as httpx
except ModuleNotFoundError:  # pragma: no cover
    import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("MCP_AUTH_TOKEN", "")
BASE = URL.rsplit("/", 1)[0]


def text_of(result):
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


async def main():
    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.get(f"{BASE}/healthz")
        print(f"GET /healthz -> {r.status_code} {r.text[:200]}")
        r = await http.get(BASE + "/")
        print(f"GET /         -> {r.status_code}\n{r.text[:300]}")
        if TOKEN:
            r = await http.post(
                URL,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Content-Type": "application/json"},
            )
            print(f"POST {URL} without token -> {r.status_code} (expect 401)")

    # 2.x takes auth via a pre-configured httpx client rather than a headers kwarg.
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    http_client = httpx.AsyncClient(headers=headers, timeout=60)
    async with streamable_http_client(URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"\nconnected over HTTP: {init.server_info.name} {init.server_info.version}")
            tools = await session.list_tools()
            print(f"{len(tools.tools)} tools available")

            out = await session.call_tool(
                "aggregate_expenses",
                {"group_by": ["department"], "metrics": ["total_usd"], "limit": 3},
            )
            print("\naggregate_expenses ->", text_of(out)[:320])

            out = await session.call_tool("get_employee", {"employee": "Sneha"})
            body = text_of(out)
            print("\nget_employee('Sneha') ->", body[:220])

            out = await session.call_tool("query_sql", {"sql": "DROP TABLE expenses"})
            print("\nquery_sql(DROP) is_error =", out.is_error, "->", text_of(out)[:120])

    print("\nHTTP transport OK")


asyncio.run(main())
