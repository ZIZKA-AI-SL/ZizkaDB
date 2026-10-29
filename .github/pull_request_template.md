## Related issue

<!-- REQUIRED — put this at the top of every PR description -->

Fixes #<!-- number -->

Link the GitHub issue this PR completes. Use **`Fixes #123`** (or `Closes #123`) so GitHub auto-closes the issue on merge. Use `Related to #123` only if the issue must stay open.

Issue: <!-- paste issue title for reviewers, e.g. "Run dashboard vitest in CI (#179)" -->

---

## Summary

<!-- What changed? (CODING_STANDARDS §39) -->

## Why

<!-- Why was this necessary? -->

## Implementation

<!-- How was it implemented? Keep it brief. -->

## Test plan

CI runs three jobs on every PR: **python**, **typescript-sdk**, **dashboard**. Check the boxes that match files you changed.

- [ ] New features and bug fixes include automated tests (required; see CONTRIBUTING.md). Docs-only: say so here.

### python (`core/`, `sdk/python/`, `mcp/`, `integrations/`)

- [ ] `ruff check core/ sdk/python/ mcp/ integrations/`
- [ ] `bash scripts/check-doc-drift.sh`
- [ ] `pytest core/tests/ -m "not integration" -v`
- [ ] `pytest sdk/python/tests/ -v` (if Python SDK touched)
- [ ] `pytest mcp/tests/ -v` (if MCP touched)

### typescript-sdk (`sdk/typescript/`)

- [ ] `cd sdk/typescript && npm test`

### dashboard (`dashboard/`)

- [ ] `cd dashboard && npm run lint && npm test && npm run build`

### Manual / optional

- [ ] `bash scripts/smoke-test.sh` (local stack up)
- [ ] Other: <!-- docs-only, scripts-only, etc. -->

## Edge cases

<!-- Empty, error, loading, auth, concurrency — §20 -->

## Security

<!-- Secrets, auth, input validation — §23–24; "N/A" for docs-only -->

## Performance

<!-- DB queries, renders, cache — §19; "N/A" if not relevant -->

## Documentation

<!-- KB, ADR, CODING_STANDARDS, lib/api.ts — §34 -->

## Breaking changes

<!-- None, or describe migration — §31 -->
