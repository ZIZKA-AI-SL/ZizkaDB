import hashlib
import httpx
import json
import logging
from typing import Any, Awaitable, Callable

from db.connection import get_redis
from services.embedding_config import (
    EMBEDDING_MODELS,
    TenantEmbeddingConfig,
    VECTOR_SIZE,
    get_tenant_embedding_config,
    local_embeddings_settings,
)

logger = logging.getLogger(__name__)

CACHE_TTL = 86400  # 24 hours

API_KEY_REQUIRED_PROVIDERS = {
    "openai",
}

EmbeddingProvider = Callable[
    [str, TenantEmbeddingConfig],
    Awaitable[list[float] | None],
]


def _cache_key_for(provider: str, model: str, text: str) -> str:
    """Stable across processes (unlike builtin hash())."""
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    return f"emb:{provider}:{model}:{text_hash}"


async def generate_embedding(text: str, tenant_id: str) -> list[float] | None:
    config = await get_tenant_embedding_config(tenant_id)
    return await generate_embedding_with_config(text, config)


async def generate_embedding_with_config(
    text: str,
    config: TenantEmbeddingConfig,
) -> list[float] | None:
    if (
        config.provider in API_KEY_REQUIRED_PROVIDERS
        and not config.api_key
    ):
        logger.warning(
            "No embedding API key for tenant %s (platform=%s)",
            config.tenant_id,
            config.use_platform_key,
        )
        return None

    cache_key = _cache_key_for(config.provider, config.model, text)

    try:
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass


    provider = _get_embedding_provider(config.provider)
    if provider is None:
        logger.warning(
            "Unsupported embedding provider: %s",
            config.provider,
        )

        return None

    try:
        embedding = await provider(text, config)

        if not embedding:
            return None

        if len(embedding) != VECTOR_SIZE:
            logger.warning(
                "Embedding dimension %s != %s for tenant %s",
                len(embedding),
                VECTOR_SIZE,
                config.tenant_id,
            )
            return None

        try:
            redis = get_redis()
            await redis.setex(
                cache_key,
                CACHE_TTL,
                json.dumps(embedding),
            )
        except Exception:
            pass

        return embedding

    except Exception as e:
        logger.warning(
            "Embedding generation failed for tenant %s: %s",
            config.tenant_id,
            e,
        )
        return None


async def _openai_embedding(
    text: str,
    config: TenantEmbeddingConfig,
) -> list[float] | None:
    payload: dict = {
        "input": text,
        "model": config.model,
    }

    for model in EMBEDDING_MODELS.get("openai", []):
        if (
            model["id"] == config.model
            and model.get("dimensions_param") is not None
        ):
            payload["dimensions"] = model["dimensions_param"]
            break

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {config.api_key}",
            },
            json=payload,
        )

        response.raise_for_status()

        return response.json()["data"][0]["embedding"]



async def _local_embedding(
    text: str,
    config: TenantEmbeddingConfig,
) -> list[float] | None:
    settings = local_embeddings_settings()
    if not settings:
        return None
    headers = {"Authorization": f"Bearer {settings['api_key']}"} if settings["api_key"] else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings['url']}/embeddings",
            headers=headers,
            json={"input": text, "model": config.model},
        )
        response.raise_for_status()
        embedding = response.json()["data"][0]["embedding"]
    if len(embedding) > VECTOR_SIZE:
        logger.warning(
            "Local embedding model %s returns %s dims; at most %s are supported",
            config.model, len(embedding), VECTOR_SIZE,
        )
        return None
    # Zero padding keeps dot products and norms, so cosine similarity is unchanged.
    return list(embedding) + [0.0] * (VECTOR_SIZE - len(embedding))


# Map provider id -> the module-level function that implements it.
# The function is resolved from module globals at call time rather
# than storing a direct function reference. This allows monkeypatching
# provider implementations in tests without leaving stale bindings.
_EMBEDDING_PROVIDER_NAMES: dict[str, str] = {
    "openai": "_openai_embedding",
    "local": "_local_embedding",
}


def _get_embedding_provider(
    provider: str,
) -> EmbeddingProvider | None:
    fn_name = _EMBEDDING_PROVIDER_NAMES.get(provider)
    if fn_name is None:
        return None

    provider_fn = globals().get(fn_name)
    if provider_fn is None:
        return None

    return provider_fn

def _flatten_data(value: Any, prefix: str = "") -> list[str]:
    parts: list[str] = []

    if isinstance(value, dict):
        for key, item in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            parts.extend(_flatten_data(item, new_prefix))

    elif isinstance(value, list):
        for index, item in enumerate(value):
            new_prefix = f"{prefix}.{index}" if prefix else str(index)
            parts.extend(_flatten_data(item, new_prefix))

    elif isinstance(value, (str, int, float, bool)):
        parts.append(f"{prefix}:{value}")

    return parts


def event_to_text(event_type: str, data: dict) -> str:
    parts = [f"event_type:{event_type}"]
    parts.extend(_flatten_data(data))
    return " ".join(parts)
