# Verifying ZizkaDB releases

How to check that a release asset is the one the project published, unchanged, and who or what published it.

## Release identifiers

Every official release has one version (for example `0.5.1`), the git tag `v0.5.1`, and a [GitHub release](https://github.com/ZIZKA-AI-SL/ZizkaDB/releases) with its CHANGELOG entry. Every asset carries that version:

| Asset | Identifier |
|-------|------------|
| Container images `ghcr.io/zizka-ai-sl/zizkadb`, `zizkadb-api`, `zizkadb-dashboard` | Tag `:0.5.1` plus an immutable digest (`@sha256:...`) |
| Python packages on PyPI (`zizkadb-sdk`, `zizkadb-mcp`, integrations) | Version in the file name, e.g. `zizkadb_sdk-0.5.1-py3-none-any.whl` |
| TypeScript SDK on npm (`zizkadb-sdk`) | `zizkadb-sdk@0.5.1` |

`latest` is a moving tag. Pin a version or, better, a digest in production.

## Container images

Images are built by [`.github/workflows/publish-images.yml`](../../.github/workflows/publish-images.yml) on a `v*` tag. Each image is:

- **signed** with [Sigstore cosign](https://docs.sigstore.dev/) keyless signing, bound to the workflow's GitHub identity (no long-lived key exists to steal);
- shipped with an **SBOM** (SPDX) and a **SLSA provenance** attestation recording the source commit and build.

These apply to releases published after this workflow change; 0.5.0 and older images are not signed.

**Check the signature and the publisher identity:**

```bash
cosign verify ghcr.io/zizka-ai-sl/zizkadb:0.5.1 \
  --certificate-identity-regexp '^https://github\.com/ZIZKA-AI-SL/ZizkaDB/\.github/workflows/publish-images\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

It succeeds only if the image was signed by that workflow, in this repository, running for a release tag. Any other signer, or a modified image, fails.

**Read the SBOM and provenance:**

```bash
docker buildx imagetools inspect ghcr.io/zizka-ai-sl/zizkadb:0.5.1 --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/zizka-ai-sl/zizkadb:0.5.1 --format '{{ json .Provenance }}'
```

## Python packages (PyPI)

PyPI serves every file over HTTPS with its SHA-256 hash. To pin and verify:

```bash
pip download zizkadb-sdk==0.5.1 --no-deps -d dist/
sha256sum dist/*        # compare with the hashes on https://pypi.org/project/zizkadb-sdk/0.5.1/#files
pip install --require-hashes -r requirements.txt   # with "zizkadb-sdk==0.5.1 --hash=sha256:..."
```

**Publisher identity:** the packages are uploaded by the PyPI project owners listed in [MAINTAINERS.md](../../MAINTAINERS.md); the project pages link back to this repository.

## TypeScript SDK (npm)

npm signs every package version with the registry key. Verify the signatures of everything you installed:

```bash
npm install zizkadb-sdk@0.5.1
npm audit signatures
```

**Publisher identity:** the package is published by the npm owners listed in [MAINTAINERS.md](../../MAINTAINERS.md).

## Planned

Publishing the PyPI and npm packages from GitHub Actions with trusted publishing, so they also carry build provenance (PyPI attestations, `npm publish --provenance`) linking each file to its source commit.
