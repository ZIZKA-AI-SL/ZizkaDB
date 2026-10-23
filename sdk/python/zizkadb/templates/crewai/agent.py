"""CrewAI + ZizkaDB — log task lifecycle with causal links."""

import asyncio
import os

from crewai import Agent, Crew, Task
from langchain_openai import ChatOpenAI

from zizkadb import ZizkaDB
from zizkadb.integrations.crewai import ZizkaDBCrewLogger

AGENT = os.getenv("ZIZKADB_AGENT", "crewai-researcher")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")


async def main() -> None:
    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    host = HOST or (None if API_KEY else "http://localhost:8000")
    async with ZizkaDB(api_key=API_KEY, host=host) as db:
        logger = ZizkaDBCrewLogger(db=db, agent=AGENT)

        kickoff = await logger.log_kickoff(goal="Explain causal logging for AI agents")
        researcher = Agent(
            role="Researcher",
            goal="Explain technical concepts clearly",
            backstory="You write concise engineering summaries.",
            llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            verbose=True,
        )
        task = Task(
            description="In 3 bullets, explain why agents need parent_id causal logging.",
            expected_output="Three bullet points.",
            agent=researcher,
        )
        crew = Crew(agents=[researcher], tasks=[task], verbose=True)
        result = await crew.kickoff_async()
        await logger.log_output(str(result), parent_id=kickoff.event_id)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
