#!/usr/bin/env python3
"""Write the shields.io endpoint JSON for the README "downloads" badge.

Adds the all-time download counts of every published package, using the
same sources as the per-package badges under it in the README:

  - PyPI: pepy.tech (what img.shields.io/pepy/dt shows)
  - npm:  api.npmjs.org (what img.shields.io/npm/d18m shows)
  - ClawHub: clawhub.ai/api/v1/packages (what the README's ClawHub badge shows)

It also writes one badge JSON per package next to the total
(downloads-<registry>-<package>.json). The README's per-package badges read
those files instead of asking pepy and npm live through shields.io, which
fails with "rate limited by upstream service" or "package not found or too
new". So the total always equals the sum of the badges it expands to. If any
source fails, the script exits non-zero and writes nothing, so a bad run
never replaces a good total with a low one. The one exception: npm answers
"not found" for a package until its first download stats exist (a day or
so after the first release), which counts as 0.

Usage: python scripts/update_download_badge.py <output-dir>/downloads.json
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

PYPI_PACKAGES = ("zizkadb-sdk", "zizkadb-mcp", "zizkadb-langchain", "zizkadb-crewai")
NPM_PACKAGES = ("zizkadb-sdk", "zizkadb-openclaw")
CLAWHUB_PACKAGES = ("@zizka-ai/zizkadb",)  # the OpenClaw plugin, installed from ClawHub
# npm serves at most 18 months per request; our npm packages are younger than that.
NPM_FIRST_DAY = "2025-05-01"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "zizkadb-download-badge"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pypi_total(package: str) -> int:
    return int(_get_json(f"https://pepy.tech/api/v2/projects/{package}")["total_downloads"])


def npm_total(package: str) -> int:
    period = f"{NPM_FIRST_DAY}:{date.today().isoformat()}"
    try:
        return int(_get_json(f"https://api.npmjs.org/downloads/point/{period}/{package}")["downloads"])
    except urllib.error.HTTPError as err:
        # A package too new to have stats yet: {"error": "package <name> not found"}
        if err.code == 404 and b"not found" in err.read():
            return 0
        raise


def clawhub_total(package: str) -> int:
    url = f"https://clawhub.ai/api/v1/packages/{urllib.parse.quote(package, safe='')}"
    return int(_get_json(url)["package"]["stats"]["downloads"])


def short(n: int) -> str:
    """Format like shields.io: 950, 5.8k, 20.8k, 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def badge(n: int, color: str = "blue") -> dict:
    return {"schemaVersion": 1, "label": "downloads", "message": short(n), "color": color}


def slug(name: str) -> str:
    """npm/zizkadb-sdk -> npm-zizkadb-sdk, clawhub/@zizka-ai/zizkadb -> clawhub-zizka-ai-zizkadb."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def write(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f)
        f.write("\n")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    counts = {f"pypi/{p}": pypi_total(p) for p in PYPI_PACKAGES}
    counts.update({f"npm/{p}": npm_total(p) for p in NPM_PACKAGES})
    counts.update({f"clawhub/{p}": clawhub_total(p) for p in CLAWHUB_PACKAGES})
    total = sum(counts.values())
    for name, n in counts.items():
        print(f"{name}: {n}")
    print(f"total: {total}")
    out_dir = os.path.dirname(os.path.abspath(sys.argv[1]))
    for name, n in counts.items():
        write(os.path.join(out_dir, f"downloads-{slug(name)}.json"), badge(n, "green"))
    write(sys.argv[1], badge(total))


if __name__ == "__main__":
    main()
