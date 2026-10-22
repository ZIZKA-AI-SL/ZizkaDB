# ZizkaDB + LiveKit Agents

**PyPI:** [zizkadb-livekit 0.2.1](https://pypi.org/project/zizkadb-livekit/) · depends on `zizkadb-sdk>=0.3.0` + `livekit-agents>=1.3.0`

Log voice calls to ZizkaDB with the same **Activity → Sessions / Events** model as text agents.

- **One `session_id` per call** (use the LiveKit room name or job id)
- **Transcript only** — text copied from LiveKit's chat history and `make_session_report()` (no audio in ZizkaDB)
- **Pipeline events** — tool calls, handoffs, state changes, interruptions, token usage
- **Causal lineage** — a single-stranded `parent_id` chain for Why? in the dashboard
- **Never on the audio path** — writes are queued and drained in the background; a ZizkaDB outage degrades observability, never the call

## Install

```bash
pip install "zizkadb-livekit>=0.2.1"
```

Start ZizkaDB (Docker quickstart):

```bash
curl -fsSL https://raw.githubusercontent.com/Zizka-ai/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

## Usage

```python
import os
from livekit.agents import AgentServer, JobContext, AgentSession, Agent
from zizkadb import ZizkaDB
from zizkadb_livekit import ZizkaDBLiveKitObserver, pop_observer, register_observer

server = AgentServer()


async def on_session_end(ctx: JobContext) -> None:
    # on_session_end receives the same JobContext as the entrypoint, so the
    # observer is looked up per call. One worker serves many calls concurrently;
    # a module-level observer would let them overwrite each other's transcripts.
    observer = pop_observer(ctx)
    if observer is None:
        return
    try:
        await observer.ingest_session_report(ctx)
    finally:
        await observer.aclose()


@server.rtc_session(agent_name="support-voice", on_session_end=on_session_end)
async def entrypoint(ctx: JobContext):
    db = ZizkaDB(host=os.getenv("ZIZKADB_HOST", "http://localhost:8000"))
    observer = ZizkaDBLiveKitObserver(
        db,
        agent=os.getenv("ZIZKADB_AGENT", "support-voice"),
        session_id=ctx.room.name,
    )
    register_observer(ctx, observer)

    session = AgentSession(...)  # your STT / LLM / TTS
    # Attach before session.start() so no turn is missed.
    observer.attach(session, job_ctx=ctx)
    observer.queue_session_started(room=ctx.room.name, job_id=ctx.job.id)

    await session.start(agent=Agent(instructions="..."), room=ctx.room)
    await ctx.connect()
```

Open **http://localhost:3001** → Activity → your agent → Sessions.

## Verbosity

Voice pipelines emit many more events than text agents, so events are tiered.
Each level logs its own tier and everything quieter; errors and session
boundaries are always logged.

| Level | Adds |
|-------|------|
| `transcript` | Turns, tool calls/results, handoffs, errors, session boundaries |
| `standard` (default) | State changes, interruptions, timeouts, token usage |
| `verbose` | Partial transcripts, turn-detector predictions, bounded `raw` payloads |

```python
observer = ZizkaDBLiveKitObserver(db, agent="...", session_id="...", level="verbose")
```

or set `ZIZKADB_EVENT_LEVEL` globally.

## Event types

| Event | Source |
|-------|--------|
| `session_started` / `session_ended` | Call boundaries |
| `user_message` / `assistant_response` | LiveKit chat history |
| `user_transcript` | Final STT result |
| `tool_call` / `tool_result` | Tool items + `function_tools_executed` |
| `agent_handoff` | Multi-agent workflow handoff |
| `user_state_changed` / `agent_state_changed` | Pipeline state |
| `usage_updated` | Token usage |
| `error` | Failures |

Manual helpers: `log_tool_call`, `log_tool_result`, `log_error`.

## Lifecycle

- `attach(session, job_ctx=ctx)` — subscribe to the live event stream, before `session.start()`. Never raises.
- `queue_session_started(...)` — non-blocking; use `await log_session_started(...)` if you need the event id.
- `ingest_session_report(ctx)` — back-fill the full transcript at call end.
- `flush(timeout=None)` — wait for queued writes (default `flush_timeout`, 10s).
- `aclose()` — flush, stop the writer, release the pooled connection. Idempotent; also usable as `async with`.

## Environment

```bash
export ZIZKADB_HOST=http://localhost:8000   # self-host
export ZIZKADB_AGENT=support-voice
export ZIZKADB_EVENT_LEVEL=standard         # transcript | standard | verbose
# Cloud: export ZIZKADB_API_KEY=zizkadb_live_...
```

See [examples/livekit-agent](../../examples/livekit-agent/) for a full sample.
