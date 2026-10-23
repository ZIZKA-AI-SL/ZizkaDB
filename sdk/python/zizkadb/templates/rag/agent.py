"""RAG agent template — retrieval + LLM with automatic lineage."""

import asyncio
import os

from zizkadb import ZizkaDB

AGENT = os.getenv("ZIZKADB_AGENT", "rag-agent")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")


async def main() -> None:
    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    host = HOST or (None if API_KEY else "http://localhost:8000")
    async with ZizkaDB(api_key=API_KEY, host=host) as db:
        async with db.track(agent=AGENT):
            await db.log(agent=AGENT, event="user_message", data={"text": "Find refund policy"})
            await db.log(
                agent=AGENT,
                event="retrieval",
                data={"query": "refund policy", "doc_ids": ["doc-1"], "chunk_count": 1},
            )
            answer = await db.log(
                agent=AGENT,
                event="assistant_response",
                data={"text": "Refunds within 30 days."},
            )
        (await db.why(answer.event_id)).print()


if __name__ == "__main__":
    asyncio.run(main())
