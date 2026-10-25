# GitHub repository settings (maintainers)

Admin-only steps for **ZIZKA-AI-SL/ZizkaDB**. Contributors cannot change rulesets or repo settings.

## Prerequisites

- [GitHub CLI](https://cli.github.com/) (`gh`) installed
- Repo **admin** access (org owner `ZIZKA-AI-SL` or delegated admin)
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml) merged to `main` first

## Quick setup (recommended)

```bash
gh auth login   # as org owner / repo admin
bash scripts/github-setup.sh
```

## Manual checklist

### 1. Disable the broken ruleset

The ruleset **"Only admin"** (id `18095491`) blocks all pushes/merges with GH013:

- Requires Code Scanning (CodeQL not configured)
- Requires signed commits (GPG friction for contributors)

**Action:** https://github.com/ZIZKA-AI-SL/ZizkaDB/rules/18095491 → set enforcement to **Disabled**

Do this **before** merging the first CI PR if merges are blocked.

### 2. Repository settings

| Setting | Value |
|---------|-------|
| Default branch | `main` |
| Merge button | **Squash merge only** |
| Auto-delete head branches | **On** |
| Actions → Workflow permissions | **Read repository contents** |

Path: https://github.com/ZIZKA-AI-SL/ZizkaDB/settings

### 3. Create light ruleset for `main`

Create a new ruleset (or let `github-setup.sh` create **main-protection**):

| Rule | Setting |
|------|---------|
| Target | Default branch (`main`) |
| Pull request | Required, **1** approval |
| Dismiss stale approvals | On |
| Require code owner review | **Off** (early stage) |
| Required status checks | `python`, `typescript-sdk`, `dashboard` |
| Require branches up to date | On |
| Block force pushes | On |
| Block deletions | On |
| Require conversation resolution | On |

**Do NOT enable (Phase 2+):**

- Required signed commits
- Code Scanning merge gate
- Code quality merge gate
- Linear history
- Tag protection (`v*`)

### 4. Verify status check names

After the first CI run on a PR, open the **Checks** tab. If names differ (e.g. `CI / python` instead of `python`), update the ruleset to match **exactly**.

### 5. Optional (enable later)

- **Dependabot alerts** — Settings → Code security → Dependabot
- **Secret scanning** — org/repo security settings
- **CodeQL** — after [.github/workflows/codeql.yml](../../.github/workflows/codeql.yml) is stable (non-blocking for 2+ weeks first)

---

## Message template for admin

Copy and send to the org owner:

```
Hi — we need GitHub admin help on ZIZKA-AI-SL/ZizkaDB so contributors can merge PRs.

1. DISABLE ruleset "Only admin" (id 18095491):
   https://github.com/ZIZKA-AI-SL/ZizkaDB/rules/18095491
   (It requires Code Scanning we don't run yet — every push fails with GH013.)

2. After our CI workflow is on main, run:
   bash scripts/github-setup.sh
   Or follow docs/maintainers/github-settings.md

New policy: PR + 1 approval + CI checks (python, typescript-sdk, dashboard).
No signed commits, no Code Scanning merge gate.
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `GH013: Cannot update protected ref` | Disable ruleset 18095491 |
| `Waiting for Code Scanning results` | Remove code scanning from ruleset |
| `gpg: signing failed` | Remove required signed commits from ruleset |
| PR blocked but CI green | Status check names in ruleset don't match Checks tab |
| Skipped CI jobs block merge | Don't use path filters on required jobs until optional checks exist |
