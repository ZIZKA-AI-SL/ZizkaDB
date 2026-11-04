# Security documentation

| Document | Covers |
|----------|--------|
| [SECURITY.md](../../SECURITY.md) | Reporting vulnerabilities, supported versions and end of support, advisories, SCA and SAST remediation thresholds |
| [assessment.md](assessment.md) | Security assessment and threat model: actors, assets, attack surface, threats, residual risk |
| [release-verification.md](release-verification.md) | Release identifiers, image signatures, SBOM and provenance, verifying packages and their publisher |
| [secrets-policy.md](secrets-policy.md) | Storing, accessing and rotating project secrets |
| [MAINTAINERS.md](../../MAINTAINERS.md) | Who has access to sensitive resources, roles, and the policy for granting escalated access |
| [CONTRIBUTING.md](../../CONTRIBUTING.md) | DCO sign-off, test policy, when tests run, dependency selection |
| [`.vex/zizkadb.openvex.json`](../../.vex/zizkadb.openvex.json) | Vulnerabilities in dependencies that do not affect ZizkaDB (OpenVEX) |

## OpenSSF OSPS Baseline, Level 3: where each control is met

| Control | Evidence |
|---------|----------|
| AC-04.02 least-privilege CI jobs | Every workflow in `.github/workflows/` declares per-job or read-only permissions; `publish-images.yml` grants nothing at workflow level |
| BR-02.02 assets tied to the release | [release-verification.md § Release identifiers](release-verification.md#release-identifiers) |
| BR-07.02 secrets policy | [secrets-policy.md](secrets-policy.md) |
| DO-03.01 verify integrity | [release-verification.md](release-verification.md) |
| DO-03.02 verify publisher identity | `cosign verify` with the workflow identity; registry owners in MAINTAINERS.md |
| DO-04.01 support scope and duration | [SECURITY.md § Supported versions](../../SECURITY.md#supported-versions) |
| DO-05.01 end of security updates | [SECURITY.md § Supported versions](../../SECURITY.md#supported-versions) |
| GV-04.01 review before escalated access | [MAINTAINERS.md § Granting escalated access](../../MAINTAINERS.md#granting-escalated-access) |
| QA-02.02 SBOM with compiled assets | Images carry an SPDX SBOM attestation (`sbom: true` in `publish-images.yml`) |
| QA-04.02 subprojects | Not applicable: SDKs, MCP server, integrations and dashboard all live in this repository under the same CI and policies |
| QA-06.02 when and how tests run | [CONTRIBUTING.md § Testing expectations](../../CONTRIBUTING.md#testing-expectations) |
| QA-06.03 tests required for major changes | CONTRIBUTING.md "Tests are required" and the PR template |
| QA-07.01 non-author approval | Branch rule on `main`: one approving review required, the author cannot approve their own PR; CONTRIBUTING.md § Maintainer workflow |
| SA-03.02 threat model | [assessment.md](assessment.md) |
| VM-04.02 VEX | [`.vex/zizkadb.openvex.json`](../../.vex/zizkadb.openvex.json), referenced in SECURITY.md |
| VM-05.01 / 05.02 SCA policy | [SECURITY.md § Dependency and code scanning](../../SECURITY.md#dependency-and-code-scanning) |
| VM-05.03 automatic SCA gate | `dependency-review.yml` on every PR, a required check |
| VM-06.01 SAST policy | [SECURITY.md § Dependency and code scanning](../../SECURITY.md#dependency-and-code-scanning) |
| VM-06.02 automatic SAST gate | `codeql.yml` on every PR, with code scanning merge protection on `main` |

QA-07.01, VM-05.03 and VM-06.02 also depend on repository settings: see [docs/maintainers/github-settings.md](../maintainers/github-settings.md).
