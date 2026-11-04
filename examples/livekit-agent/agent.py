"""
LiveKit voice agent → ZizkaDB Activity (Sessions + Events).

Prerequisites:
  1. ZizkaDB: bash scripts/quickstart-remote.sh  (or quickstart.sh from clone)
  2. LiveKit Cloud project + API keys in .env
  3. OPENAI_API_KEY for OpenAI Realtime voice
  4. pip install -r requirements.txt

Run (voice worker):
  python agent.py dev

Run (browser UI — separate terminal):
  python web_server.py

After a test call, open http://localhost:3001 → Activity → support-voice → Sessions.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, JobContext, AgentSession
from livekit.plugins import openai
from zizkadb import ZizkaDB
from zizkadb_livekit import ZizkaDBLiveKitObserver, pop_observer, register_observer

load_dotenv()

AGENT_NAME = os.getenv("ZIZKADB_AGENT") or os.getenv("AGENT_NAME", "support-voice")

server = AgentServer()


async def on_session_end(ctx: JobContext) -> None:
    # on_session_end receives the same JobContext as the entrypoint, so the
    # observer is looked up per call. A worker serves many calls at once.
    observer = pop_observer(ctx)
    if observer is None:
        return
    try:
        await observer.ingest_session_report(ctx)
    finally:
        await observer.aclose()


@server.rtc_session(agent_name=AGENT_NAME, on_session_end=on_session_end)
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # An explicit ZIZKADB_HOST wins (self-hosted with an API key); a key alone
    # means ZizkaDB Cloud; neither means the local dev stack.
    api_key = os.getenv("ZIZKADB_API_KEY")
    host = os.getenv("ZIZKADB_HOST") or (None if api_key else "http://localhost:8000")
    agent_name = AGENT_NAME
    # Room name is already unique (e.g. call_<uuid> from web_server token)
    session_id = ctx.room.name

    db = ZizkaDB(api_key=api_key, host=host)

    observer = ZizkaDBLiveKitObserver(db, agent=agent_name, session_id=session_id)
    register_observer(ctx, observer)
    await observer.log_session_started(
        room=ctx.room.name,
        job_id=getattr(ctx.job, "id", None),
    )

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is required for voice conversation. "
            "Add it to examples/livekit-agent/.env and restart the agent worker."
        )

    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice=os.getenv("OPENAI_VOICE", "coral")),
    )
    observer.attach(session, job_ctx=ctx)

    await session.start(
        agent=Agent(
            instructions="You are a helpful voice support agent. Be concise.",
        ),
        room=ctx.room,
    )


if __name__ == "__main__":
    from livekit.agents import cli

    cli.run_app(server)
