# CrewAI + ZizkaDB starter

A minimal two-agent crew (Researcher → Writer) logged to ZizkaDB with causal
`parent_id` lineage. After the run, `db.why()` reconstructs **why** the crew
produced its output — not just what it produced.

## Run

```bash
# 1. Start a self-hosted ZizkaDB (no cloud signup needed)
bash ../../scripts/setup-local.sh        # API on http://localhost:8000

# 2. Configure and run
cp .env.example .env                      # add your OPENAI_API_KEY
pip install -r requirements.txt
python agent.py
```

The example is **OSS-first**: with neither variable set it uses the local stack at
`http://localhost:8000`. For a self-hosted server set both `ZIZKADB_HOST` and
`ZIZKADB_API_KEY`; for ZizkaDB Cloud set only `ZIZKADB_API_KEY`.

## What you'll see

After the crew finishes, `db.why()` prints the causal chain:

```
=== ZizkaDB — why did this output happen? ===
crew_kickoff: {'goal': 'Explain why AI agents need causal event logging.'}
    └── crew_task: Researcher — three factual bullet points
        └── crew_task: Writer — polished three-sentence summary
            └── crew_output: <final answer>
```

The same timeline is visible on the dashboard at http://localhost:3001.

## How it works

`ZizkaDBCrewLogger` logs three event types, auto-chained through `parent_id`:

| Method | Event type |
|---|---|
| `log_kickoff(goal)` | `crew_kickoff` (root) |
| `log_task(description, output)` | `crew_task` (one per task) |
| `log_output(result)` | `crew_output` |

See the adapter docs at [`integrations/crewai/`](../../integrations/crewai/).

## Monorepo dev install

```bash
pip install -e ../../sdk/python -e ../../integrations/crewai   # instead of requirements.txt
```
