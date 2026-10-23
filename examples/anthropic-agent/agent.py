"""Claude + ZizkaDB — causal logging with parent_id."""

import asyncio
import os

import anthropic
from zizkadb import ZizkaDB

AGENT = os.getenv("ZIZKADB_AGENT", "claude-agent")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")


async def run(user_input: str) -> str:
    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    host = HOST or (None if API_KEY else "http://localhost:8000")
    async with ZizkaDB(api_key=API_KEY, host=host) as db:
        client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        turn = await db.log(
            agent=AGENT,
            event="user_message",
            data={"text": user_input},
        )
        # Server-side fallback: if the model declines, the API retries on a
        # fallback model inside the same call.
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user_input}],
        )
        if response.stop_reason == "refusal":
            text = "[refused]"
        else:
            text = "".join(b.text for b in response.content if b.type == "text")
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
