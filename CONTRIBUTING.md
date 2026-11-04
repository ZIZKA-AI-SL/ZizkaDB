# Contributing to ZizkaDB

Thank you for your interest in ZizkaDB. We welcome bug reports, documentation improvements, SDK and integration work, dashboard UX, and core API changes — whether you are fixing a typo or designing a new primitive.

This guide explains how to get started, what we expect in pull requests, and the extra care required when changing a **multi-tenant operational database** (schema, auth, event storage, and drift baselines).

**Quick links**

| Resource | URL |
|----------|-----|
| Repository | https://github.com/ZIZKA-AI-SL/ZizkaDB |
| OSS quickstart (no clone) | `curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh \| bash` |
| Connect guide | [CONNECT.md](CONNECT.md) |
| Development setup | [DEVELOPMENT.md](DEVELOPMENT.md) |
| Docs | https://db.zizka.ai/docs |
| Architecture / trust | https://db.zizka.ai/trust |
| Wiki | https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki |
| Community board | https://db.zizka.ai/community |
| Issues | https://github.com/ZIZKA-AI-SL/ZizkaDB/issues |
| Security | [SECURITY.md](SECURITY.md) |

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before participating.

---

## Ways to contribute

You do **not** need a large PR to help.

| Type | Examples |
|------|----------|
| **Report bugs** | Events not appearing, drift score wrong, MCP tool failing — open a [GitHub issue](https://github.com/ZIZKA-AI-SL/ZizkaDB/issues) with repro steps |
| **Improve docs** | README, wiki, `dashboard/app/docs`, integration guides |
| **Fix issues** | Pick an open issue or ask on the [community board](https://db.zizka.ai/community) |
| **SDKs & MCP** | Python / TypeScript SDK, `zizkadb-mcp` tools |
| **Integrations** | LangChain, CrewAI (PyPI adapters); example patterns for other frameworks (`examples/`) |
| **Dashboard** | Agent views, drift UI, settings |
| **Core API** | Event pipeline, search, baselines, auth |
| **Examples** | Minimal demos under `examples/` |
| **Discuss design** | Open an issue or community post before large architectural changes |

If you are unsure where to start, say hello on the [community board](https://db.zizka.ai/community) or open a **“Looking for guidance”** issue.

---

## License and legal

ZizkaDB is open source with a split license:

| Component | License |
|-----------|---------|
| API, dashboard, Python SDK, TypeScript SDK | [AGPL-3.0](LICENSE) |
| MCP server (`mcp/`) | [MIT](mcp/LICENSE) |

By contributing code, you agree that your contributions are licensed under the same license as the files you modify. If you contribute to AGPL-covered code, downstream users who run a modified network-facing version must comply with AGPL obligations (source availability, etc.).

### Developer Certificate of Origin (sign-off required)

Every commit must be signed off. Signing off certifies the [Developer Certificate of Origin](https://developercertificate.org/): you wrote the change, or otherwise have the right to submit it under the project's license. Add the line with `git commit -s`:

```
Signed-off-by: Your Name <you@example.com>
```

The name and email must match the commit author. The **DCO** check fails a pull request with an unsigned commit; fix existing commits with `git rebase --signoff origin/main` and force-push. Bot commits (Dependabot, GitHub Actions) are exempt.

---

## Development setup

**Start here:** [DEVELOPMENT.md](DEVELOPMENT.md) — self-host quickstart, contributor workflow, test matrix, and port reference (dashboard **3001** via Docker, **3000** via `npm run dev`).

### Prerequisites

- **Docker** + Docker Compose v2
- **Python 3.10+** (API tests, SDK, demos)
- **Node.js 20+** (optional — only if you work on `dashboard/`)
- **Git**

### One-command local stack

```bash
git clone https://github.com/ZIZKA-AI-SL/ZizkaDB.git
cd ZizkaDB
bash scripts/setup-local.sh
```

| Service | URL |
|---------|-----|
| API health | http://localhost:8000/health |
| Swagger | http://localhost:8000/swagger |
| Dashboard | http://localhost:3001/login → **Open my dashboard →** |

Local dev uses `DEV_API_KEY=zizkadb_dev_local` (see `infra/.env`). The Python SDK auto-injects this key against `localhost:8000`.

### Hybrid local/Docker setup (Recommended for development)

For faster iteration, instant hot-reloading, and easier debugging, you can run the FastAPI backend and Next.js frontend code natively on your host machine while running database services (Postgres, Redis, Qdrant) inside Docker.

#### 1. Start database containers only
```bash
docker compose -f infra/docker-compose.yml up -d postgres qdrant redis
```
*Note: If you have a local PostgreSQL service running natively on your host on port `5432` (causing connection password errors), you can either stop the local service (e.g. `Stop-Service postgresql-x64-18` in PowerShell as Administrator) or change the mapped port to `"5433:5432"` in `infra/docker-compose.yml`.*

#### 2. Run the Backend API (`core/`)
Create a `core/.env` file:
```ini
DATABASE_URL=postgresql://zizkadb:zizkadb@localhost:5432/zizkadb
REDIS_URL=redis://localhost:6379
QDRANT_URL=http://localhost:6333
DEV_API_KEY=zizkadb_dev_local
ENV=development
```
*(Use port `5433` in `DATABASE_URL` if you changed the docker-compose mapping to resolve port conflicts).*

Install dependencies and start the local server:
```bash
cd core
python -m venv .venv
source .venv/bin/activate  # Or .venv/Scripts/activate on Windows
uv pip install -r requirements.txt -r requirements-dev.txt -e ../sdk/python
uvicorn main:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```
*(On Windows Git Bash, path slashes must be forward `/` to prevent escaping: `.venv/Scripts/uvicorn` instead of `.venv\Scripts\uvicorn`).*

#### 3. Run the Dashboard (`dashboard/`)
Create a `dashboard/.env` file:
```ini
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_DEV_MODE=true
```

Run the local Next.js server:
```bash
cd dashboard
npm install
npx next dev -p 3001
```

### Verify your environment

```bash
bash scripts/smoke-test.sh      # includes minimal-python example (SKIP_EXAMPLE=1 for curl-only)
bash scripts/smoke-example.sh   # example only: log → why()
python scripts/demo-why.py
bash scripts/check-doc-drift.sh
```

### Pre-commit (recommended for contributors)

Install hooks once — runs ruff and AI doc drift checks before each commit:

```bash
pip install -r core/requirements-dev.txt
pre-commit install
pre-commit run --all-files   # optional first-time check
```

See [docs/ai/README.md](docs/ai/README.md) for how AI coding standards are encoded in the repo.

### API unit & integration tests

```bash
cd core
pip install -r requirements.txt -r requirements-dev.txt
pip install -e ../sdk/python   # SDK tests import zizkadb

# Unit tests only (no Docker)
pytest -m "not integration"

# Full suite (Docker stack must be running)
pytest

# Real-Postgres tests (schema, init_db() migrations, tenant-scoped SQL).
# Skipped unless the variable is set; each test uses its own throwaway database.
docker run -d --rm --name zizkadb_pgtest -e POSTGRES_PASSWORD=test \
  -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16
until docker exec zizkadb_pgtest pg_isready -h 127.0.0.1 -U postgres -q; do sleep 1; done
ZIZKADB_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \
  pytest -m postgres
docker stop zizkadb_pgtest
```

### Dashboard (optional)

See [dashboard/README.md](dashboard/README.md) for the full dashboard dev guide.

| Mode | URL | When to use |
|------|-----|-------------|
| Docker Compose (`setup-local.sh`) | http://localhost:3001 | Default — full stack, matches production layout |
| `npm run dev` | http://localhost:3000 | UI-only work; set `NEXT_PUBLIC_API_URL=http://localhost:8000` |

Prefer the one-command stack (port **3001**):

```bash
bash scripts/setup-local.sh
# http://localhost:3001/login → Open my dashboard →
```

Or run the dashboard alone for UI work:

```bash
cd dashboard
npm install
export NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev    # http://localhost:3000
npm run lint
npm test
npm run build
```

### Reset local data (dev only)

```bash
bash scripts/reset-local-db.sh
```

**Never** run `docker compose down -v` on a server with real users — it wipes Postgres volumes.

---

## Repository layout

```
ZizkaDB/
├── core/              # FastAPI API, Postgres/Qdrant/Redis, auth, events, baselines
│   ├── api/           # HTTP routes
│   ├── db/            # schema.sql, connection pool, idempotent migrations
│   └── tests/
├── dashboard/         # Next.js app (db.zizka.ai UI) — see dashboard/README.md
├── sdk/python/        # PyPI: zizkadb-sdk
├── sdk/typescript/    # npm: zizkadb-sdk
├── mcp/               # PyPI: zizkadb-mcp (MIT)
├── integrations/      # LangChain, CrewAI, etc.
├── examples/          # Runnable sample agents
├── docs/ai/           # AI-assisted development (AGENTS.md companion)
├── infra/             # Docker Compose, deploy scripts
├── scripts/           # setup-local.sh, smoke-test.sh, demos
└── wiki/              # GitHub wiki source (mirrored on GitHub)
```

When changing behavior, update **all surfaces** that expose it: API route, OpenAPI/Swagger, SDK methods, MCP tools (if applicable), docs, and dashboard UI.

---

## AI-assisted development

You do **not** need to paste “follow best practices” into every AI chat. Standards are encoded in the repo:

| Read | Purpose |
|------|---------|
| [AGENTS.md](AGENTS.md) | Entry point for Cursor, Claude Code, Copilot, Windsurf, Gemini |
| [docs/ai/CODING_STANDARDS.md](docs/ai/CODING_STANDARDS.md) | Full team engineering standards (44 sections) |
| [docs/ai/ZIZKADB_MAPPINGS.md](docs/ai/ZIZKADB_MAPPINGS.md) | Repo-specific folder mapping |
| [docs/ai/README.md](docs/ai/README.md) | Full AI architecture map |
| [.cursor/rules/coding-standards.mdc](.cursor/rules/coding-standards.mdc) | ZizkaDB invariants (auto-loaded in Cursor) |
| [CLAUDE.md](CLAUDE.md) | Stack and module guides |

Rationale: [docs/adr/008-ai-coding-assistant-architecture.md](docs/adr/008-ai-coding-assistant-architecture.md).

When your change affects documented behavior, update the matching KB, ADR, or rule file in the **same PR**.

---

## Making a change

### 1. Open or link a GitHub issue

Every branch that becomes a PR should trace to an issue so work is trackable and **auto-closes on merge** when you use `Fixes #N` in the PR description.

| Change type | Issue | Label(s) |
|-------------|-------|----------|
| Bug fix | Required | `bug` |
| New feature / capability | Required | `enhancement` |
| Docs / DX / CI | Required | `documentation` (add `enhancement` if CI or tooling changes) |
| Security | Required | Report via [SECURITY.md](SECURITY.md) or `bug` |

```bash
gh issue create --title "Short description" --label documentation --body "Problem, solution, acceptance criteria"
```

For non-trivial work (new API, schema, auth), discuss in the issue before coding.

### 2. Discuss first (for large changes)

Open an issue or community post if your change:

- Adds a new API primitive or MCP tool
- Changes schema or event semantics
- Affects multi-tenant isolation or auth
- Removes or renames public SDK methods
- Changes default retention, billing, or AGPL-covered network behavior

### 3. Fork and branch

Include the issue number in the branch name when possible:

```bash
git checkout -b fix/179-dashboard-ci-vitest   # bug / fix
git checkout -b feat/180-short-description    # feature
git checkout -b docs/181-what-you-changed     # documentation only
```

### 4. Write focused commits

- One logical change per commit when possible
- Use clear messages: `fix(api): reject events without tenant scope`
- Sign off every commit: `git commit -s` (see [DCO](#developer-certificate-of-origin-sign-off-required))
- Put `Fixes #123` in the **PR description** (GitHub closes the issue on merge)

### 5. Open a pull request

Target the `main` branch on [ZIZKA-AI-SL/ZizkaDB](https://github.com/ZIZKA-AI-SL/ZizkaDB).

**The PR description must start with the linked issue** — first line: `Fixes #<number>` (or `Closes #<number>`). Optionally repeat the issue title on the next line for reviewers. GitHub closes the issue when the PR merges.

**PR description must also include:**

1. **What** changed and **why**
2. **How to test** (commands, curl, screenshots for UI)
3. **Areas touched** (API / schema / SDK / dashboard / MCP)
4. **Breaking changes** (if any)

**Tests are required.** New features and bug fixes must come with automated tests that cover them (a bug fix adds a test that fails without the fix). PRs without tests are accepted only for docs, comments and other changes with no runtime behaviour; say so in the PR.

**CI must pass** before merge (Python lint + tests, TS SDK tests, dashboard lint + vitest + build, doc drift).

Keep PRs small and reviewable. Split large work into stacked PRs when you can.

---

## Area-specific guidelines

### Core API (`core/`)

- Python 3.10+, async (`asyncpg`, FastAPI)
- All tenant data must be scoped by `tenant_id` from the authenticated API key or JWT — **never** trust client-supplied tenant IDs
- Events are **append-only** by default; `forget` / GDPR paths are explicit APIs
- New endpoints: add tests in `core/tests/`, document in Swagger, mirror in SDK/MCP if user-facing
- Log meaningful errors server-side; return safe messages to clients (no stack traces in production)

### Database & schema (critical)

ZizkaDB stores production agent history. Schema mistakes can cause data loss or downtime.

**New installs:** update `core/db/schema.sql` so fresh `docker compose up` gets the full schema.

**Existing deployments:** add **idempotent** migrations in `core/db/connection.py` (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded `DO $$` blocks). Migrations run on API startup.

Rules:

1. **Prefer additive changes** — new columns, new tables, new indexes
2. **Avoid destructive changes** (`DROP COLUMN`, mass `UPDATE` without filters) without maintainer approval and a documented upgrade path
3. **Test both paths:** empty DB (schema.sql) and upgraded DB (migrations in connection.py)
4. **Index consciously** — event tables grow fast; note expected query patterns in the PR
5. **Qdrant:** collection `agent_events` — coordinate embedding dimension changes with maintainers

### Python SDK (`sdk/python/`)

- Public surface: `ZizkaDB`, `log`, `why`, `at`, `search`, `baseline`, etc.
- Maintain backward compatibility for `zizkadb_live_*` and legacy `agdb_*` keys where documented
- Bump version in `sdk/python/pyproject.toml` only when maintainers cut a PyPI release
- Add or update tests under `core/tests/` that import `zizkadb`

### TypeScript SDK (`sdk/typescript/`)

- Keep parity with Python SDK for core methods where feasible
- Run `npm run build` in `sdk/typescript/` before PR

### MCP server (`mcp/`)

- MIT licensed — keep dependencies minimal
- New tools must map to real API endpoints and be documented in `mcp/README.md`
- Test with `uvx zizkadb-mcp` against local stack

### Dashboard (`dashboard/`)

- Next.js 14, mostly inline styles matching existing pages
- Use `NEXT_PUBLIC_API_URL` for API base; never embed secrets
- UI changes: include screenshot in PR when visual

### Integrations & examples

- Put reusable adapters in `integrations/`; runnable demos in `examples/`
- Each example needs a short README and `requirements.txt` (or `package.json`)
- Prefer `parent_id` / `session_id` in examples so `why()` and drift work out of the box

---

## Testing expectations

| Area | Minimum bar |
|------|-------------|
| Python lint | `ruff check core/ sdk/python/ mcp/ integrations/` |
| Core API | `pytest core/tests/ -m "not integration" -v` |
| Python SDK | `pytest sdk/python/tests/ -v` |
| MCP | `pytest mcp/tests/ -v` |
| TypeScript SDK | `cd sdk/typescript && npm test` |
| Dashboard | `cd dashboard && npm run lint && npm test && npm run build` |
| Doc / router drift | `bash scripts/check-doc-drift.sh` |
| Docs only | Link check; no tests required |
| Full stack | `bash scripts/smoke-test.sh` |

**CI runs the Python + dashboard gates on every PR.** Match the row for the area you changed. `pre-commit install` is optional locally.

When each automated check runs (all in `.github/workflows/`):

| Workflow | Runs on | What it checks |
|----------|---------|----------------|
| `ci.yml` | every PR and every push to `main` | ruff, doc drift, core unit tests, real-Postgres tests, Python SDK, MCP, TypeScript SDK, dashboard lint + tests + build |
| `golden-path.yml` | every PR and push to `main` | the documented quickstart end to end |
| `codeql.yml` | every PR, push to `main`, weekly | static analysis (SAST) of Python, JS/TS and workflows |
| `dependency-review.yml` | every PR | new dependencies with known vulnerabilities, malware or blocked licences |
| `dco.yml` | every PR | `Signed-off-by` on every commit |
| `integration.yml` | weekly and on demand | full Docker stack integration tests |

A PR merges only when the required checks pass and another maintainer has approved it.

```bash
bash scripts/smoke-test.sh
cd core && pytest -m "not integration"
cd core && pytest    # with Docker up
```

Mark slow or Docker-dependent tests with `@pytest.mark.integration`. Mark tests that need real SQL (not a mocked pool) with `@pytest.mark.postgres` and use the `pg_db` fixture from `core/tests/conftest.py`.

---

## Dependencies

How dependencies are chosen, obtained and tracked:

- **Choosing.** Prefer the standard library or a dependency the project already uses. A new dependency must be actively maintained, widely used, under a licence compatible with AGPL-3.0 (MIT for `mcp/`), and justified in the PR description. Never add a package to save a few lines.
- **Obtaining.** Only from the official registries (PyPI, npm, Docker Hub / GHCR official images) over HTTPS, through the standard tooling: `pip` with `requirements*.txt` / `pyproject.toml`, `npm` with a committed `package-lock.json` (CI installs with `npm ci`), and pinned base images. GitHub Actions are pinned to a full commit SHA.
- **Tracking.** Direct dependencies are declared in those manifest files. Dependabot (`.github/dependabot.yml`) proposes updates weekly and security fixes as they appear; the **Dependency review** check blocks a PR that adds a vulnerable or malicious package. Thresholds: [SECURITY.md](SECURITY.md#dependency-and-code-scanning).

---

## Documentation

Update docs in the same PR as code when behavior changes:

- `README.md` — quick start, high-level behavior
- `dashboard/app/docs/sections.tsx` — user-facing docs on db.zizka.ai
- `wiki/` — deployment, MCP, troubleshooting
- Package READMEs: `sdk/python/README.md`, `mcp/README.md`, etc.

Use real commands and copy-pasteable snippets. Prefer `zizkadb_dev_local` / `zizkadb_live_*` naming (not legacy `agdb_*` in new docs).

---

## Security

Do **not** open public issues for vulnerabilities.

See [SECURITY.md](SECURITY.md) for responsible disclosure. Security fixes take priority over feature work.

Especially sensitive areas:

- API key hashing and validation
- JWT / OTP auth
- Cross-tenant data leaks
- SQL injection, SSRF in dashboard proxies
- Embedding API key storage (`embedding_api_key_encrypted`)

---

## Code style

We value consistency with surrounding code over strict formatting rules.

- **Python:** follow existing patterns in `core/`; type hints where already used
- **TypeScript:** match `dashboard/` conventions; run `npm run lint`
- **No unrelated refactors** in the same PR as a feature or fix
- **Comments:** only for non-obvious business logic (tenant rules, drift math, migration rationale)

---

## Maintainer workflow

Maintainers will:

1. Review for correctness, security, and tenant isolation; every change to `main` needs an approving review from a maintainer who is not its author (see [MAINTAINERS.md](MAINTAINERS.md))
2. Run CI / smoke tests
3. Squash-merge or merge with clean history
4. Cut releases for PyPI (`zizkadb-sdk`, `zizkadb-mcp`) and npm (`zizkadb-sdk`) on their own schedule

You do not need permission to open a PR. You **do** need alignment for large design changes — talk to us first.

---

## Recognition

Contributors are credited in release notes and, with permission, on the project site. Thank you for helping build operational infrastructure for AI agents.

**Questions?** [Community board](https://db.zizka.ai/community) · [GitHub Discussions/Issues](https://github.com/ZIZKA-AI-SL/ZizkaDB/issues) · founder@zizka.ai
