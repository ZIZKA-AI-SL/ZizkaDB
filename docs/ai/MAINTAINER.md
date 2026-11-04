# Maintainer workflow (ZizkaDB core team)

**External contributors:** use [CONTRIBUTING.md](../../CONTRIBUTING.md) and [AGENTS.md](../../AGENTS.md) only. You do not need assignee, reviewer, or footer rules below.

---

## Branch hygiene (maintainers)

Before starting work when multiple people land on `main`:

```bash
git fetch origin main
git checkout -B main origin/main
git checkout -b <topic>
```

Never `git push` to `main`. Never `gh pr merge` from automation — humans merge after review.

---

## GitHub PR metadata (`gh pr create`)

Set sidebar fields in the **same** `gh pr create` when you are a maintainer with repo permissions:

```bash
gh pr create \
  --label enhancement \
  --assignee saadamjad \
  --reviewer ZIZKA-AI-SL \
  --body "..."
```

- Labels: `enhancement`, `bug`, and/or `documentation` (add `documentation` when KB/ADR/`CLAUDE.md` changed). Do **not** use `good first issue` on PRs.
- PR description **must start with** `Fixes #N` or `Closes #N` (issue title on the next line helps reviewers). Use `Related to #N` only when the issue must stay open.
- Optional footer for team PRs: **Made with [Cursor](https://cursor.com) and Saad**

Fork contributors: a normal PR title + description + test plan is enough. Maintainers will assign reviewers.

---

## GitHub issues (CODING_STANDARDS §37–38)

| Work type | Issue required? |
|-----------|-----------------|
| Non-trivial feature, API, schema, auth | Yes — open or link before PR |
| Typos, obvious bugs, docs-only | Optional |
| Maintainer releases | Yes — track in GitHub |

Apply labels: `enhancement`, `bug`, `documentation`, `security`, etc. (not `good first issue` on PRs).

PR descriptions should follow §39 in [CODING_STANDARDS.md](CODING_STANDARDS.md) and [.github/pull_request_template.md](../../.github/pull_request_template.md).
