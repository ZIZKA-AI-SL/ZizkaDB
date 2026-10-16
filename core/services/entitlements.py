"""Single source of truth for per-plan entitlements (limits + future feature access).

Only the plans listed in ``PLAN_ENTITLEMENTS`` are capped. Anything else
(unknown, ``pending_checkout``, ``None``) is treated as unlimited.

To change a plan's limit:
  - Quick/no-deploy: set ``API_KEY_LIMIT_<PLAN>`` (e.g. ``API_KEY_LIMIT_PRO=5``)
    and restart. Overrides the default below for that one plan.
  - Permanent: edit the value in ``PLAN_ENTITLEMENTS`` below. This is the only
    file that needs to change — no other business logic references the
    numbers directly.

Adding a new capped resource (e.g. max agents) is a one-field change to
``PlanEntitlements`` plus a value per plan below — no other business logic
needs to change. Adding a new plan is a one-line dict entry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ``None`` means "no cap".
UNLIMITED: int | None = None


@dataclass(frozen=True)
class PlanEntitlements:
    max_api_keys: int | None = UNLIMITED
    max_agents: int | None = UNLIMITED
    # Future limits (max_team_members, max_events_per_month, max_storage_mb,
    # data_retention_days, features: frozenset[str]) go here.


# Self-hosted (open source) is one agent with one API key. Running several
# agents is a ZizkaDB Cloud feature.
SELF_HOSTED_PLAN = "self_hosted"

# The only plans with entitlement caps. Extend this dict to add plans.
PLAN_ENTITLEMENTS: dict[str, PlanEntitlements] = {
    SELF_HOSTED_PLAN: PlanEntitlements(max_api_keys=1, max_agents=1),
    "pro": PlanEntitlements(max_api_keys=2, max_agents=2),
    "team": PlanEntitlements(max_api_keys=5, max_agents=5),
}


def _max_api_keys_override(plan: str) -> int | None:
    """Optional env-var override, e.g. ``API_KEY_LIMIT_PRO=5``.

    Lets ops tune a single plan's cap via env (no code change, no redeploy —
    just a restart). Unset/blank/non-integer falls back to the
    ``PLAN_ENTITLEMENTS`` default above.
    """
    raw = os.getenv(f"API_KEY_LIMIT_{plan.upper()}", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _max_agents_override(plan: str) -> int | None:
    """Optional env-var override, e.g. ``MAX_AGENTS_PRO=3``. Same shape as
    ``_max_api_keys_override`` above."""
    raw = os.getenv(f"MAX_AGENTS_{plan.upper()}", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def entitlements_for_plan(plan: str | None) -> PlanEntitlements:
    """Return the entitlements for ``plan``, or the unlimited default."""
    if not plan:
        return PlanEntitlements()
    key = plan.strip().lower()
    base = PLAN_ENTITLEMENTS.get(key, PlanEntitlements())
    # The self-hosted caps are part of the edition, not tunable per install.
    if key == SELF_HOSTED_PLAN:
        return base
    api_keys_override = _max_api_keys_override(key)
    agents_override = _max_agents_override(key)
    if api_keys_override is None and agents_override is None:
        return base
    return PlanEntitlements(
        max_api_keys=api_keys_override if api_keys_override is not None else base.max_api_keys,
        max_agents=agents_override if agents_override is not None else base.max_agents,
    )


def api_key_limit_for_plan(plan: str | None) -> int | None:
    """Return the active-API-key cap for ``plan``, or ``None`` for unlimited."""
    return entitlements_for_plan(plan).max_api_keys


def agent_limit_for_plan(plan: str | None) -> int | None:
    """Return the agent cap for ``plan``, or ``None`` for unlimited."""
    return entitlements_for_plan(plan).max_agents


def limits_enforced() -> bool:
    """Whether plan caps (API keys, agents) are enforced.

    Self-hosted: always. Managed: the ``API_KEY_LIMITS_ENFORCED`` kill switch,
    which defaults OFF so caps can be enabled per environment after measuring
    how many existing tenants already exceed their plan limit.
    """
    if is_self_hosted_deployment():
        return True
    return os.getenv("API_KEY_LIMITS_ENFORCED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_self_hosted_deployment() -> bool:
    """Whether this backend instance is running as a self-hosted deployment.

    Self-host installs also set ``ENV=production`` (see wiki/Self-Hosting.md)
    so ``ENV`` alone can't distinguish them from managed cloud — this is a
    separate, explicit knob self-host configs set so entitlement checks can
    resolve their plan to ``"self_hosted"`` without trusting ``users.plan``
    (which self-host installs never populate).
    """
    return os.getenv("DEPLOYMENT_MODE", "managed").strip().lower() == SELF_HOSTED_PLAN


def embeddings_enabled() -> bool:
    """Whether event indexing and semantic search may call an embedding provider.

    Managed cloud: enabled (tenant/platform embedding config applies).
    Self-hosted OSS: disabled unless ``EMBEDDINGS_ENABLED=true`` — avoids
    silently using the operator's OpenAI key or platform defaults.
    """
    if not is_self_hosted_deployment():
        return True
    return os.getenv("EMBEDDINGS_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
