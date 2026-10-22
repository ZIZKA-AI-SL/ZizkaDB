# LiveKit integration

**Package:** [`zizkadb-livekit`](https://pypi.org/project/zizkadb-livekit/) **0.2.0** on PyPI (AGPL-3.0)  
**Class:** `ZizkaDBLiveKitObserver`  
**Source:** [`integrations/livekit/zizkadb_livekit/`](../../integrations/livekit/zizkadb_livekit/)  
**Runnable example:** [`examples/livekit-agent/`](../../examples/livekit-agent/)

## Why this exists

Voice agents built with [LiveKit Agents](https://docs.livekit.io/agents/) need the same operational audit trail as text agents: *why did the agent say that, and which tool caused it?*

`zizkadb-livekit` maps **one LiveKit call → one ZizkaDB session** in the existing **Activity → Sessions / Events** UI. Transcript text is copied from LiveKit's session report at call end — **no audio** is stored in ZizkaDB.

Event types match text agents: `session_started`, `user_message`, `assistant_response`, `tool_call`, `tool_result`, `error`, `session_ended`. Each event links via `parent_id` so `db.why()` works in the dashboard.

## Install (one command)

```bash
pip install zizkadb-livekit
```

Pulls `zizkadb-sdk>=0.3.0` and `livekit-agents>=1.3.0` automatically. No separate core SDK install.

Start ZizkaDB (Docker):

```bash
curl -fsSL https://raw.githubusercontent.com/Zizka-ai/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

## Quick wiring

```python
import os
from livekit.agents import AgentServer, JobContext, AgentSession, Agent
from zizkadb import ZizkaDB
from zizkadb_livekit import ZizkaDBLiveKitObserver, pop_observer, register_observer

server = AgentServer()


async def on_session_end(ctx: JobContext) -> None:
    # on_session_end gets the same JobContext as the entrypoint, so the observer
    # is looked up per call. One worker serves many calls at once — a module-level
    # observer would let them overwrite each other's transcripts.
    observer = pop_observer(ctx)
    if observer is None:
        return
    try:
        await observer.ingest_session_report(ctx)
    finally:
        await observer.aclose()  # flushes queued events, closes the connection


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

`queue_session_started()` returns immediately; use `await observer.log_session_started(...)`
only when you need the resulting event id. Nothing on the ZizkaDB path ever blocks
the audio path — writes go onto a background queue.


After a test call: **http://localhost:3001 → Activity → support-voice → Sessions**.

## Environment

| Variable | Purpose |
|----------|---------|
| `ZIZKADB_HOST` | Self-host API URL (default `http://localhost:8000`) |
| `ZIZKADB_AGENT` | Agent name — must match dashboard |
| `ZIZKADB_API_KEY` | Cloud key (`zizkadb_live_...`) instead of `host=` |
| `ZIZKADB_EVENT_LEVEL` | Verbosity: `transcript`, `standard` (default), `verbose` |
| LiveKit vars | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — see [LiveKit docs](https://docs.livekit.io/) |

## Verbosity levels

Voice pipelines emit far more events than text agents, so events are tiered. A
level logs its own tier and everything quieter. `error`, `session_started` and
`session_ended` are always logged.

| Level | Adds |
|-------|------|
| `transcript` | Turns, tool calls/results, handoffs, errors, session boundaries |
| `standard` (default) | Pipeline detail: state changes, interruptions, timeouts, token usage |
| `verbose` | Partial transcripts, turn-detector predictions, speech events, and a bounded `raw` copy of each LiveKit payload |

Set per observer (`level="verbose"`) or globally via `ZIZKADB_EVENT_LEVEL`.

## Event mapping

| ZizkaDB event | Source |
|---------------|--------|
| `session_started` / `session_ended` | Call boundaries |
| `user_message` / `assistant_response` | LiveKit chat history |
| `user_transcript` | Final STT result (`user_input_transcribed`) |
| `tool_call` / `tool_result` | `function_call` items, `function_tools_executed` |
| `agent_handoff` | Multi-agent workflow handoff |
| `user_state_changed` / `agent_state_changed` | Pipeline state |
| `overlapping_speech`, `false_interruption`, `transcription_timeout` | Turn-taking |
| `usage_updated` | Token usage |
| `error` | Failures |

Manual helpers: `log_tool_call`, `log_tool_result`, `log_error`.

## Lifecycle

- `attach(session, job_ctx=ctx)` — subscribe to the live event stream. Call it
  **before** `session.start()` so no turn is missed. Never raises.
- `ingest_session_report(ctx)` — back-fill the full transcript at call end.
- `flush()` — wait for queued writes (bounded by `flush_timeout`, default 10s).
- `aclose()` — flush, stop the writer, release the pooled connection. Idempotent.
  The observer is also an async context manager.

Writes are queued and drained by one background task, so no HTTP round trip ever
sits on the audio path, and the `parent_id` chain stays single-stranded. Every
failure path is swallowed with a warning: a ZizkaDB outage degrades observability,
never the call.

## Monorepo dev

```bash
pip install -e sdk/python -e integrations/livekit
pytest sdk/python/tests/ -k livekit -v
```

## Related

- Package README: [integrations/livekit/README.md](../../integrations/livekit/README.md)
- Connect guide: [CONNECT.md](../../CONNECT.md#livekit-agents-voice)
- Framework status: [integrate/frameworks.md](../integrate/frameworks.md)
