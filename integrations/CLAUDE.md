# integrations/ — Framework Adapters

See root [`CLAUDE.md`](../CLAUDE.md) for full project context.

Standalone adapter packages: **`zizkadb-langchain`**, **`zizkadb-crewai`**, **`zizkadb-livekit`**.

---

## Packages

| Package | Directory | Class | PyPI (current) |
|---|---|---|---|
| `zizkadb-langchain` | `integrations/langchain/` | `ZizkaDBCallbackHandler` | `0.1.3` |
| `zizkadb-crewai` | `integrations/crewai/` | `ZizkaDBCrewLogger` | `0.1.3` |
| `zizkadb-livekit` | `integrations/livekit/` | `ZizkaDBLiveKitObserver` | `0.2.0` |

Install for users:
```bash
pip install zizkadb-langchain    # or zizkadb-crewai / zizkadb-livekit
```

Each package depends on `zizkadb-sdk>=0.3.0` — users install the framework package only.

---

## LangChain & CrewAI: code lives in two places

Integration source is **duplicated** for LangChain and CrewAI — keep both in sync when editing:

| Standalone (this directory) | Bundled in SDK |
|---|---|
| `integrations/langchain/zizkadb_langchain/callbacks.py` | `sdk/python/zizkadb/integrations/langchain/callbacks.py` |
| `integrations/crewai/zizkadb_crewai/logger.py` | `sdk/python/zizkadb/integrations/crewai/logger.py` |

**LiveKit** lives only under `integrations/livekit/zizkadb_livekit/` — SDK re-exports when the package is installed.

---

## LiveKit usage

One call → one ZizkaDB session. Transcript from LiveKit report; no audio stored.

```python
from zizkadb import ZizkaDB
from zizkadb_livekit import ZizkaDBLiveKitObserver

db = ZizkaDB(host="http://localhost:8000")
observer = ZizkaDBLiveKitObserver(db, agent="support-voice", session_id=f"call_{room}")
await observer.ingest_session_report(ctx)  # wire to on_session_end
```

See [`examples/livekit-agent/`](../examples/livekit-agent/) and [`docs/integrations/livekit.md`](../docs/integrations/livekit.md).

---

## LangChain usage

```python
from zizkadb import ZizkaDB
from zizkadb_langchain import ZizkaDBCallbackHandler

async with ZizkaDB(api_key="zizkadb_live_...") as db:
    handler = ZizkaDBCallbackHandler(db=db, agent="my-bot")
    result = await chain.ainvoke({"input": "..."}, config={"callbacks": [handler]})
    await db.why(handler.last_event_id).print()
```

---

## CrewAI usage

`ZizkaDBCrewLogger` logs a crew run with causal `parent_id` lineage. Three methods, all chained
via the logger's `last_event_id` (so you can omit `parent_id` to auto-link to the previous event):

| Method | Event type | Purpose |
|---|---|---|
| `await log_kickoff(goal, **extra)` | `crew_kickoff` | Start of the run (root of the chain) |
| `await log_task(description, *, parent_id=None, **extra)` | `crew_task` | One node per task |
| `await log_output(output, *, parent_id=None, **extra)` | `crew_output` | Final result |

```python
from zizkadb import ZizkaDB
from zizkadb_crewai import ZizkaDBCrewLogger

# OSS-first: default to a local self-hosted instance; api_key only for remote/cloud.
async with ZizkaDB(host="http://localhost:8000") as db:
    logger = ZizkaDBCrewLogger(db=db, agent="research-crew")
    await logger.log_kickoff(goal="Research AI trends")
    result = await crew.kickoff_async()
    for task_output in result.tasks_output:                      # one node per task
        await logger.log_task(description=task_output.description, output=str(task_output.raw)[:2000])
    await logger.log_output(str(result))

    # The differentiator — explain WHY the output happened:
    (await db.why(logger.last_event_id)).print()                 # kickoff → task → task → output
```

The **value proposition** (not "just another logger"): `db.why()` walks the `parent_id` links back
to the root cause, so a crew run becomes a causal tree, not a flat log. See the runnable proof at
[`examples/crewai-agent/`](../examples/crewai-agent/).

**Ecosystem contribution:** ZizkaDB is listed under CrewAI's community integrations —
[`awesome-crewai` PR #91](https://github.com/crewAIInc/awesome-crewai/pull/91). Note: the historical
`crewAIInc/crewAI-examples` repo is **archived** (read-only); `awesome-crewai` is the active home for
third-party integrations.

---

## Dev install (monorepo)

```bash
pip install -e sdk/python -e integrations/langchain -e integrations/crewai -e integrations/livekit
```

---

## Publish

```bash
bash scripts/publish-integrations.sh
```

Versions are in each package's `pyproject.toml`. Bump together with the SDK versions — see `.cursor/skills/zizkadb-release/SKILL.md`.
