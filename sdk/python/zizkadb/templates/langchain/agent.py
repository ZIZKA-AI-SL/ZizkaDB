"""LangChain + ZizkaDB — callback handler logs every LLM and tool step."""

import asyncio
import os

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from zizkadb import ZizkaDB
from zizkadb.integrations.langchain import ZizkaDBCallbackHandler

AGENT = os.getenv("ZIZKADB_AGENT", "langchain-agent")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")


async def main() -> None:
    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    host = HOST or (None if API_KEY else "http://localhost:8000")
    async with ZizkaDB(api_key=API_KEY, host=host) as db:
        handler = ZizkaDBCallbackHandler(db=db, agent=AGENT)
        llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        result = await llm.ainvoke(
            [HumanMessage(content="Summarize why causal logging matters for agents.")],
            config={"callbacks": [handler]},
        )
        print(result.content)
        if handler.last_event_id:
            (await db.why(handler.last_event_id)).print()


if __name__ == "__main__":
    asyncio.run(main())
