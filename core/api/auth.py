import hmac
import os
import time
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, Depends, Request
from services.exceptions import (
    conflict,
    not_found,
    internal_error,
    unauthorized,
    forbidden,
    service_unavailable,
)
from pydantic import BaseModel, EmailStr
from services.auth import request_otp, verify_otp, _issue_tokens, email_exists
from services.api_keys import (
    assert_and_reserve_api_key_slot,
    count_active_api_keys,
    create_api_key_record,
    revoke_api_key_record,
)
from api.deps import get_tenant, require_dashboard_session
from api.utils import client_ip
from db.connection import get_pool
from services.rate_limiter import (
    RateLimiter,
    InMemoryStorage,
    RedisStorage,
    RateLimitStorage,
    SlidingWindowStrategy,
)
from services.event_write import write_event
from services.entitlements import is_self_hosted_deployment

router = APIRouter()
log = logging.getLogger(__name__)

# Per-email OTP request limits (shared via Redis in production — see _otp_storage)
_OTP_RATE_WINDOW_SEC = 15 * 60   # 15 minutes
_OTP_RATE_MAX = 10               # max requests per email per window
_VERIFY_OTP_RATE_MAX = 20        # max verify attempts per email/IP per window


def _otp_storage() -> RateLimitStorage:
    """
    Choose OTP rate-limit storage.

    OTP_RATE_LIMIT_STORAGE=redis|memory overrides the default.
    Default: redis when ENV=production (shared across uvicorn workers),
    memory otherwise (local dev + unit tests).
    """
    choice = (os.getenv("OTP_RATE_LIMIT_STORAGE") or "").strip().lower()
    if not choice:
        choice = (
            "redis" if os.getenv("ENV", "development") == "production" else "memory"
        )
    if choice == "redis":
        return RedisStorage(key_prefix="otp")
    if choice == "memory":
        return InMemoryStorage()
    log.warning(
        "Unknown OTP_RATE_LIMIT_STORAGE=%r; falling back to memory",
        choice,
    )
    return InMemoryStorage()


otp_limiter = RateLimiter(
    limit=_OTP_RATE_MAX,
    window_sec=_OTP_RATE_WINDOW_SEC,
    storage=_otp_storage(),
    strategy=SlidingWindowStrategy(),
    detail="Too many code requests. Wait 15 minutes and try again."
)

verify_otp_limiter = RateLimiter(
    limit=_VERIFY_OTP_RATE_MAX,
    window_sec=_OTP_RATE_WINDOW_SEC,
    storage=_otp_storage(),
    strategy=SlidingWindowStrategy(),
    detail="Too many verification attempts. Wait 15 minutes and try again.",
)


_DEV_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_DEV_USER_ID   = "00000000-0000-0000-0000-000000000001"
_DEV_EMAIL     = "dev@localhost"


class RequestOTPBody(BaseModel):
    email: EmailStr
    intent: Literal["signup", "login"] = "login"


class VerifyOTPBody(BaseModel):
    email: EmailStr
    otp: str
    intent: Literal["signup", "login"] = "login"
    gdpr_consent: bool | None = None
    marketing_consent: bool | None = None


class CreateAPIKeyBody(BaseModel):
    name: str | None = None


@router.post("/request-otp")
async def request_otp_route(body: RequestOTPBody):
    email = body.email.lower().strip()
    try:
        await otp_limiter.check(email)
    except HTTPException:
        raise
    except Exception:
        # Fail closed: do not allow unlimited OTP sends if Redis is down.
        log.exception("OTP rate limit backend unavailable")
        raise service_unavailable(
            "Login temporarily unavailable. Please try again shortly."
        )

    if body.intent == "signup" and await email_exists(email):
        raise conflict(
            "This email is already registered. Please sign in instead."
        )

    if body.intent == "login" and not await email_exists(email):
        raise not_found(
            "No account found for this email. Create an account to get started."
        )

    try:
        await request_otp(email)
        return {"message": "Code sent"}
    except Exception as e:
        log.error(f"request_otp failed for {email}: {e}")
        raise internal_error("Failed to send code. Check server logs.")


@router.post("/verify-otp")
async def verify_otp_route(body: VerifyOTPBody, response: Response, request: Request):
    email = body.email.lower().strip()
    ip = client_ip(request)
    try:
        await verify_otp_limiter.check(f"verify:{email}")
        await verify_otp_limiter.check(f"verify-ip:{ip}")
    except HTTPException:
        raise
    except Exception:
        log.exception("verify-otp rate limit backend unavailable")
        raise service_unavailable(
            "Login temporarily unavailable. Please try again shortly."
        )

    try:
        tokens = await verify_otp(
            email,
            body.otp,
            intent=body.intent,
            gdpr_consent=body.gdpr_consent,
            marketing_consent=body.marketing_consent,
        )
    except ValueError as e:
        raise unauthorized(str(e))
    except Exception as e:
        log.exception("verify_otp failed for %s: %s", email, e)
        raise internal_error(
            "Could not complete account setup. Please request a new code and try again."
        )

    from services.billing import billing_status_payload, fetch_user_billing

    billing = billing_status_payload(None)
    try:
        billing_row = await fetch_user_billing(email=email)
        billing = billing_status_payload(billing_row)
    except Exception as e:
        log.warning("billing status lookup failed after verify for %s: %s", email, e)

    response.set_cookie(
        key="refresh_token",
        value=tokens["refresh_token"],
        httponly=True,
        secure=os.getenv("ENV", "development") == "production",
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )

    return {
        "access_token": tokens["access_token"],
        "token_type": "bearer",
        "requires_plan_selection": billing["requires_plan_selection"],
        "requires_checkout": billing["requires_checkout"],
        "has_access": billing["has_access"],
        "plan": billing["plan"],
    }


@router.post("/api-keys")
async def create_api_key(
    body: CreateAPIKeyBody,
    session: dict = Depends(require_dashboard_session),
):
    """Unassigned key: binds to the first agent that uses it. Per-agent keys: POST /v1/agents/{id}/api-keys."""
    pool = get_pool()
    tenant_id = session["tenant_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await assert_and_reserve_api_key_slot(conn, tenant_id=tenant_id)
            return await create_api_key_record(
                conn,
                tenant_id=tenant_id,
                name=body.name,
                agent_id=None,
            )


@router.get("/api-keys/usage")
async def api_keys_usage(session: dict = Depends(require_dashboard_session)):
    """Account-wide API key quota for the current plan.

    Returns unlimited whenever enforcement is off or the plan is uncapped, so the
    dashboard UI stays consistent with the backend guard. Fail-open on lookup error.
    """
    from services.billing import fetch_effective_plan
    from services.entitlements import api_key_limit_for_plan, limits_enforced

    pool = get_pool()
    tenant_id = session["tenant_id"]
    used = await count_active_api_keys(pool, tenant_id)

    plan: str | None = None
    limit: int | None = None
    if limits_enforced():
        try:
            plan = await fetch_effective_plan(pool, tenant_id)
            limit = api_key_limit_for_plan(plan)
        except Exception as e:
            log.warning("api-key usage: plan lookup failed for tenant %s: %s", tenant_id, e)
            limit = None

    unlimited = limit is None
    return {
        "plan": plan,
        "limit": limit,
        "used": used,
        "unlimited": unlimited,
        "at_limit": (not unlimited) and used >= limit,
    }


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    tenant: dict = Depends(require_dashboard_session),
):
    """Revoke an API key. The key stops working immediately."""
    pool = get_pool()
    if not await revoke_api_key_record(pool, tenant["tenant_id"], key_id):
        raise not_found("API key not found")
    return {"revoked": True, "key_id": key_id}


@router.post("/dev-token")
async def dev_token_route():
    """
    Only works when ENV=development (self-hosted local mode).
    Issues a real JWT for a fixed local dev user so the dashboard
    is accessible without email / signup.
    """
    if os.getenv("ENV", "development") != "development":
        raise forbidden("Not available in production")

    pool = get_pool()
    await _ensure_dev_tenant(pool)
    tokens = _issue_tokens(_DEV_USER_ID, _DEV_EMAIL, _DEV_TENANT_ID)
    return {"access_token": tokens["access_token"], "token_type": "bearer"}


SelfHostLoginMode = Literal["one_click", "admin_token", "unavailable"]


def _selfhost_login_mode() -> SelfHostLoginMode:
    """
    How the self-hosted dashboard signs in. Fails closed: a production
    instance without SELFHOST_ADMIN_TOKEN gets no login at all, never one-click.
    """
    if os.getenv("ENV", "development") == "development":
        return "one_click"
    if (os.getenv("SELFHOST_ADMIN_TOKEN") or "").strip():
        return "admin_token"
    return "unavailable"


class SelfHostLoginBody(BaseModel):
    token: str | None = None


selfhost_login_limiter = RateLimiter(
    limit=10,
    window_sec=60,
    storage=_otp_storage(),
    strategy=SlidingWindowStrategy(),
    detail="Too many sign-in attempts. Wait a minute and try again.",
)


def _selfhost_login_allowed() -> bool:
    """
    Self-host sign-in is offered on self-hosted deployments, and on any
    ENV=development instance (same rule as /dev-token, so local stacks whose
    .env predates DEPLOYMENT_MODE keep working). Managed production: never.
    """
    return is_self_hosted_deployment() or os.getenv("ENV", "development") == "development"


@router.get("/selfhost")
async def selfhost_config_route():
    """Public: tells the dashboard whether to show the self-host login and which kind."""
    if not _selfhost_login_allowed():
        return {"self_hosted": False, "login": None}
    return {"self_hosted": True, "login": _selfhost_login_mode()}


@router.post("/selfhost-login")
async def selfhost_login_route(request: Request, body: SelfHostLoginBody | None = None):
    """
    Sign in to a self-hosted instance as its single owner tenant — no email,
    no signup. One-click when ENV=development; otherwise requires
    SELFHOST_ADMIN_TOKEN. Uses the same fixed tenant as /dev-token so
    existing local data stays visible.
    """
    if not _selfhost_login_allowed():
        raise not_found("Not found")

    try:
        await selfhost_login_limiter.check(f"selfhost:{client_ip(request)}")
    except HTTPException:
        raise
    except Exception:
        log.exception("selfhost-login rate limit backend unavailable")
        raise service_unavailable("Login temporarily unavailable. Please try again shortly.")

    mode = _selfhost_login_mode()
    if mode == "unavailable":
        raise forbidden("Set SELFHOST_ADMIN_TOKEN in your .env to enable dashboard login.")
    if mode == "admin_token":
        expected = (os.getenv("SELFHOST_ADMIN_TOKEN") or "").strip()
        supplied = ((body.token if body else None) or "").strip()
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise unauthorized("Invalid admin token")

    pool = get_pool()
    await _ensure_dev_tenant(pool)
    tokens = _issue_tokens(_DEV_USER_ID, _DEV_EMAIL, _DEV_TENANT_ID)
    return {"access_token": tokens["access_token"], "token_type": "bearer"}


async def _ensure_dev_tenant(pool) -> None:
    """Idempotently create the dev tenant + user rows if they don't exist."""
    await pool.execute(
        """
        INSERT INTO tenants (tenant_id, name)
        VALUES ($1::uuid, 'Local Dev')
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        _DEV_TENANT_ID,
    )
    await pool.execute(
        """
        INSERT INTO users (user_id, email, tenant_id, last_login)
        VALUES ($1::uuid, $2::text, $3::uuid, NOW())
        ON CONFLICT (email) DO UPDATE SET last_login = NOW()
        """,
        _DEV_USER_ID, _DEV_EMAIL, _DEV_TENANT_ID,
    )


@router.post("/test-event")
async def test_event(tenant: dict = Depends(require_dashboard_session)):
    """
    Log a test event using the dashboard session (JWT).
    Verifies the tenant pipeline without an API key.
    """
    result = await write_event(
        tenant_id=tenant["tenant_id"],
        agent="dashboard-connection-test",
        event="connection_test",
        data={"source": "dashboard_settings", "ok": True},
    )
    return {
        **result,
        "message": "Test event recorded. Check Agents — dashboard-connection-test should appear within seconds.",
    }


@router.get("/api-keys")
async def list_api_keys(tenant: dict = Depends(require_dashboard_session)):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT key_id, key_prefix, name, agent_id, created_at, last_used
        FROM api_keys
        WHERE tenant_id = $1 AND revoked = FALSE
        ORDER BY created_at DESC
        """,
        tenant["tenant_id"],
    )
    return [
        {
            "key_id": str(r["key_id"]),
            "prefix": r["key_prefix"],
            "name": r["name"],
            "agent_id": r["agent_id"],
            "created_at": r["created_at"].isoformat(),
            "last_used": r["last_used"].isoformat() if r["last_used"] else None,
        }
        for r in rows
    ]
