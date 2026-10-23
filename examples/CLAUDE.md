# examples/ — Runnable reference agents

See root [`CLAUDE.md`](../CLAUDE.md), [`docs/ai/CODING_STANDARDS.md`](../docs/ai/CODING_STANDARDS.md), and [`docs/ai/ZIZKADB_MAPPINGS.md`](../docs/ai/ZIZKADB_MAPPINGS.md).

These are **end-user integration demos**, not core product code. Keep them minimal, copy-paste friendly, and aligned with published PyPI packages.

---

## Examples in this folder

| Directory | Package / pattern |
|-----------|-------------------|
| `minimal-python/` | `zizkadb-sdk` — `log` → `parent_id` → `why()` |
| `openai-agent/` | AsyncOpenAI + causal `parent_id` |
| `anthropic-agent/` | AsyncAnthropic + causal `parent_id` |
| `langchain-agent/` | `zizkadb-langchain` → `ZizkaDBCallbackHandler` |
| `crewai-agent/` | `zizkadb-crewai` → `ZizkaDBCrewLogger` |
| `livekit-agent/` | `zizkadb-livekit` → `ZizkaDBLiveKitObserver` |
| `mcp-cursor/` | `zizkadb-mcp` config for Cursor |

Fastest path for users: `zizkadb init my-agent --template <name>` (templates ship in the Python SDK).

---

## Import rules

Examples use **standalone integration package names**, not internal SDK paths:

```python
from zizkadb_langchain import ZizkaDBCallbackHandler   # correct
# NOT: from zizkadb.integrations.langchain import ...  # wrong in examples/
```

LiveKit: `from zizkadb_livekit import ZizkaDBLiveKitObserver`

---

## Each example should include

- `README.md` — runbook and expected dashboard outcome
- `.env.example` — no real secrets
- `requirements.txt` — pin or path to monorepo packages for local dev
- `agent.py` (or equivalent entrypoint)

---

## Local ZizkaDB stack

```bash
bash scripts/setup-local.sh
export ZIZKADB_HOST=http://localhost:8000
# Dev API key auto-accepted when ENV=development
```

Verify in dashboard: Activity → agent name → Sessions / Events.

---

## LiveKit voice (`livekit-agent/`)

- Observer hooks: `log_session_started`, `attach(session)`, `ingest_session_report` on session end.
- `session_id` should match the LiveKit room name (avoid double `call_` prefixes).
- Requires valid `OPENAI_API_KEY` when using OpenAI Realtime for voice.
- Integration guide: [`docs/integrations/livekit.md`](../docs/integrations/livekit.md).

---

## When editing examples

- Do not change core API contracts — examples consume public SDK/integration APIs only.
- If you add a new example, update [`examples/README.md`](README.md) and `.cursor/rules/sdk-integrations-mcp.mdc`.
- `@main` git URLs in `requirements.txt` are moving targets — prefer path installs for monorepo dev.
