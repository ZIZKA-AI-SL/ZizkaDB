"""instrument() beyond OpenAI and Anthropic: OpenAI-compatible providers
(DeepSeek, GLM, Kimi), Google Gemini, the native ollama package and Zhipu's SDK.

Stand-in modules are shaped like the real libraries; response payloads follow
each provider's documented format.
"""

from __future__ import annotations

import sys
import types

import httpx
import pytest

import zizkadb
from zizkadb import ZizkaDB


@pytest.fixture
def posted(monkeypatch):
    events: list[dict] = []

    def reply(body):
        events.append(dict(body))
        return {"event_id": f"evt-{len(events)}", "timestamp": "2026-11-11T00:00:00+00:00",
                "sequence_no": len(events), "checksum": "c", "indexed": False}

    def fake_sync_post(self, url, json=None, **_):
        return httpx.Response(200, json=reply(json), request=httpx.Request("POST", "http://x" + url))

    async def fake_async_post(_self, path, body):
        return reply(body)

    monkeypatch.setattr(httpx.Client, "post", fake_sync_post)
    monkeypatch.setattr(ZizkaDB, "_post", fake_async_post)
    return events


@pytest.fixture
def db():
    return ZizkaDB(host="http://localhost:8000")


def _install(monkeypatch, modules: dict[str, types.ModuleType]) -> None:
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _scripted(replies):
    def create(self, **kwargs):
        return replies.pop(0)
    return create


# ── OpenAI-compatible providers: DeepSeek, GLM, Kimi ─────────────────────────


@pytest.fixture
def openai_mod(monkeypatch):
    replies: list = []
    oa = types.ModuleType("openai.resources.chat.completions")
    oa.Completions = type("Completions", (), {"create": _scripted(replies)})
    oa.AsyncCompletions = type("AsyncCompletions", (), {})
    _install(monkeypatch, {"openai": types.ModuleType("openai"), "openai.resources.chat.completions": oa})
    return types.SimpleNamespace(replies=replies, Completions=oa.Completions)


def test_deepseek_reasoning_and_cache_hits(openai_mod, posted, db):
    openai_mod.replies.append({  # DeepSeek deepseek-reasoner response shape
        "model": "deepseek-reasoner",
        "choices": [{"message": {"role": "assistant", "content": "42 ships tomorrow.",
                                 "reasoning_content": "The order lookup says tomorrow."}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "prompt_cache_hit_tokens": 96,
                  "prompt_cache_miss_tokens": 24, "completion_tokens_details": {"reasoning_tokens": 31}},
    })
    inst = zizkadb.instrument(db, agent="bot", providers=("openai",))
    try:
        openai_mod.Completions().create(model="deepseek-reasoner", messages=[{"role": "user", "content": "order 42?"}])
    finally:
        inst.uninstrument()
    data = posted[1]["data"]
    assert data["reasoning"] == "The order lookup says tomorrow."
    assert data["token_usage"] == {"model": "deepseek-reasoner", "input_tokens": 120, "output_tokens": 40,
                                   "cached_tokens": 96, "reasoning_tokens": 31}


def test_kimi_cached_tokens_and_glm_reasoning_stream(openai_mod, posted, db):
    openai_mod.replies.append({  # Kimi (Moonshot): cached_tokens at the top of usage
        "model": "kimi-k2", "choices": [{"message": {"content": "Hello"}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 2, "cached_tokens": 32},
    })
    openai_mod.replies.append(iter([  # GLM-4.5 stream: reasoning_content deltas, then content
        {"model": "glm-4.5", "choices": [{"delta": {"reasoning_content": "Think "}}]},
        {"model": "glm-4.5", "choices": [{"delta": {"reasoning_content": "first."}}]},
        {"model": "glm-4.5", "choices": [{"delta": {"content": "Answer."}}],
         "usage": {"prompt_tokens": 9, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 4}}},
    ]))
    inst = zizkadb.instrument(db, agent="bot", providers=("openai",))
    try:
        openai_mod.Completions().create(model="kimi-k2", messages=[{"role": "user", "content": "hi"}])
        list(openai_mod.Completions().create(model="glm-4.5", stream=True, messages=[{"role": "user", "content": "q"}]))
    finally:
        inst.uninstrument()
    assert posted[1]["data"]["token_usage"]["cached_tokens"] == 32
    glm = posted[3]["data"]
    assert glm["reasoning"] == "Think first." and glm["output"] == "Answer."
    assert glm["token_usage"]["cached_tokens"] == 4 and glm["streamed"] is True


def test_capture_content_false_drops_reasoning(openai_mod, posted, db):
    openai_mod.replies.append({"model": "deepseek-reasoner",
                               "choices": [{"message": {"content": "x", "reasoning_content": "secret thoughts"}}]})
    inst = zizkadb.instrument(db, agent="bot", providers=("openai",), capture_content=False)
    try:
        openai_mod.Completions().create(model="deepseek-reasoner", messages=[{"role": "user", "content": "hi"}])
    finally:
        inst.uninstrument()
    assert posted[1]["data"]["reasoning"] is None


def test_zhipu_sdk_is_recorded(monkeypatch, posted, db):
    replies = [{"model": "glm-4.6", "choices": [{"message": {"content": "Ni hao", "tool_calls": [
        {"id": "call_z", "function": {"name": "lookup", "arguments": '{"k": 1}'}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3}}]
    zai_mod = types.ModuleType("zai.api_resource.chat.completions")
    zai_mod.Completions = type("Completions", (), {"create": _scripted(replies)})
    _install(monkeypatch, {"zai": types.ModuleType("zai"), "zai.api_resource.chat.completions": zai_mod})
    inst = zizkadb.instrument(db, agent="bot", providers=("zhipu",))
    try:
        zai_mod.Completions().create(model="glm-4.6", messages=[{"role": "user", "content": "hi"}])
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call"]
    assert posted[2]["data"]["arguments"] == {"k": 1}


# ── Google Gemini (google-genai) ─────────────────────────────────────────────


@pytest.fixture
def gemini(monkeypatch):
    replies: list = []

    class Models:
        def generate_content(self, *, model, contents, config=None):
            return replies.pop(0)

        def generate_content_stream(self, *, model, contents, config=None):
            return iter(replies.pop(0))

    class AsyncModels:
        async def generate_content(self, *, model, contents, config=None):
            return replies.pop(0)

        async def generate_content_stream(self, *, model, contents, config=None):
            chunks = replies.pop(0)

            async def gen():
                for c in chunks:
                    yield c
            return gen()

    mod = types.ModuleType("google.genai.models")
    mod.Models, mod.AsyncModels = Models, AsyncModels
    _install(monkeypatch, {"google": types.ModuleType("google"), "google.genai": types.ModuleType("google.genai"),
                           "google.genai.models": mod})
    return types.SimpleNamespace(replies=replies, Models=Models, AsyncModels=AsyncModels)


def _gemini_reply(parts, prompt=20, out=6, thoughts=None):
    usage = {"prompt_token_count": prompt, "candidates_token_count": out, "cached_content_token_count": 0}
    if thoughts:
        usage["thoughts_token_count"] = thoughts
    return {"model_version": "gemini-2.5-flash", "candidates": [{"content": {"role": "model", "parts": parts}}],
            "usage_metadata": usage}


def test_gemini_function_call_loop(gemini, posted, db):
    gemini.replies += [
        _gemini_reply([{"thought": True, "text": "Need the order."},
                       {"function_call": {"id": "fc_g1", "name": "lookup_order", "args": {"id": 42}}}], thoughts=11),
        _gemini_reply([{"text": "Order 42 ships tomorrow."}]),
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("gemini",))
    try:
        models = gemini.Models()
        models.generate_content(model="gemini-2.5-flash", contents="Where is order 42?")
        models.generate_content(model="gemini-2.5-flash", contents=[
            {"role": "user", "parts": [{"text": "Where is order 42?"}]},
            {"role": "model", "parts": [{"function_call": {"id": "fc_g1", "name": "lookup_order", "args": {"id": 42}}}]},
            {"role": "user", "parts": [{"function_response": {"id": "fc_g1", "name": "lookup_order",
                                                               "response": {"result": "ships tomorrow"}}}]},
        ])
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[1]["data"]["reasoning"] == "Need the order."
    assert posted[1]["data"]["token_usage"] == {"model": "gemini-2.5-flash", "input_tokens": 20, "output_tokens": 6,
                                               "cached_tokens": 0, "reasoning_tokens": 11}
    assert posted[2]["data"] == {"tool": "lookup_order", "tool_call_id": "fc_g1", "arguments": {"id": 42}}
    assert posted[3]["parent_id"] == "evt-3" and posted[3]["data"]["result"] == {"result": "ships tomorrow"}
    assert posted[4]["data"]["output"] == "Order 42 ships tomorrow."


def test_gemini_function_response_without_id_matches_by_name(gemini, posted, db):
    gemini.replies += [_gemini_reply([{"function_call": {"name": "get_weather", "args": {"city": "Madrid"}}}]),
                       _gemini_reply([{"text": "Sunny."}])]
    inst = zizkadb.instrument(db, agent="bot", providers=("gemini",))
    try:
        gemini.Models().generate_content(model="g", contents="weather in Madrid?")
        gemini.Models().generate_content(model="g", contents=[
            {"role": "user", "parts": [{"function_response": {"name": "get_weather", "response": {"sky": "sunny"}}}]}])
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[3]["parent_id"] == "evt-3"


def test_gemini_stream_sync_and_async(gemini, posted, db):
    gemini.replies.append([
        _gemini_reply([{"text": "Hel"}], out=1),
        _gemini_reply([{"text": "lo"}], out=2),
    ])
    gemini.replies.append([_gemini_reply([{"text": "async "}]), _gemini_reply([{"text": "stream"}], out=9)])
    inst = zizkadb.instrument(db, agent="bot", providers=("gemini",))
    try:
        chunks = list(gemini.Models().generate_content_stream(model="g", contents="hi"))
        assert len(chunks) == 2

        async def run():
            stream = await gemini.AsyncModels().generate_content_stream(model="g", contents="hey")
            return [c async for c in stream]
        import asyncio
        assert len(asyncio.run(run())) == 2
    finally:
        inst.uninstrument()
    assert posted[1]["data"]["output"] == "Hello" and posted[1]["data"]["streamed"] is True
    assert posted[1]["data"]["token_usage"]["output_tokens"] == 2
    assert posted[3]["data"]["output"] == "async stream"


# ── native ollama package ────────────────────────────────────────────────────


@pytest.fixture
def ollama_mod(monkeypatch):
    replies: list = []

    class Client:
        def chat(self, model="", messages=None, *, tools=None, stream=False, **kw):
            reply = replies.pop(0)
            return iter(reply) if stream else reply

    class AsyncClient:
        async def chat(self, model="", messages=None, *, tools=None, stream=False, **kw):
            return replies.pop(0)

    mod = types.ModuleType("ollama")
    mod.Client, mod.AsyncClient = Client, AsyncClient
    mod.chat = Client().chat  # like ollama: bound to a default client at import
    _install(monkeypatch, {"ollama": mod})
    return types.SimpleNamespace(replies=replies, mod=mod)


def _ollama_reply(content="", tool_calls=None, done=True, thinking=None):
    msg = {"role": "assistant", "content": content, "tool_calls": tool_calls}
    if thinking:
        msg["thinking"] = thinking
    reply = {"model": "qwen3", "message": msg, "done": done}
    if done:
        reply.update(prompt_eval_count=26, eval_count=8)
    return reply


def test_ollama_module_chat_positional_tool_loop(ollama_mod, posted, db):
    ollama_mod.replies += [
        _ollama_reply(tool_calls=[{"function": {"name": "lookup_order", "arguments": {"id": 42}}}], thinking="check it"),
        _ollama_reply("Order 42 ships tomorrow."),
        _ollama_reply("Bye."),
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("ollama",))
    try:
        history = [{"role": "user", "content": "Where is order 42?"}]
        ollama_mod.mod.chat("qwen3", history)  # positional, like ollama.chat(model, messages)
        history += [{"role": "assistant", "tool_calls": [{"function": {"name": "lookup_order"}}]},
                    {"role": "tool", "tool_name": "lookup_order", "content": "ships tomorrow"}]
        ollama_mod.mod.chat(model="qwen3", messages=history)
        history += [{"role": "assistant", "content": "Order 42 ships tomorrow."}, {"role": "user", "content": "thanks"}]
        ollama_mod.mod.Client().chat(model="qwen3", messages=history)
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == [
        "user_message", "llm_end", "tool_call", "tool_result", "llm_end", "user_message", "llm_end"]
    assert posted[1]["data"]["reasoning"] == "check it"
    assert posted[1]["data"]["token_usage"] == {"model": "qwen3", "input_tokens": 26, "output_tokens": 8, "cached_tokens": 0}
    assert posted[3]["parent_id"] == "evt-3"  # tool result matched to its call by tool name


def test_ollama_stream_and_async(ollama_mod, posted, db):
    ollama_mod.replies.append([_ollama_reply("Hel", done=False), _ollama_reply("lo", done=True)])
    ollama_mod.replies.append(_ollama_reply("async hi"))
    inst = zizkadb.instrument(db, agent="bot", providers=("ollama",))
    try:
        list(ollama_mod.mod.chat(model="qwen3", messages=[{"role": "user", "content": "hi"}], stream=True))
        import asyncio
        asyncio.run(ollama_mod.mod.AsyncClient().chat(model="qwen3", messages=[{"role": "user", "content": "hey"}]))
    finally:
        inst.uninstrument()
    assert posted[1]["data"]["output"] == "Hello" and posted[1]["data"]["token_usage"]["output_tokens"] == 8
    assert posted[3]["data"]["output"] == "async hi"
