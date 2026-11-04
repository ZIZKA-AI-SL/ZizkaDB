# Security Policy

ZizkaDB is an operational database for AI agents. It stores event history, API keys, user accounts, and embeddings metadata. We take security reports seriously.

## Supported versions

| Version | Security fixes |
|---------|----------------|
| `main` branch | Yes |
| Latest minor release line (currently **0.5.x**) | Yes, as patch releases |
| Older release lines | No |

**Scope and duration.** Each minor release line (0.5.x, 0.6.x, ...) receives bug and security fixes until the next minor release ships. On that day the previous line **stops receiving security updates**; upgrade to the latest minor release. This applies to every published artifact: the API and dashboard images, the single-container image, `zizkadb-sdk` (PyPI and npm), `zizkadb-mcp` and the integration packages.

ZizkaDB Cloud (**db.zizka.ai**) is a separate managed product that is not built from this repository. Report issues in it to the same address below.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Email **founder@zizka.ai** with subject line `ZizkaDB Security` and include:

1. Description of the issue and potential impact
2. Steps to reproduce (proof-of-concept if available)
3. Affected component (API, dashboard, SDK, MCP, self-host Docker stack)
4. Your environment (self-hosted vs managed, version/commit if known)
5. Whether you would like public credit (name/handle) when fixed

We aim to acknowledge reports within **3 business days** and provide a status update within **7 business days**.

## What to report

Examples of in-scope issues:

- Cross-tenant data access (viewing another user's events, agents, or API keys)
- Authentication bypass (OTP, JWT, dev key misuse in production)
- API key leakage or weak hashing
- SQL injection or unsafe query construction
- Remote code execution in API, dashboard, or MCP server
- SSRF via dashboard or API proxies
- Privilege escalation (user → admin, tenant → tenant)
- Insecure defaults on production deployments (`DEV_API_KEY` exposed, open admin routes)

Out of scope (please use regular [issues](https://github.com/ZIZKA-AI-SL/ZizkaDB/issues) instead):

- Social engineering
- Denial of service without a practical exploit path
- Issues in third-party services (Stripe, email providers) outside our control
- Missing security headers with no demonstrated exploit
- Vulnerabilities in dependencies already fixed in `main`

## Safe harbor

We will not pursue legal action against researchers who:

- Make a good-faith effort to avoid privacy violations and service disruption
- Do not access or modify data belonging to other users
- Report findings promptly and allow reasonable time to fix before public disclosure

## Vulnerability data and advisories

- Every fixed vulnerability is published as a [GitHub Security Advisory](https://github.com/ZIZKA-AI-SL/ZizkaDB/security/advisories) (with a CVE when it qualifies) and noted in the [CHANGELOG](CHANGELOG.md) entry of the release that fixes it.
- Vulnerabilities reported in our dependencies that **do not affect** ZizkaDB are recorded, with the reason, in the OpenVEX document [`.vex/zizkadb.openvex.json`](.vex/zizkadb.openvex.json).

## Dependency and code scanning

**Software composition analysis (SCA).** Dependabot alerts and the *Dependency review* check (every pull request) cover known vulnerabilities, malicious packages and licences.

| Finding | Remediation threshold |
|---------|-----------------------|
| Critical or high vulnerability | Fixed within 14 days |
| Moderate vulnerability | Fixed within 60 days |
| Low vulnerability | Next routine dependency update |
| Known-malicious package | Removed immediately |
| Licence incompatible with AGPL-3.0 (or MIT for `mcp/`) | Not merged |

- A pull request that adds a dependency with a moderate-or-higher vulnerability, malware, or a blocked licence **cannot merge** unless a maintainer records why it is not exploitable (in the PR and in the VEX document).
- **No release ships with an open moderate-or-higher SCA finding** unless it is recorded in the VEX document as not affecting ZizkaDB.

**Static analysis (SAST).** CodeQL scans every pull request, every push to `main` and weekly.

| Finding | Remediation threshold |
|---------|-----------------------|
| Error / high or critical security severity | Blocks the merge; fixed before merging |
| Warning / medium | Fixed within 60 days |
| Note / low | Fixed when the code is next touched |

A finding can be dismissed only as a false positive or not exploitable, with the reason written in the dismissal.

## Disclosure timeline

- We prefer coordinated disclosure
- We will work with you on a reasonable embargo (typically 90 days or until a fix is released)
- After a fix, we may publish a security advisory and credit reporters who opt in

## Production hygiene (self-hosters)

If you run ZizkaDB yourself:

- Set `ENV=production` and **unset** `DEV_API_KEY`
- Configure `EMAIL_*` for OTP; use TLS in front of the API and dashboard
- **Never** run `docker compose down -v` on servers with real data
- Back up Postgres regularly (`infra/backup-postgres.sh`)
- Keep Docker images and dependencies updated

See [Production Deployment wiki](https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki/Production-Deployment).

## More security documentation

Threat model, release verification (signatures, SBOM), secrets policy and maintainers: [docs/security/](docs/security/README.md).

## Contact

**founder@zizka.ai** — security reports and code of conduct enforcement
