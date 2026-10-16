#!/usr/bin/env python3
"""First-boot demo for the single-container image.

Logs the same 3-step support-bot chain as `zizkadb demo`, then prints the
causal chain the API returns for the last step. Runs inside the container, so
the quickstart needs no Python or SDK on the host.
"""

from __future__ import annotations

import os
import sys

import httpx

API = os.getenv("ZIZKADB_SEED_API", "http://127.0.0.1:8000")
KEY = os.getenv("DEV_API_KEY", "zizkadb_dev_local")
AGENT = "support-bot"

STEPS = [
    ("user_message", {"text": "Why was my order delayed?"}),
    ("llm_response", {"model": "gpt-4o", "tokens": 412}),
    ("tool_call", {"tool": "lookup_order", "order_id": "ORD-8842"}),
]


def _label(event: dict) -> str:
    data = event.get("data") or {}
    name = event.get("event") or event.get("event_type") or "event"
    detail = [str(data[k]) for k in ("tool", "order_id", "model", "text") if k in data]
    return " · ".join([name, *detail])


def main() -> int:
    with httpx.Client(
        base_url=API, headers={"Authorization": f"Bearer {KEY}"}, timeout=15
    ) as http:
        parent_id = None
        for event, data in STEPS:
            res = http.post(
                "/v1/events",
                json={"agent": AGENT, "event": event, "data": data, "parent_id": parent_id},
            )
            res.raise_for_status()
            parent_id = res.json()["event_id"]

        why = http.get(f"/v1/events/{parent_id}/why")
        why.raise_for_status()
        chain = why.json()["chain"]

    # The API returns root first; show the question's answer leaf first.
    for depth, event in enumerate(reversed(chain)):
        prefix = "" if depth == 0 else "      " * (depth - 1) + "  └── "
        print(f"{prefix}{_label(event)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - reported by the entrypoint
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
