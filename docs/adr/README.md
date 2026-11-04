# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for ZizkaDB — lightweight documents that explain **why** a key design decision was made, not just what it is.

ADRs are the highest-leverage documentation for long-term maintainability. They prevent contributors (and AI coding tools) from "fixing" things that are working as designed, and give future engineers the context to make informed changes.

## Index

| ADR | Decision |
|---|---|
| [001](001-causal-lineage-parent-event-id.md) | `parent_event_id` on the `events` table for causal lineage |
| [002](002-dual-write-postgres-qdrant.md) | Dual-write: events go to both Postgres and Qdrant |
| [003](003-billing-stub.md) | Billing is intentionally stubbed — always `has_access: True` |
| [004](004-auth-dependency-split.md) | `get_tenant` vs `require_dashboard_session` auth dependency split |
| [005](005-in-process-rate-limiting.md) | In-process Python dict rate limiting |
| [006](006-token-usage-jsonb-convention.md) | Token usage stored in `events.data.token_usage` JSONB |
| [007](007-deterministic-token-optimization.md) | Deterministic (no-LLM) token optimization detectors |
| [008](008-ai-coding-assistant-architecture.md) | Repo-encoded AI coding standards (layers + tool adapters) |
| [009](009-audit-hash-chain.md) | Audit hash chain: the per-agent chain from #310 and its hardening plan |

## Format

Each ADR has:
- **Status**: Accepted / Superseded / Deprecated
- **Context**: The problem being solved and constraints at the time
- **Decision**: What was decided
- **Consequences**: What becomes easier and harder as a result
- **Alternatives considered**: What else was evaluated

## Adding a new ADR

1. Copy an existing ADR as a template
2. Number it sequentially (`010-...` after ADR-009)
3. Add it to the index above
4. Keep it short — one page is ideal

ADRs are written after a decision is made, not as proposals.
