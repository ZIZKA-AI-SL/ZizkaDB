# Security Policy

ZizkaDB is an operational database for AI agents. It stores event history, API keys, user accounts, and embeddings metadata. We take security reports seriously.

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` branch (latest) | Yes |
| Latest [GitHub release](https://github.com/ZIZKA-AI-SL/ZizkaDB/releases) | Yes |
| Older releases | Best effort — upgrade recommended |

Managed cloud at **db.zizka.ai** runs the latest stable deployment from this repository.

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

## Contact

**founder@zizka.ai** — security reports and code of conduct enforcement
