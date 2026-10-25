#!/usr/bin/env bash
# Configure ZIZKA-AI-SL/ZizkaDB GitHub settings and replace the broken "Only admin" ruleset.
# Requires: gh CLI authenticated as a repo/org admin.
#
# Usage:
#   gh auth login   # as org owner
#   bash scripts/github-setup.sh
#
# Run AFTER .github/workflows/ci.yml is on main (so status check names exist).
set -euo pipefail

REPO="${ZIZKADB_GITHUB_REPO:-ZIZKA-AI-SL/ZizkaDB}"
OLD_RULESET_ID="${ZIZKADB_OLD_RULESET_ID:-18095491}"

echo "→ Repository: ${REPO}"
gh api "repos/${REPO}" -q '.full_name' >/dev/null

echo "→ Default branch: main"
gh api -X PATCH "repos/${REPO}" \
  -f default_branch=main \
  -F allow_squash_merge=true \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true

echo "→ Workflow permissions: read-only GITHUB_TOKEN"
gh api -X PUT "repos/${REPO}/actions/permissions" \
  -f enabled=true \
  -f allowed_actions=all \
  -f default_workflow_permissions=read

echo "→ Disable broken ruleset ${OLD_RULESET_ID} (Only admin)"
gh api -X PUT "repos/${REPO}/rulesets/${OLD_RULESET_ID}" \
  -f enforcement=disabled || echo "  (ruleset may already be disabled or removed)"

echo "→ Create light ruleset for main"
RULESET_JSON="$(cat <<'EOF'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": true,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": true
    }},
    {"type": "required_status_checks", "parameters": {
      "strict": true,
      "required_status_checks": [
        {"context": "python"},
        {"context": "typescript-sdk"},
        {"context": "dashboard"}
      ]
    }},
    {"type": "non_fast_forward"},
    {"type": "deletion"}
  ]
}
EOF
)"

gh api -X POST "repos/${REPO}/rulesets" --input - <<<"${RULESET_JSON}"

echo ""
echo "Done. Verify at: https://github.com/${REPO}/rules"
echo ""
echo "If required checks fail to match, open a test PR and copy exact check names from"
echo "the PR checks tab into the ruleset (may appear as 'CI / python' vs 'python')."
