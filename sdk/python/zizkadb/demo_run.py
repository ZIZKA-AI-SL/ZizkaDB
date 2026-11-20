"""Run the support-bot causal lineage demo (OSS quick win)."""

from __future__ import annotations

import os

from .client import ZizkaDB, _is_local_host

DEFAULT_HOST = "http://localhost:8000"
DEMO_AGENT = "support-bot"


def dashboard_activity_url(host: str, agent: str = DEMO_AGENT) -> str:
    """Deep link to Activity for an agent (local or cloud)."""
    base = "http://localhost:3001" if _is_local_host(host) else "https://db.zizka.ai"
    return f"{base}/dashboard/activity?agent={agent}"


async def run_support_order_delay_demo(host: str | None = None) -> str:
    """Log a 3-step chain and print db.why(). Returns the leaf event_id."""
    api_host = host or os.getenv("ZIZKADB_HOST", DEFAULT_HOST)
    print(f"→ ZizkaDB @ {api_host}\n")

    async with ZizkaDB(host=api_host) as db:
        user = await db.log(
            agent=DEMO_AGENT,
            event="user_message",
            data={"text": "Why was my order delayed?"},
        )
        reply = await db.log(
            agent=DEMO_AGENT,
            event="llm_response",
            data={"model": "gpt-4o", "tokens": 412},
            parent_id=user.event_id,
        )
        tool = await db.log(
            agent=DEMO_AGENT,
            event="tool_call",
            data={"tool": "lookup_order", "order_id": "ORD-8842"},
            parent_id=reply.event_id,
        )

        print("Logged chain. Walking back with db.why():\n")
        chain = await db.why(tool.event_id)
        chain.print()

    activity = dashboard_activity_url(api_host, DEMO_AGENT)
    print("\n✓ Done — open the dashboard to explore this agent:")
    if _is_local_host(api_host):
        print(f"  {activity}")
        print("  (log in page → Open my dashboard → if prompted)")
    else:
        print(f"  {activity}")
    print(f"\n  CLI:  zizkadb why {tool.event_id}")
    return tool.event_id
