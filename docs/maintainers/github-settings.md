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
| Bypass list | **Empty** (admins follow the rules too) |
| Pull request | Required, **1** approval (the author cannot approve their own PR; needs a second maintainer) |
| Dismiss stale approvals | On |
| Require approval of the most recent push | On |
| Require code owner review | Off |
| Required status checks | `python`, `python-postgres`, `typescript-sdk`, `dashboard`, `dependency-review`, `dco`, `Analyze (python)`, `Analyze (javascript-typescript)`, `Analyze (actions)` |
| Require branches up to date | On |
| Require code scanning results | **CodeQL**: alerts **Errors**, security alerts **High or higher** |
| Block force pushes | On |
| Block deletions | On |
| Require conversation resolution | On |

These settings meet OpenSSF OSPS Baseline controls AC-03.01/02 (no direct commits or deletion of `main`), QA-03.01 (checks must pass), QA-07.01 (non-author approval), VM-05.03 (dependency gate) and VM-06.02 (SAST gate). See [docs/security/README.md](../security/README.md).

Also, at the **organization** level: Settings → Authentication security → **Require two-factor authentication**; Member privileges → Base permissions **Read**; Actions → General → Workflow permissions **Read repository contents** and **Allow GitHub Actions to create pull requests** (the contributors workflow opens a PR).

**Not enabled:** required signed commits (the DCO check covers contributor sign-off), linear history.

### 4. Verify status check names

After the first CI run on a PR, open the **Checks** tab. If names differ (e.g. `CI / python` instead of `python`), update the ruleset to match **exactly**.

### 5. Security features (turn on)

- **Dependabot alerts and security updates:** Settings → Code security → Dependabot (on)
- **Secret scanning and push protection:** Settings → Code security (on)
- **Private vulnerability reporting:** Settings → Code security (on)
- **Tag ruleset for `v*`:** only maintainers can create, update or delete release tags

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
| `Waiting for Code Scanning results` | CodeQL has not finished on the PR's latest commit; wait for `codeql.yml`, or re-run it |
| `gpg: signing failed` | Remove required signed commits from ruleset |
| PR blocked but CI green | Status check names in ruleset don't match Checks tab |
| Skipped CI jobs block merge | Don't use path filters on required jobs until optional checks exist |
