# Maintainers

People with access to ZizkaDB's sensitive resources, what they are responsible for, and how access is granted.

## Members

| Name | GitHub | Role | Access to sensitive resources |
|------|--------|------|-------------------------------|
| Mir Arshad Ali Talpur | [@Zizka-ai](https://github.com/Zizka-ai) | Lead maintainer, security contact | GitHub organization owner (`ZIZKA-AI-SL`); repository admin; PyPI owner of `zizkadb-sdk`, `zizkadb-mcp`, `zizkadb-langchain`, `zizkadb-crewai`, `zizkadb-langgraph`, `zizkadb-livekit`; npm owner of `zizkadb-sdk`; GitHub Actions secrets; security mailbox `founder@zizka.ai` |

Container images on GHCR (`ghcr.io/zizka-ai-sl/*`) are published only by the `publish-images.yml` workflow, never from a personal machine.

## Roles and responsibilities

- **Lead maintainer:** sets direction and the roadmap, cuts releases, manages repository settings, secrets and package-registry ownership, and has the final say on disputes.
- **Security contact:** receives reports sent to `founder@zizka.ai`, triages them within the times in [SECURITY.md](SECURITY.md), coordinates fixes and publishes advisories.
- **Maintainer (write access):** reviews and approves pull requests, triages issues, and keeps CI green. Every change to `main` needs an approving review from a maintainer who is not its author.
- **Contributor:** anyone opening issues or pull requests; no access to sensitive resources.

## Granting escalated access

Before anyone receives write, maintain or admin access to the repository, or ownership of a package or secret:

1. **Track record.** They have contributed reviewed, merged pull requests over at least three months, or are employed by Zizka AI in an engineering role.
2. **Review.** The lead maintainer reviews their contribution history and identity, and confirms that two-factor authentication is on for their GitHub, PyPI and npm accounts.
3. **Least privilege.** They get the lowest role that covers their work (write before maintain, maintain before admin), and only the packages they release.
4. **Record.** They are added to the table above in a pull request, so the change is public.

Access is reviewed every six months and removed promptly when someone steps down or leaves; removal is also recorded here.
