#!/usr/bin/env python3
"""Regenerate the README contributors grid from GitHub.

Contributors = commit authors (GitHub API) + authors of merged PRs
(squash merges credit the merger, so PR authors would otherwise be lost)
+ logins listed in .github/contributors-extra.txt. Bots are skipped.

Usage: GITHUB_TOKEN=... python scripts/update_contributors.py [owner/repo]
"""

import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
EXTRA = ROOT / ".github" / "contributors-extra.txt"
START, END = "<!-- CONTRIBUTORS:START -->", "<!-- CONTRIBUTORS:END -->"
COLS = 4
TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")


def api(url: str) -> list:
    out: list = []
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if TOKEN:
            req.add_header("Authorization", f"Bearer {TOKEN}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            out.extend(json.load(resp))
            m = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link", ""))
            url = m.group(1) if m else None
    return out


def is_bot(user: dict) -> bool:
    # "ghost" is GitHub's placeholder for deleted accounts
    return user.get("type") == "Bot" or user["login"].endswith("[bot]") or user["login"] == "ghost"


def collect(repo: str) -> list[str]:
    score: Counter[str] = Counter()
    names: dict[str, str] = {}

    def add(login: str, n: int) -> None:
        key = login.lower()
        names.setdefault(key, login)
        score[key] += n

    for u in api(f"https://api.github.com/repos/{repo}/contributors?per_page=100"):
        if not is_bot(u):
            add(u["login"], u["contributions"])
    for pr in api(f"https://api.github.com/repos/{repo}/pulls?state=closed&per_page=100"):
        if pr.get("merged_at") and pr.get("user") and not is_bot(pr["user"]):
            add(pr["user"]["login"], 1)
    if EXTRA.exists():
        for line in EXTRA.read_text().splitlines():
            login = line.split("#", 1)[0].strip()
            if login:
                add(login, 0)
    return [names[k] for k, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))]


def render(logins: list[str]) -> str:
    rows = []
    for i in range(0, len(logins), COLS):
        cells = "\n".join(
            f'    <td align="center" width="25%"><a href="https://github.com/{u}">'
            f'<img src="https://github.com/{u}.png?size=120" width="64" alt="{u}"/>'
            f"<br/><sub>{u}</sub></a></td>"
            for u in logins[i : i + COLS]
        )
        rows.append(f"  <tr>\n{cells}\n  </tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else os.getenv("GITHUB_REPOSITORY", "Zizka-ai/ZizkaDB")
    text = README.read_text()
    if START not in text or END not in text:
        sys.exit(f"README is missing {START} / {END} markers")
    logins = collect(repo)
    block = f"{START}\n{render(logins)}\n{END}"
    new = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    if new != text:
        README.write_text(new)
    print(f"{len(logins)} contributors")


if __name__ == "__main__":
    main()
