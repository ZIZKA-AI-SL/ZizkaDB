"""Tests for checksum v2 computation and verification."""

import datetime
import hashlib
import json

from services.checksum import (
    compute_event_checksum_v2,
    compute_legacy_checksum,
    verify_stored_checksum,
)

TS = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

BASE_KW = dict(
    tenant_id="t-1",
    agent_id="agent1",
    event_type="message",
    data={"text": "hello"},
    parent_event_id=None,
    session_id=None,
    timestamp=TS,
    sequence_no=7,
    metadata=None,
)
# Rows up to this sequence_no were written before v2 (BASE_KW is sequence 7).
LEGACY_UNTIL = 100


def test_v2_deterministic_regardless_of_key_order():
    a = compute_event_checksum_v2(**BASE_KW)
    b = compute_event_checksum_v2(**{**BASE_KW, "data": {"text": "hello"}})
    assert a == b


def test_v2_ignores_dict_insertion_order():
    kw = dict(BASE_KW)
    kw["data"] = {"b": 2, "a": 1}
    a = compute_event_checksum_v2(**kw)
    kw["data"] = {"a": 1, "b": 2}
    assert compute_event_checksum_v2(**kw) == a


def test_v2_detects_tampering_in_each_audit_field():
    base = compute_event_checksum_v2(**BASE_KW)
    mutations = [
        ("tenant_id", "t-2"),
        ("agent_id", "agent2"),
        ("event_type", "tool_call"),
        ("data", {"text": "goodbye"}),
        ("parent_event_id", "12345678-1234-1234-1234-123456789abc"),
        ("session_id", "s-9"),
        ("timestamp", TS + datetime.timedelta(seconds=1)),
        ("sequence_no", 8),
        ("metadata", {"k": "v"}),
    ]
    for field, value in mutations:
        mutated = compute_event_checksum_v2(**{**BASE_KW, field: value})
        assert mutated != base, f"tampering with {field} did not change the checksum"


def test_v2_naive_datetime_treated_as_utc():
    naive = BASE_KW["timestamp"].replace(tzinfo=None)
    assert (
        compute_event_checksum_v2(**{**BASE_KW, "timestamp": naive}) == compute_event_checksum_v2(**BASE_KW)
    )


def test_v2_stable_across_jsonb_round_trip():
    """JSONB normalises key order/whitespace; the reloaded dict must hash the same."""
    kw = dict(BASE_KW)
    kw["data"] = {"z": [1, 2], "a": {"nested": True}, "m": "café 🚀"}
    original = compute_event_checksum_v2(**kw)
    reloaded = json.loads(json.dumps(kw["data"]))
    assert compute_event_checksum_v2(**{**kw, "data": reloaded}) == original


def test_legacy_v1_formula_matches_historical_write_path():
    content = json.dumps({"event": "message", "data": {"text": "hello"}}, sort_keys=True)
    assert compute_legacy_checksum("message", {"text": "hello"}) == hashlib.sha256(content.encode()).hexdigest()


def test_verify_reports_valid_for_v2_row():
    row = {
        **{k: v for k, v in BASE_KW.items() if k != "metadata"},
        "metadata": None,
        "checksum": compute_event_checksum_v2(**BASE_KW),
    }
    result = verify_stored_checksum(row, LEGACY_UNTIL)
    assert result == {"status": "valid", "version": "v2"}


def test_verify_accepts_jsonb_string_data_and_metadata():
    row = {
        **BASE_KW,
        "data": json.dumps({"text": "hello"}),
        "metadata": json.dumps({"k": "v"}),
        "checksum": compute_event_checksum_v2(**{**BASE_KW, "metadata": {"k": "v"}}),
    }
    assert verify_stored_checksum(row, LEGACY_UNTIL)["status"] == "valid"


def test_verify_reports_valid_legacy_for_v1_row():
    row = {
        **BASE_KW,
        "checksum": compute_legacy_checksum("message", {"text": "hello"}),
    }
    result = verify_stored_checksum(row, LEGACY_UNTIL)
    assert result == {"status": "valid_legacy", "version": "v1"}


def test_verify_rejects_v1_checksum_on_rows_written_after_v2():
    """Downgrade attack: edit a v2-only field, then store the payload-only v1
    hash (which that edit does not change). Past the cutoff this must fail."""
    row = {
        **BASE_KW,
        "session_id": "forged",
        "checksum": compute_legacy_checksum("message", {"text": "hello"}),
    }
    assert verify_stored_checksum(row, BASE_KW["sequence_no"] - 1)["status"] == "mismatch"
    assert verify_stored_checksum(row, None)["status"] == "mismatch"


def test_verify_reports_mismatch_for_tampered_row():
    row = {
        **BASE_KW,
        "checksum": compute_legacy_checksum("message", {"text": "tampered"}),
    }
    result = verify_stored_checksum(row, LEGACY_UNTIL)
    assert result == {"status": "mismatch", "version": None}


def test_verify_reports_missing_when_no_checksum_stored():
    row = {**BASE_KW, "checksum": None}
    result = verify_stored_checksum(row, LEGACY_UNTIL)
    assert result == {"status": "missing", "version": None}


def test_v2_canonicalises_uuids():
    """Postgres returns lowercase hyphenated UUIDs whatever the client sent."""
    from uuid import UUID

    parent = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    tenant = "11111111-1111-1111-1111-111111111111"
    sent = compute_event_checksum_v2(**{**BASE_KW, "tenant_id": tenant, "parent_event_id": parent})
    read_back = compute_event_checksum_v2(
        **{**BASE_KW, "tenant_id": UUID(tenant), "parent_event_id": UUID(parent)}
    )
    assert sent == read_back


def test_v2_empty_metadata_equals_null():
    """The write path stores {} metadata as NULL."""
    assert compute_event_checksum_v2(**{**BASE_KW, "metadata": {}}) == compute_event_checksum_v2(**BASE_KW)


def test_v2_non_utc_timestamp_normalised():
    plus_two = datetime.timezone(datetime.timedelta(hours=2))
    shifted = TS.astimezone(plus_two)
    assert compute_event_checksum_v2(**{**BASE_KW, "timestamp": shifted}) == compute_event_checksum_v2(**BASE_KW)


def test_v2_row_helper_matches_keyword_form():
    from services.checksum import compute_row_checksum_v2

    row = {**BASE_KW, "data": json.dumps(BASE_KW["data"])}
    assert compute_row_checksum_v2(row) == compute_event_checksum_v2(**BASE_KW)
