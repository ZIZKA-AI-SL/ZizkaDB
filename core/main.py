from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from contextlib import asynccontextmanager
import logging
import os

from db.connection import init_db, close_db, get_pool, check_postgres, check_redis, check_qdrant
from api.auth import _ensure_dev_tenant
from api.events import router as events_router
from api.sessions import router as sessions_router
from api.agents import router as agents_router
from api.auth import router as auth_router
from api.a2a import router as a2a_router
from api.search import router as search_router
from api.memory import router as memory_router
from api.telemetry import router as telemetry_router
from api.billing_checkout import router as billing_checkout_router
from api.settings import router as settings_router
from api.account import router as account_router
from api.demo_requests import router as demo_requests_router
from api.community import router as community_router
from api.marketing_subscriptions import router as marketing_subscriptions_router
from middleware.request_id import RequestIdMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Display/API release line — keep in sync with zizkadb-sdk (see scripts/check-doc-drift.sh).
API_VERSION = "0.2.8"


def warn_if_production_cors_wildcard(cors_allowed_origins: list[str]) -> None:
    """Log when production runs with default wildcard CORS."""
    if not cors_allowed_origins:
        logger.warning(
            "CORS_ALLOWED_ORIGINS is unset in production — allowing all origins (*). "
            "Set CORS_ALLOWED_ORIGINS to your dashboard origin(s) for browser security."
        )


_DEFAULT_DEV_KEYS = frozenset({"zizkadb_dev_local", "agdb_dev_local"})
_DEFAULT_JWT_SECRETS = frozenset({"", "dev-secret-change-in-production"})


def validate_production_startup(
    env: str,
    dev_key: str,
    jwt_secret: str,
) -> None:
    """Refuse production boot with known-insecure defaults."""
    if env != "production":
        return
    if not dev_key or dev_key in _DEFAULT_DEV_KEYS:
        raise RuntimeError(
            "Refusing to start with ENV=production and a dev/default DEV_API_KEY. "
            "Unset DEV_API_KEY or set a unique secret in infra/.env."
        )
    if jwt_secret in _DEFAULT_JWT_SECRETS:
        raise RuntimeError(
            "Refusing to start with ENV=production and default JWT_SECRET. "
            "Set a strong JWT_SECRET in infra/.env."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    env = os.getenv("ENV", "development")
    if env == "production":
        validate_production_startup(
            env,
            os.getenv("DEV_API_KEY", ""),
            os.getenv("JWT_SECRET", ""),
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
            "DEV MODE: hardcoded dev API keys (zizkadb_dev_local) are accepted. "
            "Set ENV=production for any public deployment."
        )
        await _ensure_dev_tenant(get_pool())
    logger.info("ZizkaDB started")
    yield
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

app.include_router(auth_router,      prefix="/v1/auth",      tags=["auth"])
app.include_router(events_router,    prefix="/v1/events",    tags=["events"])
app.include_router(sessions_router,  prefix="/v1/sessions",  tags=["sessions"])
app.include_router(agents_router,    prefix="/v1/agents",    tags=["agents"])
app.include_router(a2a_router,       prefix="/v1/a2a",       tags=["a2a"])
app.include_router(search_router,    prefix="/v1/search",    tags=["search"])
app.include_router(memory_router,    prefix="/v1/memory",    tags=["memory"])
app.include_router(telemetry_router, prefix="/v1/telemetry", tags=["telemetry"])
app.include_router(billing_checkout_router, prefix="/v1/billing", tags=["billing"])
app.include_router(settings_router,  prefix="/v1/settings",  tags=["settings"])
app.include_router(account_router,   prefix="/v1/account",   tags=["account"])
app.include_router(demo_requests_router, prefix="/v1/demo-requests", tags=["demo"])
app.include_router(community_router, prefix="/v1/community", tags=["community"])
app.include_router(marketing_subscriptions_router, prefix="/v1/marketing-subscriptions", tags=["marketing"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": API_VERSION}


@app.get("/health/deep")
async def health_deep():
    checks = {
        "postgres": await check_postgres(),
        "redis": await check_redis(),
        "qdrant": await check_qdrant(),
    }
    status = "ok" if all(check.get("ok") for check in checks.values()) else "degraded"
    return {"status": status, "checks": checks}
