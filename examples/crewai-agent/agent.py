"""CrewAI + ZizkaDB — log a crew run with causal links, then explain *why*.

A two-agent crew (Researcher → Writer) is logged to ZizkaDB with parent_id
lineage. After the run, ``db.why()`` reconstructs the causal chain:

    crew_kickoff → crew_task (Researcher) → crew_task (Writer) → crew_output

This is the point of difference from a plain logger: the run is a causal tree,
not a flat log.
"""

import asyncio
import os

from crewai import Agent, Crew, Task
from langchain_openai import ChatOpenAI

from zizkadb import ZizkaDB
from zizkadb_crewai import ZizkaDBCrewLogger

AGENT = os.getenv("ZIZKADB_AGENT", "crewai-research")
API_KEY = os.getenv("ZIZKADB_API_KEY")
HOST = os.getenv("ZIZKADB_HOST")
GOAL = "Explain why AI agents need causal event logging."


def build_crew() -> Crew:
    """A minimal Researcher → Writer crew (2 agents, 2 tasks)."""
    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    researcher = Agent(
        role="Researcher",
        goal="Explain technical concepts clearly",
        backstory="You write concise engineering summaries.",
        llm=llm,
        verbose=True,
    )
    writer = Agent(
        role="Writer",
        goal="Turn research notes into a polished summary",
        backstory="You write crisp developer-facing prose.",
        llm=llm,
        verbose=True,
    )
    research_task = Task(
        description="In 3 factual bullet points, explain why agents need parent_id causal logging.",
        expected_output="Three bullet points.",
        agent=researcher,
    )
    write_task = Task(
        description="Rewrite the three bullets into a polished three-sentence summary.",
        expected_output="A three-sentence summary.",
        agent=writer,
    )
    return Crew(agents=[researcher, writer], tasks=[research_task, write_task], verbose=True)


def connect() -> ZizkaDB:
    """An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    means ZizkaDB Cloud; neither means the local dev stack."""
    return ZizkaDB(api_key=API_KEY, host=HOST or (None if API_KEY else "http://localhost:8000"))


async def main() -> None:
    crew = build_crew()
    async with connect() as db:
        logger = ZizkaDBCrewLogger(db=db, agent=AGENT)

        await logger.log_kickoff(goal=GOAL)
        result = await crew.kickoff_async()
        for task_output in result.tasks_output:                    # one causal node per task
            await logger.log_task(
                description=task_output.description,
                output=str(task_output.raw)[:2000],
            )
        await logger.log_output(str(result))
        print(result)

        # The payoff — explain WHY the crew produced its output.
        print("\n=== ZizkaDB — why did this output happen? ===")
        (await db.why(logger.last_event_id)).print()


if __name__ == "__main__":
    asyncio.run(main())
