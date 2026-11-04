"""EMBEDDINGS_PROVIDER=local: OpenAI-compatible endpoint, zero-padded to 1536."""

import json

import httpx
import pytest

from services import embeddings
from services.embedding_config import VECTOR_SIZE, get_tenant_embedding_config


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBEDDINGS_URL", "http://ollama.test/v1/")
    monkeypatch.setenv("LOCAL_EMBEDDINGS_MODEL", "nomic-embed-text")


def _mock_http(monkeypatch, dims, seen):
    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": [{"embedding": [0.5] * dims}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        embeddings.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_local_config_needs_no_key(local_env):
    config = await get_tenant_embedding_config("t1")
    assert (config.provider, config.model) == ("local", "nomic-embed-text")


async def test_short_vectors_are_zero_padded(local_env, monkeypatch):
    seen = {}
    _mock_http(monkeypatch, 768, seen)
    config = await get_tenant_embedding_config("t1")
    vector = await embeddings._local_embedding("hello", config)
    assert len(vector) == VECTOR_SIZE
    assert vector[:768] == [0.5] * 768 and set(vector[768:]) == {0.0}
    assert seen["url"] == "http://ollama.test/v1/embeddings"
    assert json.loads(seen["body"])["model"] == "nomic-embed-text"


async def test_too_large_vectors_are_refused(local_env, monkeypatch):
    _mock_http(monkeypatch, 3072, {})
    config = await get_tenant_embedding_config("t1")
    assert await embeddings._local_embedding("hello", config) is None
