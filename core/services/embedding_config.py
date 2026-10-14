"""Embedding provider catalog and per-tenant configuration."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from db.connection import get_pool
from services.runtime_env import is_dev_mode

# All v1 models use 1536 dims (matches Postgres vector(1536) + Qdrant collection).
EMBEDDING_MODELS: dict[str, list[dict]] = {
    "openai": [
        {
            "id": "text-embedding-3-small",
            "label": "OpenAI text-embedding-3-small",
            "dimensions": 1536,
            "description": "Default — fast and cost-effective",
        },
        {
            "id": "text-embedding-3-large",
            "label": "OpenAI text-embedding-3-large (1536 dims)",
            "dimensions": 1536,
            "description": "Higher quality; stored at 1536 dimensions for compatibility",
            "dimensions_param": 1536,
        },
        {
            "id": "text-embedding-ada-002",
            "label": "OpenAI text-embedding-ada-002",
            "dimensions": 1536,
            "description": "Legacy OpenAI embedding model",
        },
    ],
}

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536


@dataclass
class TenantEmbeddingConfig:
    tenant_id: str
    provider: str
    model: str
    use_platform_key: bool
    api_key: str | None  # resolved secret for API calls


def list_catalog() -> dict:
    return {
        "providers": [
            {
                "id": provider,
                "models": models,
            }
            for provider, models in EMBEDDING_MODELS.items()
        ],
        "default": {"provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL},
        "note": (
            "Changing models does not re-embed existing events. "
            "New events use the selected model; search works best when one model is used consistently."
        ),
    }


def validate_provider_model(provider: str, model: str) -> None:
    models = EMBEDDING_MODELS.get(provider)
    if not models:
        raise ValueError(f"Unknown embedding provider: {provider}")
    if not any(m["id"] == model for m in models):
        raise ValueError(f"Unknown model {model} for provider {provider}")


def model_dimensions(provider: str, model: str) -> int:
    validate_provider_model(provider, model)
    for m in EMBEDDING_MODELS[provider]:
        if m["id"] == model:
            return int(m["dimensions"])
    return VECTOR_SIZE


def _fernet() -> Fernet:
    """
    Derive the Fernet key used to encrypt per-tenant BYOK embedding API keys.

    Falls back to JWT_SECRET when EMBEDDING_ENCRYPTION_KEY is unset, matching
    .env.example. In development this also falls back to a fixed insecure
    string so local self-host works out of the box without any env setup.

    In production (ENV != "development"), neither secret being set is a
    misconfiguration, not something to silently paper over: the hardcoded
    fallback string is visible in this public repository, so encrypting
    tenant API keys with it would mean every BYOK key is effectively
    unencrypted. Refuse to start serving embeddings in that case.
    """
    secret = os.getenv("EMBEDDING_ENCRYPTION_KEY") or os.getenv("JWT_SECRET")
    if not secret:
        if not is_dev_mode():
            raise RuntimeError(
                "EMBEDDING_ENCRYPTION_KEY or JWT_SECRET must be set in production. "
                "Refusing to encrypt tenant API keys with a hardcoded fallback "
                "value, since that value is visible in the public source code."
            )
        secret = "dev-insecure-key"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_api_key(raw: str) -> str:
    return _fernet().encrypt(raw.strip().encode()).decode()


def decrypt_api_key(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Stored API key could not be decrypted")


def _resolve_api_key(use_platform: bool, encrypted: str | None) -> str | None:
    if use_platform:
        return os.getenv("OPENAI_API_KEY") or None
    if not encrypted:
        return None
    return decrypt_api_key(encrypted)


async def get_tenant_embedding_config(tenant_id: str) -> TenantEmbeddingConfig:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT embedding_provider, embedding_model,
               embedding_use_platform_key, embedding_api_key_encrypted
        FROM tenants
        WHERE tenant_id = $1::uuid
        """,
        tenant_id,
    )
    if not row:
        return TenantEmbeddingConfig(
            tenant_id=tenant_id,
            provider=DEFAULT_PROVIDER,
            model=DEFAULT_MODEL,
            use_platform_key=True,
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    provider = row["embedding_provider"] or DEFAULT_PROVIDER
    model = row["embedding_model"] or DEFAULT_MODEL
    use_platform = row["embedding_use_platform_key"]
    if use_platform is None:
        use_platform = True

    return TenantEmbeddingConfig(
        tenant_id=tenant_id,
        provider=provider,
        model=model,
        use_platform_key=bool(use_platform),
        api_key=_resolve_api_key(bool(use_platform), row["embedding_api_key_encrypted"]),
    )


async def update_tenant_embedding_config(
    tenant_id: str,
    *,
    provider: str,
    model: str,
    use_platform_key: bool,
    api_key: str | None = None,
) -> TenantEmbeddingConfig:
    validate_provider_model(provider, model)
    if model_dimensions(provider, model) != VECTOR_SIZE:
        raise ValueError(f"Model dimension must be {VECTOR_SIZE} in this release")

    encrypted = None
    if not use_platform_key:
        if not api_key or not api_key.strip():
            raise ValueError("API key required when not using Zizka platform embeddings")
        encrypted = encrypt_api_key(api_key)

    pool = get_pool()
    await pool.execute(
        """
        UPDATE tenants
        SET embedding_provider = $2,
            embedding_model = $3,
            embedding_use_platform_key = $4,
            embedding_api_key_encrypted = CASE
                WHEN $4 THEN NULL
                ELSE $5
            END
        WHERE tenant_id = $1::uuid
        """,
        tenant_id,
        provider,
        model,
        use_platform_key,
        encrypted,
    )
    return await get_tenant_embedding_config(tenant_id)


def embedding_config_for_response(config: TenantEmbeddingConfig) -> dict:
    return {
        "provider": config.provider,
        "model": config.model,
        "use_platform_key": config.use_platform_key,
        "has_custom_api_key": not config.use_platform_key,
        "platform_key_available": bool(os.getenv("OPENAI_API_KEY")),
        "ready": bool(config.api_key),
    }
