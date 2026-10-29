# Secrets and credentials policy

How the project stores, uses and rotates the secrets it controls: CI tokens, package-registry credentials and signing.

## Rules

1. **Never in version control.** No secret, token, password or private key is committed, not even in examples or tests. Examples use placeholders (`zizkadb_live_...`, `change-me`). `.env` files are git-ignored; only `.env.example` is committed. A secret that reaches git, an issue, a PR, a log or a chat is treated as leaked and rotated, even if it was deleted right away.
2. **Prefer no secret at all.** CI uses the short-lived `GITHUB_TOKEN` and OIDC (keyless image signing) instead of stored credentials wherever possible.
3. **Where a stored secret is needed,** it lives only in GitHub Actions secrets (encrypted, masked in logs) or in the maintainer's password manager. It is never pasted into workflow files, scripts, chat tools or AI assistants.
4. **Least privilege.** Each token is scoped to one purpose (for example, one PyPI project) with the shortest practical lifetime. Workflows declare only the permissions each job needs.
5. **Two-factor authentication** is required on every account that can reach a secret: GitHub (enforced for the organization), PyPI and npm.

## Who can access

Only the people listed in [MAINTAINERS.md](../../MAINTAINERS.md) with access to "GitHub Actions secrets" or a package registry. Access is granted and removed as described there.

## Rotation

| Credential | Rotation |
|------------|----------|
| PyPI / npm API tokens | At least every 12 months, and immediately when a maintainer leaves or a leak is suspected |
| GitHub personal access tokens (maintainers) | Fine-grained, expiry of 90 days or less |
| GitHub Actions secrets | When the underlying credential rotates; reviewed every 6 months with access |
| `GITHUB_TOKEN`, OIDC signing identities | Issued per workflow run; nothing to rotate |

## If a secret leaks

1. Revoke or rotate it at the provider first.
2. Check the provider's audit log for use between the leak and the revocation.
3. Remove it from the place it leaked (rewrite history only if it was committed).
4. If users could be affected, publish an advisory as described in [SECURITY.md](../../SECURITY.md).

Self-hosters' own secrets (`JWT_SECRET`, database passwords, API keys) are generated per installation and never shared with the project; see [SECURITY.md](../../SECURITY.md#production-hygiene-self-hosters).
