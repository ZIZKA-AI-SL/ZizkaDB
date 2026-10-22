# zizkadb-crewai

**PyPI:** [zizkadb-crewai 0.1.3](https://pypi.org/project/zizkadb-crewai/0.1.3/) · requires `zizkadb-sdk>=0.3.0`

Self-hosted, open-source **observability and causal debugging** for [CrewAI](https://crewai.com).

A CrewAI run is normally a black box: when the output is wrong, it's hard to see which task or
agent caused it. `zizkadb-crewai` logs every step of a crew run to [ZizkaDB](https://github.com/Zizka-ai/ZizkaDB)
with a `parent_id` link, so a run becomes a **causal tree** — and one call, `db.why()`, walks that
tree back to the root cause.

> It doesn't only show *what* happened; it explains **why** it happened.

```
CrewAI run ──▶ ZizkaDBCrewLogger ──▶ ZizkaDB (causal store) ──▶ db.why() / dashboard
```

## Install

Both packages are published on PyPI — no git URLs needed:

```bash
pip install zizkadb-sdk zizkadb-crewai crewai
```

## Usage

`ZizkaDBCrewLogger` logs the crew lifecycle. Events auto-chain through the logger's `last_event_id`,
so you can omit `parent_id` to link each event to the previous one.

| Method | Event type | Purpose |
|---|---|---|
| `await log_kickoff(goal, **extra)` | `crew_kickoff` | Start of the run (root of the chain) |
| `await log_task(description, *, parent_id=None, **extra)` | `crew_task` | One node per task |
| `await log_output(output, *, parent_id=None, **extra)` | `crew_output` | Final result |

```python
from zizkadb import ZizkaDB
from zizkadb_crewai import ZizkaDBCrewLogger

# OSS-first: default to a local self-hosted instance; pass api_key only for remote/cloud.
async with ZizkaDB(host="http://localhost:8000") as db:
    logger = ZizkaDBCrewLogger(db=db, agent="research-crew")

    await logger.log_kickoff(goal="Research causal logging for AI agents")
    result = await crew.kickoff_async()
    for task_output in result.tasks_output:                    # one causal node per task
        await logger.log_task(
            description=task_output.description,
            output=str(task_output.raw)[:2000],
        )
    await logger.log_output(str(result))

    # The payoff — explain WHY the crew produced its output:
    (await db.why(logger.last_event_id)).print()
```

`db.why()` reconstructs the chain:

```
crew_kickoff → crew_task (Researcher) → crew_task (Writer) → crew_output
```

## Runnable example

A complete two-agent crew (Researcher → Writer) with the `why()` proof lives at
[`examples/crewai-agent/`](../../examples/crewai-agent/). It targets a self-hosted ZizkaDB
(`http://localhost:8000`) — no cloud signup required.

## Monorepo dev install

```bash
pip install -e sdk/python -e integrations/crewai
```

## Links

- PyPI: [`zizkadb-crewai`](https://pypi.org/project/zizkadb-crewai/) · [`zizkadb-sdk`](https://pypi.org/project/zizkadb-sdk/)
- Listed in CrewAI's community integrations: [`awesome-crewai`](https://github.com/crewAIInc/awesome-crewai)

## License

`zizkadb-crewai` and `zizkadb-sdk` are licensed under AGPL-3.0.
