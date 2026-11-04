"""OpenAI + ZizkaDB — causal logging with parent_id."""

import asyncio
import os

from openai import AsyncOpenAI
from zizkadb import ZizkaDB

AGENT = os.getenv("ZIZKADB_AGENT", "gpt-4o-agent")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")


async def run(user_input: str) -> str:
    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    host = HOST or (None if API_KEY else "http://localhost:8000")
    async with ZizkaDB(api_key=API_KEY, host=host) as db:
        client = AsyncOpenAI()
        turn = await db.log(
            agent=AGENT,
            event="user_message",
            data={"text": user_input},
        )
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": user_input}],
        )
        text = response.choices[0].message.content or ""
        await db.log(
            agent=AGENT,
            event="assistant_response",
            data={"text": text, "model": response.model},
            parent_id=turn.event_id,
        )
    return text


async def main() -> None:
    reply = await run("What is causal lineage in agent systems?")
    print(reply)


if __name__ == "__main__":
    asyncio.run(main())
