#!/usr/bin/env python3
"""Seed a realistic demo dataset (default 10,000 events) for screen recordings.

Fills every dashboard feature with believable data over the last 30 days:

  * 3 agents — support-bot, research-agent, billing-agent
  * sessions with causal chains (user_message → llm_response → tool_call →
    tool_result → llm_response → final_answer) for the Why? tab
  * token_usage on every LLM call (several models) for Token Usage / cost
  * repeated identical prompts for Token Optimization to flag
  * errors + retries for Reliability
  * a behavior change for support-bot in the last 4 days (new escalation
    tool, longer chains, more tokens, more errors) for Agent Behavior / drift
  * one showcase session "demo-order-delay" logged today for db.why()

Rows go straight into Postgres so timestamps can be spread over weeks (the
API always stamps NOW()). Checksums are computed exactly like
core/services/event_write.py so integrity checks pass. Every row carries
metadata.seed = "demo"; --reset deletes only those rows.

Usage (run inside the API container so it always hits the stack's Postgres):
  docker exec -i zizkadb_api python - < scripts/seed-demo-data.py
  docker exec -i zizkadb_api python - --events 20000 --days 45 < scripts/seed-demo-data.py
  docker exec -i zizkadb_api python - --reset < scripts/seed-demo-data.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

# services.checksum lives in core/: on the path as /app inside the API container
# (cwd), and next to scripts/ when run from a checkout.
if "__file__" in globals():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, os.getcwd())
from services.checksum import CHECKSUM_COLUMNS, compute_row_checksum_v2  # noqa: E402

# Inside the API container DATABASE_URL points at the stack's Postgres. Running
# on the host is discouraged: a native Postgres on localhost:5432 (Homebrew,
# Postgres.app) silently wins over Docker's published port.
DATABASE_URL = os.getenv("ZIZKADB_SEED_DATABASE_URL") or os.getenv(
    "DATABASE_URL", "postgresql://zizkadb:zizkadb@localhost:5432/zizkadb"
)
# The self-hosted dashboard owner tenant (same IDs as /v1/auth/selfhost-login).
OWNER_TENANT_ID = "00000000-0000-0000-0000-000000000001"
SEED_TAG = {"seed": "demo"}
DRIFT_DAYS = 4  # support-bot behaves differently over the most recent N days

MODELS = {
    "support-bot": ["gpt-4o", "gpt-4o-mini"],
    "research-agent": ["claude-sonnet-5", "gpt-4o"],
    "billing-agent": ["gpt-4o-mini", "claude-haiku-4-5"],
}

SCENARIOS = {
    "support-bot": [
        ("Why was my order delayed?", "lookup_order", {"order_id": "ORD-{n}"}),
        ("I want a refund for my last purchase", "lookup_order", {"order_id": "ORD-{n}"}),
        ("How do I reset my password?", "search_kb", {"query": "password reset"}),
        ("Can I change my shipping address?", "lookup_order", {"order_id": "ORD-{n}"}),
        ("What is your return policy?", "search_kb", {"query": "return policy"}),
    ],
    "research-agent": [
        ("Summarize recent papers on agent memory", "web_search", {"query": "agent memory 2026"}),
        ("Compare vector databases for RAG", "web_search", {"query": "vector database comparison"}),
        ("Find the EU AI Act Article 12 requirements", "fetch_url", {"url": "https://eur-lex.europa.eu/"}),
        ("What changed in the latest Postgres release?", "web_search", {"query": "postgres release notes"}),
    ],
    "billing-agent": [
        ("Why was I charged twice?", "get_invoices", {"customer_id": "CUS-{n}"}),
        ("Upgrade my plan to Team", "update_subscription", {"plan": "team"}),
        ("Send me last month's invoice", "get_invoices", {"customer_id": "CUS-{n}"}),
        ("Cancel my subscription", "update_subscription", {"plan": "cancelled"}),
    ],
}

# Per-agent session weight (support-bot is the busiest).
AGENT_WEIGHTS = {"support-bot": 0.5, "research-agent": 0.3, "billing-agent": 0.2}


def token_usage(model: str, drifted: bool) -> dict:
    scale = 1.8 if drifted else 1.0
    return {
        "model": model,
        "input_tokens": int(random.randint(600, 2400) * scale),
        "output_tokens": int(random.randint(80, 520) * scale),
        "cached_tokens": random.choice([0, 0, 0, 256, 512]),
        "reasoning_tokens": 0,
    }


class SessionBuilder:
    def __init__(self, agent: str, session_id: str, start: datetime):
        self.agent = agent
        self.session_id = session_id
        self.ts = start
        self.rows: list[tuple] = []

    def add(self, event_type: str, data: dict, parent: uuid.UUID | None) -> uuid.UUID:
        event_id = uuid.uuid4()
        self.ts += timedelta(milliseconds=random.randint(150, 4000))
        self.rows.append(
            (
                event_id,
                uuid.UUID(OWNER_TENANT_ID),
                self.agent,
                self.ts,
                event_type,
                json.dumps(data),
                parent,
                self.session_id,
                None,  # v2 checksum is set after COPY, once sequence_no exists
                json.dumps(SEED_TAG),
            )
        )
        return event_id


def build_session(agent: str, n: int, start: datetime, now: datetime) -> list[tuple]:
    drifted = agent == "support-bot" and start > now - timedelta(days=DRIFT_DAYS)
    question, tool, args = random.choice(SCENARIOS[agent])
    args = {k: v.format(n=random.randint(1000, 9999)) if isinstance(v, str) else v for k, v in args.items()}
    model = random.choice(MODELS[agent])
    s = SessionBuilder(agent, f"demo-{agent}-{n:05d}", start)

    user = s.add("user_message", {"text": question, "user_id": f"user-{random.randint(1, 400)}"}, None)
    plan = s.add(
        "llm_response",
        {"model": model, "intent": tool, "token_usage": token_usage(model, drifted)},
        user,
    )
    call = s.add("tool_call", {"tool": tool, "args": args}, plan)

    # Reliability: some tool calls fail and are retried (more often after drift).
    error_rate = 0.18 if drifted else 0.06
    if random.random() < error_rate:
        err = s.add("error", {"tool": tool, "error": random.choice(["timeout", "rate_limited", "upstream_500"])}, call)
        call = s.add("tool_call", {"tool": tool, "args": args, "retry": 1}, err)

    result = s.add("tool_result", {"tool": tool, "status": "ok", "latency_ms": random.randint(80, 1800)}, call)

    # Drift: support-bot starts escalating to humans and taking longer chains.
    if drifted and random.random() < 0.55:
        esc = s.add("tool_call", {"tool": "escalate_to_human", "args": {"reason": "low_confidence"}}, result)
        result = s.add("tool_result", {"tool": "escalate_to_human", "status": "queued"}, esc)

    answer = s.add(
        "llm_response",
        {"model": model, "intent": "answer", "token_usage": token_usage(model, drifted)},
        result,
    )
    s.add("final_answer", {"text": f"Resolved: {question}", "satisfied": random.random() > (0.3 if drifted else 0.1)}, answer)
    return s.rows


def showcase_session(now: datetime) -> list[tuple]:
    """The order-delay story from the README, logged a few minutes ago."""
    s = SessionBuilder("support-bot", "demo-order-delay", now - timedelta(minutes=5))
    u = s.add("user_message", {"text": "Why was my order ORD-8842 delayed?", "user_id": "user-42"}, None)
    llm = s.add("llm_response", {"model": "gpt-4o", "intent": "lookup_order", "token_usage": token_usage("gpt-4o", False)}, u)
    call = s.add("tool_call", {"tool": "lookup_order", "args": {"order_id": "ORD-8842"}}, llm)
    res = s.add("tool_result", {"tool": "lookup_order", "status": "ok", "carrier_delay_days": 2}, call)
    ans = s.add("llm_response", {"model": "gpt-4o", "intent": "answer", "token_usage": token_usage("gpt-4o", False)}, res)
    s.add("final_answer", {"text": "Your order was delayed 2 days by the carrier; it ships tomorrow.", "satisfied": True}, ans)
    return s.rows


def generate(target: int, days: int) -> list[tuple]:
    now = datetime.now(timezone.utc)
    rows = showcase_session(now)
    agents = list(AGENT_WEIGHTS)
    weights = [AGENT_WEIGHTS[a] for a in agents]
    n = 0
    while len(rows) < target:
        n += 1
        agent = random.choices(agents, weights)[0]
        # Busier recently; weekdays and working hours a bit heavier.
        day = int(random.triangular(0, days, 0))
        hour = int(random.triangular(0, 24, 14))
        start = (now - timedelta(days=day)).replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))
        if start > now - timedelta(minutes=10):
            start = now - timedelta(minutes=random.randint(10, 600))
        rows.extend(build_session(agent, n, start, now))
    return rows[:target] if len(rows) == target else trim_to_sessions(rows, target)


def trim_to_sessions(rows: list[tuple], target: int) -> list[tuple]:
    """Never cut a session mid-chain (a child without its parent breaks the FK)."""
    kept, seen = [], set()
    for row in rows:
        if len(kept) >= target and row[6] is None:
            break
        if row[6] is None or row[6] in seen:
            kept.append(row)
            seen.add(row[0])
    return kept


async def reset(conn: asyncpg.Connection) -> int:
    deleted = await conn.fetchval(
        "WITH d AS (DELETE FROM events WHERE metadata->>'seed' = 'demo' RETURNING 1) SELECT count(*) FROM d"
    )
    await conn.execute(
        """
        UPDATE agents a SET event_count = (
          SELECT count(*) FROM events e WHERE e.agent_id = a.agent_id AND e.tenant_id = a.tenant_id
        ) WHERE a.tenant_id = $1::uuid
        """,
        OWNER_TENANT_ID,
    )
    return deleted


async def main(args: argparse.Namespace) -> None:
    random.seed(args.seed)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if args.reset:
            print(f"OK removed {await reset(conn)} demo events")
            return

        tenant = await conn.fetchval("SELECT 1 FROM tenants WHERE tenant_id = $1::uuid", OWNER_TENANT_ID)
        if not tenant:
            raise RuntimeError(
                "Owner tenant not found. Open http://localhost:3001 and click "
                "'Open my dashboard' once, then re-run."
            )

        host = DATABASE_URL.split("@")[-1]
        print(f"-> Target database: {host}")
        rows = generate(args.events, args.days)
        print(f"-> Inserting {len(rows):,} events over the last {args.days} days ...")
        async with conn.transaction():
            for agent in AGENT_WEIGHTS:
                count = sum(1 for r in rows if r[2] == agent)
                await conn.execute(
                    """
                    INSERT INTO agents (agent_id, tenant_id, event_count, last_seen)
                    VALUES ($1, $2::uuid, $3, NOW())
                    ON CONFLICT (agent_id, tenant_id)
                    DO UPDATE SET last_seen = NOW(), event_count = agents.event_count + $3
                    """,
                    agent,
                    OWNER_TENANT_ID,
                    count,
                )
            # Parents always precede children in `rows`, and FK checks run at
            # statement end, so one COPY is safe and fast.
            await conn.copy_records_to_table(
                "events",
                records=rows,
                columns=[
                    "event_id", "tenant_id", "agent_id", "timestamp", "event_type",
                    "data", "parent_event_id", "session_id", "checksum", "metadata",
                ],
            )
            # Same v2 checksum write_event stores, so verify reports the demo
            # events as valid. It covers sequence_no, which COPY just assigned.
            seeded = await conn.fetch(
                f"SELECT {CHECKSUM_COLUMNS} FROM events WHERE event_id = ANY($1::uuid[])",
                [r[0] for r in rows],
            )
            await conn.executemany(
                "UPDATE events SET checksum = $1 WHERE event_id = $2",
                [(compute_row_checksum_v2(r), r["event_id"]) for r in seeded],
            )
        sessions = len({r[7] for r in rows})
        print(f"OK {len(rows):,} events in {sessions:,} sessions across {len(AGENT_WEIGHTS)} agents")
        print("   Showcase session: demo-order-delay (support-bot) — open it and click 'Why? (causal)'")
        print(f"   support-bot behaves differently over the last {DRIFT_DAYS} days (see Agent Behavior)")
        print("   Remove later with: docker exec -i zizkadb_api python - --reset < scripts/seed-demo-data.py")
    finally:
        await conn.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", type=int, default=10_000, help="approximate number of events (default 10000)")
    p.add_argument("--days", type=int, default=30, help="spread events over the last N days (default 30)")
    p.add_argument("--seed", type=int, default=42, help="random seed for reproducible data")
    p.add_argument("--reset", action="store_true", help="delete demo rows (metadata.seed = 'demo') and exit")
    return p.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except Exception as exc:  # noqa: BLE001 — CLI entry point
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Start the stack first: bash scripts/setup-local.sh", file=sys.stderr)
        sys.exit(1)
