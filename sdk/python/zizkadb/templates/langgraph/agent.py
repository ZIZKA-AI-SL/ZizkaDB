"""LangGraph-style agent using zizkadb-langgraph middleware."""

import asyncio
import os

from zizkadb import ZizkaDB
from zizkadb_langgraph import ZizkaDBLangGraphMiddleware, wrap_node

AGENT = os.getenv("ZIZKADB_AGENT", "graph-agent")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")


async def research(state: dict) -> dict:
    return {"notes": "found 2 sources"}


async def main() -> None:
    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    host = HOST or (None if API_KEY else "http://localhost:8000")
    async with ZizkaDB(api_key=API_KEY, host=host) as db:
        async with db.track(agent=AGENT) as ctx:
            mw = ZizkaDBLangGraphMiddleware(db, agent=AGENT, session_id=ctx.session_id)
            state: dict = {}
            node = wrap_node(mw, "research", research)
            await node(state)
        print("Logged graph nodes — open Activity dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
