"""Event checksums against a real Postgres: what write_event stores must
verify as ``valid`` after the JSONB / UUID / TIMESTAMPTZ round trip, and any
rewrite of an audit field must verify as ``mismatch``."""

import pytest

from api.events import verify_event, verify_events
from services.checksum import compute_legacy_checksum
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "auditor"


@pytest.fixture
async def tenant(pg_db):
    tenant_id = await pg_db.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    )
    return {"tenant_id": str(tenant_id), "agent_id": AGENT}


async def _verify(tenant, event_id):
    return (await verify_event(event_id, tenant=tenant))["status"]


async def test_round_trip_edge_cases_verify_valid(pg_db, tenant):
    root = await write_event(
        tenant_id=tenant["tenant_id"], agent=AGENT, event="root", data={"a": 1},
    )
    # JSONB rewrites these numbers (1e16 -> 10000000000000000, 1.50 -> 1.5),
    # stores {} metadata as NULL and canonicalises the uppercase UUID.
    child = await write_event(
        tenant_id=tenant["tenant_id"],
        agent=AGENT,
        event="child",
        data={"big": 1e16, "f": 1.50, "neg": -0.0, "s": "café 🚀", "nested": {"b": [1, None], "a": True}},
        parent_id=root["event_id"].upper(),
        session_id="s-1",
        metadata={},
    )
    with_meta = await write_event(
        tenant_id=tenant["tenant_id"], agent=AGENT, event="m", data={}, metadata={"k": [1.0]},
    )

    for written in (root, child, with_meta):
        assert await _verify(tenant, written["event_id"]) == "valid"
        stored = await pg_db.fetchval(
            "SELECT checksum FROM events WHERE event_id = $1", written["event_id"]
        )
        assert stored == written["checksum"]

    report = await verify_events(AGENT, limit=100, before=None, after=None, tenant=tenant)
    assert report["summary"] == {"checked": 3, "valid": 3, "valid_legacy": 0, "mismatch": 0, "missing": 0}


@pytest.mark.parametrize(
    "assignment",
    [
        "data = '{\"a\": 2}'::jsonb",
        "event_type = 'other'",
        "session_id = 'forged'",
        "timestamp = timestamp - interval '1 day'",
        "sequence_no = sequence_no + 1000",
        "metadata = '{\"x\": 1}'::jsonb",
        "parent_event_id = NULL",
    ],
)
async def test_tampering_with_audit_fields_is_detected(pg_db, tenant, assignment):
    root = await write_event(tenant_id=tenant["tenant_id"], agent=AGENT, event="root", data={})
    child = await write_event(
        tenant_id=tenant["tenant_id"], agent=AGENT, event="child", data={"a": 1},
        parent_id=root["event_id"], session_id="s",
    )
    await pg_db.execute(f"UPDATE events SET {assignment} WHERE event_id = $1", child["event_id"])
    assert await _verify(tenant, child["event_id"]) == "mismatch"


async def test_embedding_pipeline_updates_do_not_invalidate(pg_db, tenant):
    written = await write_event(tenant_id=tenant["tenant_id"], agent=AGENT, event="e", data={})
    await pg_db.execute(
        "UPDATE events SET index_status = 'indexed' WHERE event_id = $1", written["event_id"]
    )
    assert await _verify(tenant, written["event_id"]) == "valid"


async def test_legacy_and_missing_rows(pg_db, tenant):
    legacy = await write_event(tenant_id=tenant["tenant_id"], agent=AGENT, event="e", data={"x": 1})
    missing = await write_event(tenant_id=tenant["tenant_id"], agent=AGENT, event="e", data={})
    await pg_db.execute(
        "UPDATE events SET checksum = $1 WHERE event_id = $2",
        compute_legacy_checksum("e", {"x": 1}), legacy["event_id"],
    )
    await pg_db.execute("UPDATE events SET checksum = NULL WHERE event_id = $1", missing["event_id"])

    assert await _verify(tenant, legacy["event_id"]) == "valid_legacy"
    assert await _verify(tenant, missing["event_id"]) == "missing"
