"""Agent-to-agent handoffs (db.handoff / db.continue_from) and agent_version."""

import pytest

from zizkadb import HANDOFF_HEADER, HandoffToken, ZizkaDB, token_from_headers
from zizkadb.exceptions import CloudOnlyError, NotFoundError


class FakeServer:
    """Records posts; /v1/a2a/messages behaves like Cloud, or 404s like a self-hosted server (no such route)."""

    def __init__(self, a2a: bool = True, receipt: bool = True):
        self.a2a = a2a
        self.receipt = receipt
        self.posts: list[tuple[str, dict]] = []

    async def post(self, _db, path, body):
        self.posts.append((path, dict(body)))
        n = len(self.posts)
        base = {"timestamp": "2026-01-01T00:00:00+00:00", "sequence_no": n, "checksum": "c", "indexed": False}
        if path == "/v1/a2a/messages":
            if not self.a2a:
                raise NotFoundError("Resource not found", status_code=404)
            out = {**base, "event_id": f"sent-{n}", "message_id": "msg-1", "session_id": body.get("session_id")}
            if self.receipt:
                out["received_event_id"] = f"recv-{n}"
            return out
        return {**base, "event_id": f"evt-{n}"}


def _patch(monkeypatch, s):
    async def _post(db, path, body):
        return await s.post(db, path, body)

    monkeypatch.setattr(ZizkaDB, "_post", _post)


@pytest.fixture
def server(monkeypatch):
    s = FakeServer()
    _patch(monkeypatch, s)
    return s


def test_token_round_trip_and_ids_only():
    t = HandoffToken(event_id="e1", session_id="s1", from_agent="planner", to_agent="billing", message_id="m1", received_event_id="r1")
    token = t.encode()
    assert token.startswith("zdbh1.")
    assert HandoffToken.decode(token) == t
    with pytest.raises(ValueError):
        HandoffToken.decode("not-a-token")
    with pytest.raises(ValueError):
        HandoffToken.decode("zdbh1.%%%")


@pytest.mark.asyncio
async def test_handoff_on_cloud_uses_the_a2a_endpoint(server):
    async with ZizkaDB(host="http://localhost:8000") as db:
        async with db.track(agent="planner", session_id="case-1"):
            first = await db.log(agent="planner", event="user_message", data={"text": "refund 4411"})
            h = await db.handoff(agent="planner", to="billing", message="Refund order 4411")

    path, body = server.posts[-1]
    assert path == "/v1/a2a/messages"
    assert body["recipient_agent"] == "billing"
    assert body["parent_id"] == first.event_id  # linked to what caused the handoff
    assert body["session_id"] == "case-1"
    assert h.recorded_by == "a2a"
    assert h.headers == {HANDOFF_HEADER: h.token}
    decoded = HandoffToken.decode(h.token)
    assert (decoded.event_id, decoded.received_event_id, decoded.to_agent) == (h.event_id, h.received_event_id, "billing")


@pytest.mark.asyncio
async def test_handoff_needs_zizkadb_cloud(monkeypatch):
    # A self-hosted server has no /v1/a2a/messages: it runs one agent.
    s = FakeServer(a2a=False)
    _patch(monkeypatch, s)
    async with ZizkaDB(host="http://localhost:8000") as db:
        with pytest.raises(CloudOnlyError, match="ZizkaDB Cloud"):
            await db.handoff(agent="planner", to="billing", message="hi", session_id="case-1")
    # Nothing was written for a second agent.
    assert all(path != "/v1/events" for path, _ in s.posts)


@pytest.mark.asyncio
async def test_a_cloud_not_found_is_not_reported_as_cloud_only(monkeypatch):
    async def _post(db, path, body):
        raise NotFoundError("Resource not found", status_code=404, detail="Recipient agent 'billing' not found")

    monkeypatch.setattr(ZizkaDB, "_post", _post)
    async with ZizkaDB(host="http://localhost:8000") as db:
        with pytest.raises(NotFoundError, match="Recipient agent 'billing' not found") as exc:
            await db.handoff(agent="planner", to="billing", message="hi")
    assert not isinstance(exc.value, CloudOnlyError)


@pytest.mark.asyncio
async def test_continue_from_records_the_receipt_when_the_server_did_not(monkeypatch):
    # An older Cloud server answers without received_event_id.
    s = FakeServer(receipt=False)
    _patch(monkeypatch, s)
    async with ZizkaDB(host="http://localhost:8000") as db:
        h = await db.handoff(agent="planner", to="billing", message="hi", session_id="case-1")
        async with db.continue_from(h.token, agent="billing") as ctx:
            await db.log(agent="billing", event="tool_call", data={"tool": "refund"})

    received = s.posts[-2][1]
    assert received["event"] == "a2a.received"
    assert received["agent"] == "billing"
    assert received["parent_id"] == h.event_id
    assert s.posts[-1][1]["parent_id"] == ctx.received_event_id


@pytest.mark.asyncio
async def test_continue_from_reuses_the_receipt_the_server_recorded(server):
    async with ZizkaDB(host="http://localhost:8000") as db:
        h = await db.handoff(agent="planner", to="billing", message="hi", session_id="case-1")
        before = len(server.posts)
        async with db.continue_from(h.token, agent="billing") as ctx:
            await db.log(agent="billing", event="tool_call", data={})

    assert len(server.posts) == before + 1  # no second a2a.received
    assert ctx.received_event_id == h.received_event_id
    assert server.posts[-1][1]["parent_id"] == h.received_event_id


def test_token_from_headers_is_case_insensitive():
    assert token_from_headers({"x-zizkadb-handoff": "zdbh1.abc"}) == "zdbh1.abc"
    assert token_from_headers({HANDOFF_HEADER: "zdbh1.abc"}) == "zdbh1.abc"
    assert token_from_headers({}) is None
    assert token_from_headers(None) is None


@pytest.mark.asyncio
async def test_agent_version_is_recorded_on_every_write(server, monkeypatch):
    async with ZizkaDB(host="http://localhost:8000", agent_version="prompt-v7") as db:
        await db.log(agent="planner", event="user_message", data={}, metadata={"team": "a"})
        await db.log(agent="planner", event="x", data={}, metadata={"agent_version": "explicit"})
        await db.handoff(agent="planner", to="billing", message="hi")

    assert server.posts[0][1]["metadata"] == {"team": "a", "agent_version": "prompt-v7"}
    assert server.posts[1][1]["metadata"] == {"agent_version": "explicit"}
    assert server.posts[2][1]["metadata"] == {"agent_version": "prompt-v7"}

    monkeypatch.setenv("ZIZKADB_AGENT_VERSION", "from-env")
    async with ZizkaDB(host="http://localhost:8000") as db:
        await db.log(agent="planner", event="y", data={})
    assert server.posts[-1][1]["metadata"] == {"agent_version": "from-env"}


@pytest.mark.asyncio
async def test_no_agent_version_leaves_metadata_alone(server, monkeypatch):
    monkeypatch.delenv("ZIZKADB_AGENT_VERSION", raising=False)
    async with ZizkaDB(host="http://localhost:8000") as db:
        await db.log(agent="planner", event="x", data={})
    assert server.posts[-1][1]["metadata"] is None
