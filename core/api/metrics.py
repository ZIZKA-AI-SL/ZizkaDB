"""GET /metrics — Prometheus text format, for the operator's monitoring.

Off unless METRICS_TOKEN is set; then scrapers send ``Authorization: Bearer
<token>``. Values come from Postgres, so every uvicorn worker reports the same
numbers; pool gauges are per worker and carry a ``pid`` label.
"""

from __future__ import annotations

import hmac
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from db.connection import check_redis, get_pool
from services.exceptions import not_found, unauthorized

router = APIRouter()

_STARTED = time.time()


def _authorised(request: Request) -> bool:
    token = (os.getenv("METRICS_TOKEN") or "").strip()
    if not token:
        raise not_found("Not Found")
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    return hmac.compare_digest(supplied.encode(), token.encode())


def _line(name: str, value: float, labels: dict | None = None) -> str:
    if labels:
        inner = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{inner}}} {value}"
    return f"{name} {value}"


async def collect(version: str) -> str:
    out: list[str] = []

    def gauge(name: str, help_text: str, samples: list[tuple[dict | None, float]]):
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} gauge")
        out.extend(_line(name, v, labels) for labels, v in samples)

    gauge("zizkadb_build_info", "Version of the running API.", [({"version": version}, 1)])
    gauge("zizkadb_process_uptime_seconds", "Seconds since this worker started.",
          [({"pid": os.getpid()}, round(time.time() - _STARTED, 1))])

    pool = get_pool()
    try:
        events = await pool.fetchval(
            "SELECT GREATEST(reltuples, 0)::bigint FROM pg_class WHERE relname = 'events'"
        )
        agents = await pool.fetchval("SELECT COUNT(*) FROM agents")
        queue = dict(await pool.fetch(
            """
            SELECT index_status, COUNT(*) FROM events
            WHERE index_status IN ('pending', 'failed')
            GROUP BY index_status
            """
        ))
        tombstones = await pool.fetchval("SELECT COUNT(*) FROM event_tombstones")
        db_bytes = await pool.fetchval("SELECT pg_database_size(current_database())")
        db_up = 1
    except Exception:
        events = agents = tombstones = db_bytes = 0
        queue = {}
        db_up = 0

    gauge("zizkadb_postgres_up", "1 when Postgres answered this scrape.", [(None, db_up)])
    gauge("zizkadb_events_estimated", "Events stored (planner estimate, cheap on large tables).",
          [(None, events or 0)])
    gauge("zizkadb_agents", "Agents stored.", [(None, agents or 0)])
    gauge("zizkadb_embedding_queue", "Events waiting for embedding, by status.",
          [({"status": s}, queue.get(s, 0)) for s in ("pending", "failed")])
    gauge("zizkadb_event_tombstones", "Chain tombstones of deleted events.", [(None, tombstones or 0)])
    gauge("zizkadb_database_bytes", "Size of the Postgres database.", [(None, db_bytes or 0)])
    gauge("zizkadb_db_pool_connections", "Connections in this worker's pool, by state.", [
        ({"pid": os.getpid(), "state": "total"}, pool.get_size()),
        ({"pid": os.getpid(), "state": "idle"}, pool.get_idle_size()),
    ])
    redis_ok = bool((await check_redis()).get("ok"))
    gauge("zizkadb_redis_up", "1 when Redis answered this scrape.", [(None, int(redis_ok))])
    return "\n".join(out) + "\n"


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    if not _authorised(request):
        raise unauthorized("Invalid metrics token")
    from main import API_VERSION

    return PlainTextResponse(await collect(API_VERSION), media_type="text/plain; version=0.0.4")
