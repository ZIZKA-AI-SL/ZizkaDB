from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from contextlib import asynccontextmanager
import asyncio
import contextlib
import logging
import os

from db.connection import init_db, close_db, get_pool, check_postgres, check_redis, check_qdrant
from api.auth import _ensure_dev_tenant
from api.events import router as events_router
from api.sessions import router as sessions_router
from api.agents import router as agents_router
from api.auth import router as auth_router
from api.search import router as search_router
from api.memory import router as memory_router
from api.telemetry import router as telemetry_router
from api.billing_checkout import router as billing_checkout_router
from api.settings import router as settings_router
from api.account import router as account_router
from api.demo_requests import router as demo_requests_router
from api.community import router as community_router
from api.marketing_subscriptions import router as marketing_subscriptions_router
from api.metrics import router as metrics_router
from middleware.body_limit import BodyLimitMiddleware
from middleware.request_id import RequestIdMiddleware
from services.runtime_env import env_name, is_dev_mode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Display/API release line — keep in sync with zizkadb-sdk (see scripts/check-doc-drift.sh).
API_VERSION = "0.6.0"


def warn_if_production_cors_wildcard(cors_allowed_origins: list[str]) -> None:
    """Log when production runs with default wildcard CORS."""
    if not cors_allowed_origins:
        logger.warning(
            "CORS_ALLOWED_ORIGINS is unset in production — allowing all origins (*). "
            "Set CORS_ALLOWED_ORIGINS to your dashboard origin(s) for browser security."
        )


_DEFAULT_DEV_KEYS = frozenset({"zizkadb_dev_local", "agdb_dev_local"})
# Values published in .env.example, Compose defaults, auth.py dev fallbacks and
# the dashboard docs. Keep in sync with scripts/validate-selfhost-config.sh and
# scripts/init-secrets.sh; core/tests/test_production_startup_guards.py checks
# all three against the repo.
_DEFAULT_JWT_SECRETS = frozenset(
    {
        "",
        "dev-secret",
        "dev-refresh-secret",
        "dev-secret-change-in-production",
        "dev-refresh-secret-change-in-production",
        "change-this-to-a-random-32-char-string",
        "change-this-to-another-random-32-char-string",
        "your-random-32-char-secret",
    }
)
# Self-host dashboard admin token: the login rate limit keys on client IP, which
# a client can vary, so the token itself must resist brute force.
_MIN_SELFHOST_ADMIN_TOKEN_LEN = 16


def validate_production_startup(
    env: str,
    dev_key: str,
    jwt_secret: str,
    selfhost_admin_token: str = "",
    jwt_refresh_secret: str = "",
) -> None:
    """Refuse production boot with known-insecure defaults.

    Production is every ``ENV`` other than ``development`` (see
    ``services.runtime_env``), so an unset, misspelt or ``staging`` value is
    checked too.
    """
    if env.strip().lower() == "development":
        return
    # Dev keys are never accepted outside development, so an empty DEV_API_KEY
    # is fine; a known default is still refused as a sign of a copied dev config.
    if dev_key in _DEFAULT_DEV_KEYS:
        raise RuntimeError(
            "Refusing to start in production mode (ENV is not 'development') with a "
            "dev/default DEV_API_KEY. Unset DEV_API_KEY or set a unique secret in "
            "infra/.env. For local development set ENV=development."
        )
    # Compare without surrounding whitespace, so a padded sample or one with a CR
    # from a CRLF-edited .env is still refused.
    if (
        jwt_secret.strip() in _DEFAULT_JWT_SECRETS
        or jwt_refresh_secret.strip() in _DEFAULT_JWT_SECRETS
    ):
        raise RuntimeError(
            "Refusing to start in production mode (ENV is not 'development') with a "
            "published or default JWT secret. Set strong JWT_SECRET and "
            "JWT_REFRESH_SECRET in infra/.env. For local development set "
            "ENV=development."
        )
    token = selfhost_admin_token.strip()
    if token and len(token) < _MIN_SELFHOST_ADMIN_TOKEN_LEN:
        raise RuntimeError(
            f"Refusing to start: SELFHOST_ADMIN_TOKEN is shorter than "
            f"{_MIN_SELFHOST_ADMIN_TOKEN_LEN} characters. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not is_dev_mode():
        validate_production_startup(
            env_name(),
            os.getenv("DEV_API_KEY", ""),
            os.getenv("JWT_SECRET", ""),
            os.getenv("SELFHOST_ADMIN_TOKEN", ""),
            os.getenv("JWT_REFRESH_SECRET", ""),
        )
        from services.entitlements import limits_enforced

        if not limits_enforced():
            logger.warning(
                "API_KEY_LIMITS_ENFORCED is false in production — per-plan API key "
                "caps are not enforced until you enable the kill switch."
            )
        if not _cors_allowed_origins:
            warn_if_production_cors_wildcard(_cors_allowed_origins)
    await init_db()
    # Seed dev tenant only when dev keys are actually accepted (ENV=development).
    from api.deps import dev_keys_enabled

    if dev_keys_enabled():
        logger.warning(
            "DEV MODE (ENV=development): hardcoded dev API keys (zizkadb_dev_local) "
            "are accepted and /v1/auth/dev-token issues tokens without a login. "
            "Never use ENV=development on a reachable deployment."
        )
        await _ensure_dev_tenant(get_pool())
    from services.retention import retention_loop

    retention_task = asyncio.create_task(retention_loop(get_pool))
    logger.info("ZizkaDB started")
    yield
    retention_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await retention_task
    await close_db()
    logger.info("ZizkaDB stopped")


app = FastAPI(
    title="ZizkaDB",
    description="The operational database for AI agents",
    version=API_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)


def _encodable(value):
    """Return *value* with every string made safe to encode as UTF-8.

    A JSON body can carry lone UTF-16 surrogates (e.g. ``\\ud800``), which
    pydantic rejects — but the offending value is echoed back in the error
    detail, and encoding that response as UTF-8 raises UnicodeEncodeError.
    ``backslashreplace`` renders such characters as literal ``\\ud800``
    escapes, so the 422 body can always be encoded.
    """
    if isinstance(value, str):
        return value.encode("utf-8", "backslashreplace").decode("utf-8")
    if isinstance(value, dict):
        return {_encodable(k): _encodable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encodable(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """422 for invalid requests — never a 500 from an unencodable body.

    FastAPI's default handler echoes the rejected ``input`` in the error
    detail; when that input contains lone surrogates the response itself
    fails to encode and the client sees a 500 for what is a client error.
    Sanitise the errors before building the response (issue #301).
    """
    errors = _encodable(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/swagger", include_in_schema=False)
async def swagger_ui():
    """
    Swagger UI without the visible /openapi.json link under the title.
    The schema remains available at /openapi.json for tools that need it.
    """
    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
    )
    body = html.body.decode("utf-8")

    css = """
    <style>
      .swagger-ui .topbar .download-url-wrapper { display: none !important; }
      .swagger-ui .info hgroup.main a { display: none !important; }
      .swagger-ui .info .link { display: none !important; }
      .swagger-ui .info a[href*="openapi.json"] { display: none !important; }
    </style>
    """

    hide_url_plugin = """
    const HideInfoUrlPlugin = () => ({
      wrapComponents: {
        InfoUrl: () => () => null,
      },
    });
    """

    body = body.replace(
        "const ui = SwaggerUIBundle({",
        hide_url_plugin + "\n    const ui = SwaggerUIBundle({\n        plugins: [HideInfoUrlPlugin],",
        1,
    )

    return HTMLResponse(body.replace("</head>", f"{css}</head>"))


@app.get("/api-explorer", include_in_schema=False)
async def api_explorer_redirect():
    """Legacy URL; nginx may mis-route this unless ^~ /api-explorer is configured."""
    return RedirectResponse(url="/swagger")

_cors_allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins or ["*"],
    allow_credentials=bool(_cors_allowed_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestIdMiddleware)
app.add_middleware(BodyLimitMiddleware)

app.include_router(auth_router,      prefix="/v1/auth",      tags=["auth"])
app.include_router(events_router,    prefix="/v1/events",    tags=["events"])
app.include_router(sessions_router,  prefix="/v1/sessions",  tags=["sessions"])
app.include_router(agents_router,    prefix="/v1/agents",    tags=["agents"])
app.include_router(search_router,    prefix="/v1/search",    tags=["search"])
app.include_router(memory_router,    prefix="/v1/memory",    tags=["memory"])
app.include_router(telemetry_router, prefix="/v1/telemetry", tags=["telemetry"])
app.include_router(billing_checkout_router, prefix="/v1/billing", tags=["billing"])
app.include_router(settings_router,  prefix="/v1/settings",  tags=["settings"])
app.include_router(account_router,   prefix="/v1/account",   tags=["account"])
app.include_router(demo_requests_router, prefix="/v1/demo-requests", tags=["demo"])
app.include_router(community_router, prefix="/v1/community", tags=["community"])
app.include_router(marketing_subscriptions_router, prefix="/v1/marketing-subscriptions", tags=["marketing"])
app.include_router(metrics_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": API_VERSION}


@app.get("/health/deep")
async def health_deep():
    """Per-subsystem health. 503 when a required subsystem is down, so
    orchestrators that gate on status code see the failure. Qdrant is only
    required when embeddings are enabled."""
    from services.entitlements import embeddings_enabled

    checks = {
        "postgres": {**await check_postgres(), "required": True},
        "redis": {**await check_redis(), "required": True},
        "qdrant": {**await check_qdrant(), "required": embeddings_enabled()},
    }
    healthy = all(c.get("ok") for c in checks.values() if c["required"])
    body = {"status": "ok" if healthy else "degraded", "checks": checks}
    return JSONResponse(content=body, status_code=200 if healthy else 503)
