"""CrewAI helpers — log crew kickoff, tasks, and outputs to ZizkaDB."""

from __future__ import annotations

from typing import Any

from zizkadb import ZizkaDB
from zizkadb.models import LogResult


class ZizkaDBCrewLogger:
    """
    Thin wrapper for logging CrewAI runs with causal links.

    Usage:
        logger = ZizkaDBCrewLogger(db, agent="research-crew")
        kickoff = await logger.log_kickoff(goal="...")
        result = await crew.kickoff_async()
        await logger.log_output(str(result), parent_id=kickoff.event_id)
    """

    def __init__(
        self,
        db: ZizkaDB,
        agent: str,
        session_id: str | None = None,
    ) -> None:
        self.db = db
        self.agent = agent
        self.session_id = session_id
        self.last_event_id: str | None = None

    async def log_kickoff(self, goal: str, **extra: Any) -> LogResult:
        result = await self.db.log(
            agent=self.agent,
            event="crew_kickoff",
            data={"goal": goal, **extra},
            session_id=self.session_id,
        )
        self.last_event_id = result.event_id
        return result

    async def log_task(
        self,
        description: str,
        *,
        parent_id: str | None = None,
        **extra: Any,
    ) -> LogResult:
        result = await self.db.log(
            agent=self.agent,
            event="crew_task",
            data={"description": description, **extra},
            parent_id=parent_id or self.last_event_id,
            session_id=self.session_id,
        )
        self.last_event_id = result.event_id
        return result

    async def log_output(
        self,
        output: str,
        *,
        parent_id: str | None = None,
        **extra: Any,
    ) -> LogResult:
        result = await self.db.log(
            agent=self.agent,
            event="crew_output",
            data={"output": output[:8000], **extra},
            parent_id=parent_id or self.last_event_id,
            session_id=self.session_id,
        )
        self.last_event_id = result.event_id
        return result

    async def log_tool(
        self,
        tool: str,
        *,
        args: Any = None,
        parent_id: str | None = None,
    ) -> LogResult:
        """Log a tool a crew agent is about to use, by name."""
        result = await self.db.log_tool(
            self.agent,
            tool,
            args=args,
            parent_id=parent_id or self.last_event_id,
            session_id=self.session_id,
        )
        self.last_event_id = result.event_id
        return result

    async def log_tool_result(
        self,
        tool: str,
        *,
        result: Any = None,
        error: str | BaseException | None = None,
        parent_id: str | None = None,
    ) -> LogResult:
        """Log a tool's output, or its failure when ``error`` is given."""
        logged = await self.db.log_tool_result(
            self.agent,
            tool,
            result=result,
            error=error,
            parent_id=parent_id or self.last_event_id,
            session_id=self.session_id,
        )
        self.last_event_id = logged.event_id
        return logged
