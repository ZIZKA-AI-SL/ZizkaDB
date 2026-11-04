import hashlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.embedding_config import TenantEmbeddingConfig
from services.embeddings import event_to_text, generate_embedding_with_config

CORE_DIR = Path(__file__).resolve().parents[1]


def test_event_to_text_includes_primitives():
    text = event_to_text(
        "tool_call",
        {
            "tool": "weather",
            "temperature": 22,
            "success": True,
        },
    )

    assert "event_type:tool_call" in text
    assert "tool:weather" in text
    assert "temperature:22" in text
    assert "success:True" in text


def test_event_to_text_nested_dict():
    text = event_to_text(
        "tool_call",
        {
            "arguments": {
                "city": "Paris",
                "country": "France",
            }
        },
    )

    assert "arguments.city:Paris" in text
    assert "arguments.country:France" in text


def test_event_to_text_lists():
    text = event_to_text(
        "retrieval",
        {
            "documents": [
                "doc1",
                "doc2",
            ]
        },
    )

    assert "documents.0:doc1" in text
    assert "documents.1:doc2" in text


def _cache_key_in_subprocess(text: str) -> str:
    """Run the cache-key computation in a fresh interpreter so PYTHONHASHSEED
    randomization (which varies builtin hash() per process, but not
    hashlib.sha256()) is actually exercised, the way separate uvicorn
    workers would see it."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from services.embeddings import _cache_key_for; "
        "print(_cache_key_for('openai', 'text-embedding-3-small', %r))"
    ) % (str(CORE_DIR), text)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_embedding_cache_key_stable_across_processes():
    """Regression test for the cache-key bug called out in core/CLAUDE.md:
    using builtin hash(text) instead of hashlib.sha256 makes the Redis
    embedding cache key differ per worker process (PYTHONHASHSEED is
    randomized per process by default), so cache reads across workers
    almost always miss. sha256 must be stable regardless of process."""
    key_a = _cache_key_in_subprocess("hello world")
    key_b = _cache_key_in_subprocess("hello world")

    assert key_a == key_b
    expected_hash = hashlib.sha256("hello world".encode()).hexdigest()
    assert expected_hash in key_a


@pytest.mark.asyncio
async def test_generate_embedding_uses_sha256_cache_key(monkeypatch):
    """The cache key written to/read from Redis must be derived from
    hashlib.sha256, not the process-unstable builtin hash()."""
    config = TenantEmbeddingConfig(
        tenant_id="tenant1",
        provider="openai",
        model="text-embedding-3-small",
        use_platform_key=True,
        api_key="sk-test",
    )

    redis = AsyncMock()
    redis.get.return_value = None
    monkeypatch.setattr("services.embeddings.get_redis", lambda: redis)
    monkeypatch.setattr(
        "services.embeddings._openai_embedding",
        AsyncMock(return_value=[0.1] * 1536),
    )

    await generate_embedding_with_config("hello world", config)

    expected_hash = hashlib.sha256("hello world".encode()).hexdigest()
    get_key = redis.get.call_args[0][0]
    setex_key = redis.setex.call_args[0][0]

    assert expected_hash in get_key
    assert get_key == setex_key

# ── raise_errors: the embed worker learns why there is no embedding ────────


def _config(**overrides):
    values = dict(
        tenant_id="tenant1",
        provider="openai",
        model="text-embedding-3-small",
        use_platform_key=True,
        api_key="sk-test",
    )
    values.update(overrides)
    return TenantEmbeddingConfig(**values)


@pytest.fixture
def no_cache(monkeypatch):
    redis = AsyncMock()
    redis.get.return_value = None
    monkeypatch.setattr("services.embeddings.get_redis", lambda: redis)
    return redis


@pytest.mark.parametrize(
    "config,provider_result,reason",
    [
        (_config(api_key=None), None, "no API key for embedding provider openai"),
        (_config(provider="nope"), None, "unsupported embedding provider nope"),
        (_config(), [], "embedding provider openai returned no embedding"),
        (_config(), [0.1] * 3, "embedding has 3 dimensions, expected 1536"),
    ],
)
async def test_raise_errors_says_why_there_is_no_embedding(
    monkeypatch, no_cache, config, provider_result, reason
):
    from services.embeddings import EmbeddingUnavailable

    monkeypatch.setattr(
        "services.embeddings._openai_embedding", AsyncMock(return_value=provider_result)
    )
    with pytest.raises(EmbeddingUnavailable, match=f"^{reason}$"):
        await generate_embedding_with_config("hello", config, raise_errors=True)
    # Without raise_errors the same failure is still None, as callers expect.
    assert await generate_embedding_with_config("hello", config) is None


async def test_raise_errors_chains_the_provider_exception(monkeypatch, no_cache):
    import httpx

    from services.embeddings import EmbeddingUnavailable

    async def openai_401(text, cfg):
        request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
        httpx.Response(401, request=request).raise_for_status()

    monkeypatch.setattr("services.embeddings._openai_embedding", openai_401)
    with pytest.raises(EmbeddingUnavailable) as caught:
        await generate_embedding_with_config("hello", _config(), raise_errors=True)
    assert str(caught.value).startswith("HTTPStatusError: Client error '401 Unauthorized'")
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    assert await generate_embedding_with_config("hello", _config()) is None


async def test_raise_errors_still_returns_an_embedding(monkeypatch, no_cache):
    monkeypatch.setattr(
        "services.embeddings._openai_embedding", AsyncMock(return_value=[0.1] * 1536)
    )
    assert await generate_embedding_with_config("hello", _config(), raise_errors=True) == [0.1] * 1536


async def test_raise_errors_without_a_message_has_no_trailing_colon(monkeypatch, no_cache):
    from services.embeddings import EmbeddingUnavailable

    async def timeout(text, cfg):
        raise TimeoutError()

    monkeypatch.setattr("services.embeddings._openai_embedding", timeout)
    with pytest.raises(EmbeddingUnavailable, match="^TimeoutError$"):
        await generate_embedding_with_config("hello", _config(), raise_errors=True)


async def test_the_provider_error_is_logged_masked(monkeypatch, no_cache, caplog):
    async def leaky(text, cfg):
        raise RuntimeError("upstream api-key: abc123secret")

    monkeypatch.setattr("services.embeddings._openai_embedding", leaky)
    with caplog.at_level("WARNING", logger="services.embeddings"):
        await generate_embedding_with_config("hello", _config())
    assert "abc123secret" not in caplog.text
    assert "api-key: [redacted]" in caplog.text
