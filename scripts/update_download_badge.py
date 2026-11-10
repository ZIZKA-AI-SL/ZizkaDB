#!/usr/bin/env python3
"""Write the shields.io endpoint JSON for the README "downloads" badge.

Adds the all-time download counts of every published package, using the
same sources as the per-package badges under it in the README:

  - PyPI: pepy.tech (what img.shields.io/pepy/dt shows)
  - npm:  api.npmjs.org (what img.shields.io/npm/d18m shows)

So the total always equals the sum of the badges it expands to. If any
source fails, the script exits non-zero and writes nothing, so a bad run
never replaces a good total with a low one.

Usage: python scripts/update_download_badge.py <output.json>
"""

import json
import sys
import urllib.request
from datetime import date

PYPI_PACKAGES = ("zizkadb-sdk", "zizkadb-mcp", "zizkadb-langchain", "zizkadb-crewai")
NPM_PACKAGES = ("zizkadb-sdk",)
# npm serves at most 18 months per request; zizkadb-sdk is younger than that.
NPM_FIRST_DAY = "2025-05-01"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "zizkadb-download-badge"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pypi_total(package: str) -> int:
    return int(_get_json(f"https://pepy.tech/api/v2/projects/{package}")["total_downloads"])


def npm_total(package: str) -> int:
    period = f"{NPM_FIRST_DAY}:{date.today().isoformat()}"
    return int(_get_json(f"https://api.npmjs.org/downloads/point/{period}/{package}")["downloads"])


def short(n: int) -> str:
    """Format like shields.io: 950, 5.8k, 20.8k, 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    counts = {f"pypi/{p}": pypi_total(p) for p in PYPI_PACKAGES}
    counts.update({f"npm/{p}": npm_total(p) for p in NPM_PACKAGES})
    total = sum(counts.values())
    for name, n in counts.items():
        print(f"{name}: {n}")
    print(f"total: {total}")
    badge = {
        "schemaVersion": 1,
        "label": "downloads",
        "message": short(total),
        "color": "blue",
    }
    with open(sys.argv[1], "w") as f:
        json.dump(badge, f)
        f.write("\n")


if __name__ == "__main__":
    main()
