"""End-to-end check: spawns the server over stdio and calls it as a real MCP client."""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters, stdio_client

PARAMS = StdioServerParameters(command="uv", args=["run", "python", "-m", "helios_expenses.server"])


def text_of(result):
    parts = [c.text for c in result.content if getattr(c, "text", None)]
    return "\n".join(parts)


async def main():
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("connected to:", init.server_info.name, init.server_info.version)
            print("instructions:", (init.instructions or "")[:90], "...")

            tools = await session.list_tools()
            print(f"\n{len(tools.tools)} tools:")
            for t in tools.tools:
                ro = t.annotations.read_only_hint if t.annotations else None
                print(f"  {t.name:20} read_only={ro}  {(t.description or '').splitlines()[0][:70]}")

            res = await session.list_resources()
            print("\nresources:", [str(r.uri) for r in res.resources])
            pr = await session.list_prompts()
            print("prompts:  ", [p.name for p in pr.prompts])

            out = await session.call_tool(
                "aggregate_expenses",
                {
                    "group_by": ["department"],
                    "metrics": ["total_usd", "line_items", "over_policy_items"],
                    "limit": 4,
                },
            )
            print("\naggregate_expenses(department) ->")
            print(text_of(out)[:700])

            out = await session.call_tool(
                "policy_audit", {"reason": "both", "group_by": ["employee_name"], "limit": 3}
            )
            print("\npolicy_audit(both, by employee) ->")
            print(text_of(out)[:600])

            out = await session.call_tool(
                "search_expenses",
                {
                    "filter": {"category": "Hotel", "city": ["Tokyo", "Singapore"], "over_policy": True},
                    "limit": 2,
                    "sort_by": "amount_usd",
                    "descending": True,
                },
            )
            print("\nsearch_expenses(nested filter object) ->")
            print(text_of(out)[:900])

            print("\nerror messages reaching the client:")
            for tool, args in [
                ("query_sql", {"sql": "DELETE FROM expenses"}),
                ("query_sql", {"sql": "SELECT * FROM nope"}),
                ("list_values", {"dimension": "salary"}),
                ("aggregate_expenses", {"metrics": ["bogus"]}),
                ("get_trip", {"trip_id": "TRP-2025-999"}),
                ("search_expenses", {"filter": {"min_amount_usd": "not-a-number"}}),
            ]:
                out = await session.call_tool(tool, args)
                print(f"  {tool}: is_error={out.is_error} -> {text_of(out)[:150]}")

            content = await session.read_resource("helios://policy-limits")
            print("\nresource helios://policy-limits ->")
            print(content.contents[0].text[:500])

            got = await session.get_prompt("audit_review", {"scope": "Q4 2025"})
            print("\nprompt audit_review ->", got.messages[0].content.text[:150], "...")

    print("\nstdio round trip OK")


asyncio.run(main())
