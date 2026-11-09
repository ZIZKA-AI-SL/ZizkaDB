"""Tests for scope validation in services.api_keys."""

import pytest

from services.api_keys import validate_scope, _expand_scopes


@pytest.mark.parametrize("scope,expected", [
    ("read_write", "events:read,events:write"),
    ("read_only", "events:read"),
    ("admin", "agents:manage,agents:read,agents:write,events:delete,events:read,events:write,webhooks:manage,webhooks:read,webhooks:write"),
    ("events:read", "events:read"),
    ("events:write", "events:write"),
    ("events:read,events:write", "events:read,events:write"),
    ("webhooks:read,events:read", "events:read,webhooks:read"),
    (None, None),
    ("", None),
    ("   ", None),
])
def test_validate_scope(scope, expected):
    assert validate_scope(scope) == expected


@pytest.mark.parametrize("scope,expected", [
    ("read_write", {"events:read", "events:write"}),
    ("read_only", {"events:read"}),
    ("admin", {"agents:manage", "agents:read", "agents:write", "events:delete", "events:read", "events:write", "webhooks:manage", "webhooks:read", "webhooks:write"}),
    ("events:read", {"events:read"}),
    ("events:read,events:write", {"events:read", "events:write"}),
    (None, set()),
    ("", set()),
])
def test_expand_scopes(scope, expected):
    assert _expand_scopes(scope) == expected