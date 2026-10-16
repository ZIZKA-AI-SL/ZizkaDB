"""Unit tests for the plan-entitlement config and the API-key enforcement guard.

Infrastructure-free: the config logic is pure, and the advisory-locked guard is
exercised against a fake asyncpg connection so no database is required.
"""

import pytest
from fastapi import HTTPException

from services import api_keys, billing
from services.entitlements import (
    PLAN_ENTITLEMENTS,
    api_key_limit_for_plan,
    embeddings_enabled,
    entitlements_for_plan,
    is_self_hosted_deployment,
    limits_enforced,
)


# ─────────────────────────────────────────
# entitlements_for_plan / api_key_limit_for_plan (single source of truth)
# ─────────────────────────────────────────

class TestApiKeyLimitForPlan:
    def test_self_hosted_plan_capped_at_one(self):
        assert api_key_limit_for_plan("self_hosted") == 1

    def test_pro_plan_capped_at_two(self):
        assert api_key_limit_for_plan("pro") == 2

    def test_team_plan_capped_at_five(self):
        assert api_key_limit_for_plan("team") == 5

    def test_config_dict_matches_expected(self):
        assert {plan: e.max_api_keys for plan, e in PLAN_ENTITLEMENTS.items()} == {
            "self_hosted": 1,
            "pro": 2,
            "team": 5,
        }

    def test_plan_is_case_and_whitespace_insensitive(self):
        assert api_key_limit_for_plan("  TEAM  ") == 5
        assert api_key_limit_for_plan("Pro") == 2
        assert api_key_limit_for_plan("Self_Hosted") == 1

    def test_unknown_plan_is_unlimited(self):
        assert api_key_limit_for_plan("free") is None
        assert api_key_limit_for_plan("pending_checkout") is None
        assert api_key_limit_for_plan("starter") is None

    def test_none_plan_is_unlimited(self):
        assert api_key_limit_for_plan(None) is None

    def test_entitlements_for_plan_returns_unlimited_default_for_unknown(self):
        assert entitlements_for_plan("unknown").max_api_keys is None


# ─────────────────────────────────────────
# API_KEY_LIMIT_<PLAN> env override (change a limit without a code change)
# ─────────────────────────────────────────

class TestLimitOverride:
    def test_no_override_uses_config_default(self, monkeypatch):
        monkeypatch.delenv("API_KEY_LIMIT_PRO", raising=False)
        assert api_key_limit_for_plan("pro") == 2

    def test_override_replaces_config_default(self, monkeypatch):
        monkeypatch.setenv("API_KEY_LIMIT_PRO", "7")
        assert api_key_limit_for_plan("pro") == 7

    def test_override_is_per_plan(self, monkeypatch):
        monkeypatch.setenv("API_KEY_LIMIT_TEAM", "20")
        assert api_key_limit_for_plan("team") == 20
        assert api_key_limit_for_plan("pro") == 2  # unaffected

    def test_blank_override_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("API_KEY_LIMIT_PRO", "  ")
        assert api_key_limit_for_plan("pro") == 2

    def test_invalid_override_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("API_KEY_LIMIT_PRO", "not-a-number")
        assert api_key_limit_for_plan("pro") == 2

    def test_override_works_for_unknown_plan_too(self, monkeypatch):
        # An override can grant a cap to a plan with no PLAN_ENTITLEMENTS
        # entry (default unlimited) — useful for a temporary/ad-hoc plan.
        monkeypatch.setenv("API_KEY_LIMIT_STARTER", "4")
        assert api_key_limit_for_plan("starter") == 4


# ─────────────────────────────────────────
# limits_enforced (kill switch)
# ─────────────────────────────────────────

class TestLimitsEnforced:
    @pytest.fixture(autouse=True)
    def _managed(self, monkeypatch):
        # The kill switch only applies to managed deployments.
        monkeypatch.setenv("DEPLOYMENT_MODE", "managed")

    def test_defaults_off(self, monkeypatch):
        monkeypatch.delenv("API_KEY_LIMITS_ENFORCED", raising=False)
        assert limits_enforced() is False

    @pytest.mark.parametrize("switch", [None, "false", "0"])
    def test_self_hosted_is_always_enforced(self, monkeypatch, switch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
        if switch is None:
            monkeypatch.delenv("API_KEY_LIMITS_ENFORCED", raising=False)
        else:
            monkeypatch.setenv("API_KEY_LIMITS_ENFORCED", switch)
        assert limits_enforced() is True

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("API_KEY_LIMITS_ENFORCED", value)
        assert limits_enforced() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("API_KEY_LIMITS_ENFORCED", value)
        assert limits_enforced() is False


# ─────────────────────────────────────────
# is_self_hosted_deployment (deployment-mode override)
# ─────────────────────────────────────────

class TestIsSelfHostedDeployment:
    def test_defaults_to_managed(self, monkeypatch):
        monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
        assert is_self_hosted_deployment() is False

    def test_self_hosted_value_enables(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
        assert is_self_hosted_deployment() is True

    def test_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "  Self_Hosted  ")
        assert is_self_hosted_deployment() is True

    def test_managed_value_disables(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
        assert is_self_hosted_deployment() is False


class TestEmbeddingsEnabled:
    def test_managed_defaults_on(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
        monkeypatch.delenv("EMBEDDINGS_ENABLED", raising=False)
        assert embeddings_enabled() is True

    def test_self_hosted_defaults_off(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
        monkeypatch.delenv("EMBEDDINGS_ENABLED", raising=False)
        assert embeddings_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_self_hosted_opt_in(self, monkeypatch, value):
        monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
        monkeypatch.setenv("EMBEDDINGS_ENABLED", value)
        assert embeddings_enabled() is True


# ─────────────────────────────────────────
# fetch_effective_plan (self-hosted override vs. managed lookup)
# ─────────────────────────────────────────

class FakeConn:
    """Minimal asyncpg-connection stand-in for the guard.

    ``fetchval`` routes by SQL: the COUNT query returns the active-key count,
    everything else (the plan lookup) returns the configured plan.
    """

    def __init__(self, plan: str | None, count: int = 0):
        self._plan = plan
        self._count = count
        self.executed: list[tuple] = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "SELECT 1"

    async def fetchval(self, query, *args):
        if "COUNT(" in query:
            return self._count
        return self._plan


class ExplodingConn(FakeConn):
    async def fetchval(self, query, *args):
        if "COUNT(" in query:
            return self._count
        raise RuntimeError("simulated plan lookup failure")


class TestFetchEffectivePlan:
    async def test_self_hosted_short_circuits_without_touching_users_plan(self, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
        # Even if users.plan would resolve to something else (e.g. via the
        # legacy NULL->'pro' backfill in connection.py), self-hosted
        # deployments must never read it.
        conn = FakeConn(plan="pro")
        assert await billing.fetch_effective_plan(conn, "t1") == "self_hosted"
        assert conn.executed == []

    async def test_managed_defers_to_users_plan(self, monkeypatch):
        monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
        conn = FakeConn(plan="team")
        assert await billing.fetch_effective_plan(conn, "t1") == "team"


# ─────────────────────────────────────────
# assert_and_reserve_api_key_slot (enforcement guard)
# ─────────────────────────────────────────

@pytest.fixture
def enforce(monkeypatch):
    """Turn API key limit enforcement ON for the guard under test."""
    monkeypatch.setattr(api_keys, "limits_enforced", lambda: True)


async def test_guard_no_op_when_enforcement_off(monkeypatch):
    monkeypatch.setattr(api_keys, "limits_enforced", lambda: False)
    conn = FakeConn(plan="pro", count=99)
    # Even wildly over limit, disabled enforcement must never raise or lock.
    await api_keys.assert_and_reserve_api_key_slot(conn, tenant_id="t1")
    assert conn.executed == []


async def test_guard_allows_under_limit(enforce):
    conn = FakeConn(plan="pro", count=1)  # limit 2
    await api_keys.assert_and_reserve_api_key_slot(conn, tenant_id="t1")
    # Advisory lock must be taken before the count check.
    assert any("pg_advisory_xact_lock" in q for q, _ in conn.executed)


async def test_guard_blocks_at_limit(enforce):
    conn = FakeConn(plan="pro", count=2)  # limit 2, already full
    with pytest.raises(HTTPException) as exc:
        await api_keys.assert_and_reserve_api_key_slot(conn, tenant_id="t1")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "api_key_limit_reached"
    assert exc.value.detail["limit"] == 2
    assert exc.value.detail["used"] == 2


async def test_guard_team_limit_is_five(enforce):
    conn_ok = FakeConn(plan="team", count=4)  # under limit of 5
    await api_keys.assert_and_reserve_api_key_slot(conn_ok, tenant_id="t1")

    conn_full = FakeConn(plan="team", count=5)
    with pytest.raises(HTTPException) as exc:
        await api_keys.assert_and_reserve_api_key_slot(conn_full, tenant_id="t1")
    assert exc.value.detail["limit"] == 5


async def test_guard_respects_env_override(enforce, monkeypatch):
    monkeypatch.setenv("API_KEY_LIMIT_PRO", "1")
    conn_full = FakeConn(plan="pro", count=1)
    with pytest.raises(HTTPException) as exc:
        await api_keys.assert_and_reserve_api_key_slot(conn_full, tenant_id="t1")
    assert exc.value.detail["limit"] == 1



async def test_guard_self_hosted_limit_is_one(enforce, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
    # users.plan says "pro" (e.g. from the legacy NULL->'pro' backfill), but
    # the deployment-mode override must still apply the self_hosted limit.
    conn_ok = FakeConn(plan="pro", count=0)
    await api_keys.assert_and_reserve_api_key_slot(conn_ok, tenant_id="t1")

    conn_full = FakeConn(plan="pro", count=1)
    with pytest.raises(HTTPException) as exc:
        await api_keys.assert_and_reserve_api_key_slot(conn_full, tenant_id="t1")
    assert exc.value.detail["plan"] == "self_hosted"
    assert exc.value.detail["limit"] == 1


async def test_guard_unlimited_plan_never_blocks(enforce):
    conn = FakeConn(plan="free", count=500)
    await api_keys.assert_and_reserve_api_key_slot(conn, tenant_id="t1")


async def test_guard_none_plan_never_blocks(enforce):
    conn = FakeConn(plan=None, count=500)
    await api_keys.assert_and_reserve_api_key_slot(conn, tenant_id="t1")


async def test_guard_fails_open_on_plan_lookup_error(enforce):
    conn = ExplodingConn(plan="pro", count=99)
    # Plan lookup blows up -> allow the create (UX guard, not a security boundary).
    await api_keys.assert_and_reserve_api_key_slot(conn, tenant_id="t1")


# ─────────────────────────────────────────
# Agent caps
# ─────────────────────────────────────────

class TestAgentLimits:
    def test_self_hosted_is_one_agent_one_key(self):
        from services.entitlements import agent_limit_for_plan

        assert agent_limit_for_plan("self_hosted") == 1
        assert api_key_limit_for_plan("self_hosted") == 1

    def test_self_hosted_caps_ignore_env_overrides(self, monkeypatch):
        from services.entitlements import agent_limit_for_plan

        monkeypatch.setenv("MAX_AGENTS_SELF_HOSTED", "10")
        monkeypatch.setenv("API_KEY_LIMIT_SELF_HOSTED", "10")
        assert agent_limit_for_plan("self_hosted") == 1
        assert api_key_limit_for_plan("self_hosted") == 1

    def test_managed_plans_can_be_tuned_by_env(self, monkeypatch):
        from services.entitlements import agent_limit_for_plan

        assert agent_limit_for_plan("pro") == 2
        monkeypatch.setenv("MAX_AGENTS_PRO", "4")
        assert agent_limit_for_plan("pro") == 4
        assert api_key_limit_for_plan("pro") == 2  # the other cap is untouched

    def test_unknown_plan_is_uncapped(self):
        from services.entitlements import agent_limit_for_plan

        assert agent_limit_for_plan(None) is None
        assert agent_limit_for_plan("pending_checkout") is None
