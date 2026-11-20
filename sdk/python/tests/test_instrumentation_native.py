"""instrument() for native SDKs (Mistral, Cohere, AWS Bedrock) and models run
in-process (llama-cpp-python, Hugging Face transformers).

Stand-in modules are shaped like the real libraries; payloads follow each
library's documented format.
"""

from __future__ import annotations

import asyncio
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
        import json as _json
        _json.dumps(json)  # every recorded payload must be JSON-serializable
        return httpx.Response(200, json=reply(json), request=httpx.Request("POST", "http://x" + url))

    async def fake_async_post(_self, path, body):
        import json as _json
        _json.dumps(body)
        return reply(body)

    monkeypatch.setattr(httpx.Client, "post", fake_sync_post)
    monkeypatch.setattr(ZizkaDB, "_post", fake_async_post)
    return events


@pytest.fixture
def db():
    return ZizkaDB(host="http://localhost:8000")


def _install(monkeypatch, modules):
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _events(posted):
    return [e["event"] for e in posted]


# ── Mistral ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mistral(monkeypatch):
    replies: list = []

    class Chat:
        def complete(self, *, model, messages, **kw):
            return replies.pop(0)

        def stream(self, *, model, messages, **kw):
            return iter(replies.pop(0))

        async def complete_async(self, *, model, messages, **kw):
            return replies.pop(0)

        async def stream_async(self, *, model, messages, **kw):
            events = replies.pop(0)

            async def gen():
                for e in events:
                    yield e
            return gen()

    mod = types.ModuleType("mistralai.client.chat")
    mod.Chat = Chat
    _install(monkeypatch, {"mistralai": types.ModuleType("mistralai"), "mistralai.client": types.ModuleType("mistralai.client"),
                           "mistralai.client.chat": mod})
    return types.SimpleNamespace(replies=replies, Chat=Chat)


def test_mistral_tool_loop_with_dict_arguments(mistral, posted, db):
    mistral.replies += [
        {"model": "mistral-large-latest", "choices": [{"message": {"content": "", "tool_calls": [
            {"id": "m1", "function": {"name": "lookup_order", "arguments": {"id": 42}}}]}}],
         "usage": {"prompt_tokens": 30, "completion_tokens": 9}},
        {"model": "mistral-large-latest", "choices": [{"message": {"content": "Ships tomorrow."}}]},
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("mistral",))
    try:
        chat = mistral.Chat()
        chat.complete(model="mistral-large-latest", messages=[{"role": "user", "content": "order 42?"}])
        chat.complete(model="mistral-large-latest", messages=[
            {"role": "user", "content": "order 42?"},
            {"role": "tool", "tool_call_id": "m1", "name": "lookup_order", "content": "ships tomorrow"}])
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[2]["data"]["arguments"] == {"id": 42} and posted[3]["parent_id"] == "evt-3"
    assert posted[1]["data"]["token_usage"]["input_tokens"] == 30


def test_mistral_streams_sync_and_async(mistral, posted, db):
    ev = lambda d: {"data": d}  # noqa: E731  CompletionEvent wraps the chunk
    mistral.replies.append([ev({"model": "mistral-small", "choices": [{"delta": {"content": "Hel"}}]}),
                            ev({"model": "mistral-small", "choices": [{"delta": {"content": "lo"}}],
                                "usage": {"prompt_tokens": 4, "completion_tokens": 2}})])
    mistral.replies.append([ev({"model": "mistral-small", "choices": [{"delta": {"tool_calls": [
        {"id": "m2", "function": {"name": "search", "arguments": {"q": "x"}}}]}}]})])
    inst = zizkadb.instrument(db, agent="bot", providers=("mistral",))
    try:
        list(mistral.Chat().stream(model="mistral-small", messages=[{"role": "user", "content": "hi"}]))

        async def run():
            stream = await mistral.Chat().stream_async(model="mistral-small", messages=[{"role": "user", "content": "find"}])
            return [e async for e in stream]
        asyncio.run(run())
    finally:
        inst.uninstrument()
    assert posted[1]["data"]["output"] == "Hello" and posted[1]["data"]["token_usage"]["output_tokens"] == 2
    assert _events(posted)[2:] == ["user_message", "llm_end", "tool_call"]
    assert posted[4]["data"]["arguments"] == {"q": "x"}


# ── Cohere ───────────────────────────────────────────────────────────────────


@pytest.fixture
def cohere(monkeypatch):
    replies: list = []

    class V2Client:
        def chat(self, *, model, messages, **kw):
            return replies.pop(0)

        def chat_stream(self, *, model, messages, **kw):
            yield from replies.pop(0)

    class AsyncV2Client:
        async def chat(self, *, model, messages, **kw):
            return replies.pop(0)

        async def chat_stream(self, *, model, messages, **kw):  # an async generator, like cohere
            for e in replies.pop(0):
                yield e

    mod = types.ModuleType("cohere.v2.client")
    mod.V2Client, mod.AsyncV2Client = V2Client, AsyncV2Client
    _install(monkeypatch, {"cohere": types.ModuleType("cohere"), "cohere.v2": types.ModuleType("cohere.v2"), "cohere.v2.client": mod})
    return types.SimpleNamespace(replies=replies, V2Client=V2Client, AsyncV2Client=AsyncV2Client)


class _Doc:
    """An SDK object in a tool result: must be recorded as plain JSON."""

    def model_dump(self):
        return {"type": "document", "document": {"data": "ships tomorrow"}}


def test_cohere_tool_loop(cohere, posted, db):
    cohere.replies += [
        {"message": {"role": "assistant", "tool_plan": "I will look up the order.", "content": None,
                     "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": '{"id": 42}'}}]},
         "usage": {"tokens": {"input_tokens": 50, "output_tokens": 12}}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "Ships tomorrow."}]},
         "usage": {"tokens": {"input_tokens": 70, "output_tokens": 4}}},
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("cohere",))
    try:
        c = cohere.V2Client()
        c.chat(model="command-a-03-2025", messages=[{"role": "user", "content": "order 42?"}])
        c.chat(model="command-a-03-2025", messages=[
            {"role": "user", "content": "order 42?"},
            {"role": "tool", "tool_call_id": "c1", "content": [_Doc()]}])
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[1]["data"]["reasoning"] == "I will look up the order."
    assert posted[1]["data"]["token_usage"] == {"model": "command-a-03-2025", "input_tokens": 50, "output_tokens": 12, "cached_tokens": 0}
    assert posted[3]["parent_id"] == "evt-3"
    assert posted[3]["data"]["result"] == [{"type": "document", "document": {"data": "ships tomorrow"}}]


COHERE_STREAM = [
    {"type": "message-start"},
    {"type": "tool-plan-delta", "delta": {"message": {"tool_plan": "Search it."}}},
    {"type": "tool-call-start", "index": 0, "delta": {"message": {"tool_calls": {"id": "c9", "function": {"name": "search", "arguments": ""}}}}},
    {"type": "tool-call-delta", "index": 0, "delta": {"message": {"tool_calls": {"function": {"arguments": '{"q": "refund"}'}}}}},
    {"type": "content-delta", "delta": {"message": {"content": {"text": "Looking."}}}},
    {"type": "message-end", "delta": {"usage": {"tokens": {"input_tokens": 9, "output_tokens": 6}}}},
]


def test_cohere_sync_stream_and_async_generator_stream(cohere, posted, db):
    cohere.replies += [list(COHERE_STREAM), list(COHERE_STREAM), list(COHERE_STREAM)]
    inst = zizkadb.instrument(db, agent="bot", providers=("cohere",))
    try:
        assert len(list(cohere.V2Client().chat_stream(model="m", messages=[{"role": "user", "content": "a"}]))) == 6

        async def full():
            return [e async for e in cohere.AsyncV2Client().chat_stream(model="m", messages=[{"role": "user", "content": "b"}])]

        async def early():
            async for _ in cohere.AsyncV2Client().chat_stream(model="m", messages=[{"role": "user", "content": "c"}]):
                break
        assert len(asyncio.run(full())) == 6
        asyncio.run(early())
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "tool_call", "user_message", "llm_end", "tool_call", "user_message", "llm_end"]
    assert posted[1]["data"]["output"] == "Looking." and posted[1]["data"]["reasoning"] == "Search it."
    assert posted[2]["data"]["arguments"] == {"q": "refund"}
    assert posted[7]["data"]["truncated"] is True


# ── AWS Bedrock (boto3 Converse) ─────────────────────────────────────────────


@pytest.fixture
def botocore_mod(monkeypatch):
    replies: list = []
    calls: list = []

    class BaseClient:
        def _make_api_call(self, operation_name, api_params):
            calls.append(operation_name)
            return replies.pop(0)

        def converse(self, **kw):
            return self._make_api_call("Converse", kw)

        def converse_stream(self, **kw):
            return self._make_api_call("ConverseStream", kw)

        def list_foundation_models(self):
            return self._make_api_call("ListFoundationModels", {})

    mod = types.ModuleType("botocore.client")
    mod.BaseClient = BaseClient
    _install(monkeypatch, {"botocore": types.ModuleType("botocore"), "botocore.client": mod})
    return types.SimpleNamespace(replies=replies, calls=calls, Client=BaseClient)


def test_bedrock_converse_tool_loop_and_other_calls_untouched(botocore_mod, posted, db):
    botocore_mod.replies += [
        {"modelSummaries": []},
        {"output": {"message": {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "Use the tool."}}},
            {"toolUse": {"toolUseId": "tu_b", "name": "lookup_order", "input": {"id": 42}}}]}},
         "stopReason": "tool_use", "usage": {"inputTokens": 80, "outputTokens": 20, "cacheReadInputTokens": 64}},
        {"output": {"message": {"role": "assistant", "content": [{"text": "Ships tomorrow."}]}},
         "usage": {"inputTokens": 100, "outputTokens": 5}},
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("bedrock",))
    try:
        client = botocore_mod.Client()
        client.list_foundation_models()
        model = "anthropic.claude-sonnet-4-5"
        client.converse(modelId=model, messages=[{"role": "user", "content": [{"text": "order 42?"}]}])
        client.converse(modelId=model, messages=[
            {"role": "user", "content": [{"text": "order 42?"}]},
            {"role": "assistant", "content": [{"toolUse": {"toolUseId": "tu_b", "name": "lookup_order", "input": {"id": 42}}}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "tu_b", "content": [{"json": {"status": "ships tomorrow"}}]}}]}])
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[1]["data"]["model"] == model and posted[1]["data"]["reasoning"] == "Use the tool."
    assert posted[1]["data"]["token_usage"] == {"model": model, "input_tokens": 80, "output_tokens": 20, "cached_tokens": 64}
    assert posted[3]["parent_id"] == "evt-3" and posted[3]["data"]["result"] == [{"status": "ships tomorrow"}]


def test_bedrock_converse_stream(botocore_mod, posted, db):
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Let me check. "}}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "tu_s", "name": "search"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"q": '}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '"refund"}'}}}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 12, "outputTokens": 7}}},
    ]
    botocore_mod.replies.append({"ResponseMetadata": {}, "stream": iter(events)})
    inst = zizkadb.instrument(db, agent="bot", providers=("bedrock",))
    try:
        resp = botocore_mod.Client().converse_stream(modelId="meta.llama3-8b", messages=[{"role": "user", "content": [{"text": "refund?"}]}])
        assert resp["ResponseMetadata"] == {}
        assert list(resp["stream"]) == events
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "tool_call"]
    assert posted[1]["data"]["output"] == "Let me check. " and posted[1]["data"]["token_usage"]["output_tokens"] == 7
    assert posted[2]["data"] == {"tool": "search", "tool_call_id": "tu_s", "arguments": {"q": "refund"}}


# ── models run in-process ────────────────────────────────────────────────────


def test_llama_cpp_create_chat_completion(monkeypatch, posted, db):
    replies = [
        {"model": "/models/qwen2.5-0.5b.gguf", "choices": [{"message": {"role": "assistant", "content": "Hi there."}}],
         "usage": {"prompt_tokens": 11, "completion_tokens": 3}},
        iter([{"model": "/models/qwen2.5-0.5b.gguf", "choices": [{"delta": {"content": "Str"}}]},
              {"model": "/models/qwen2.5-0.5b.gguf", "choices": [{"delta": {"content": "eam"}}]}]),
    ]

    class Llama:
        def create_chat_completion(self, messages, **kw):  # messages positional, like llama-cpp-python
            return replies.pop(0)

    mod = types.ModuleType("llama_cpp")
    mod.Llama = Llama
    _install(monkeypatch, {"llama_cpp": mod})
    inst = zizkadb.instrument(db, agent="bot", providers=("llama_cpp",))
    try:
        Llama().create_chat_completion([{"role": "user", "content": "hello"}])
        list(Llama().create_chat_completion([{"role": "user", "content": "stream it"}], stream=True))
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "user_message", "llm_end"]
    assert posted[1]["data"]["provider"] == "llama_cpp" and posted[1]["data"]["output"] == "Hi there."
    assert posted[3]["data"]["output"] == "Stream"


def test_transformers_pipeline_chat_and_text(monkeypatch, posted, db):
    class TextGenerationPipeline:
        def __init__(self):
            self.model = types.SimpleNamespace(name_or_path="Qwen/Qwen2.5-0.5B-Instruct")

        def __call__(self, text_inputs, **kw):
            if isinstance(text_inputs, str):
                return [{"generated_text": text_inputs + " and so on."}]
            return [{"generated_text": [*text_inputs, {"role": "assistant", "content": "Hello from a local model."}]}]

    pipelines = types.ModuleType("transformers.pipelines.text_generation")
    pipelines.TextGenerationPipeline = TextGenerationPipeline
    _install(monkeypatch, {"transformers": types.ModuleType("transformers"),
                           "transformers.pipelines": types.ModuleType("transformers.pipelines"),
                           "transformers.pipelines.text_generation": pipelines})
    inst = zizkadb.instrument(db, agent="bot", providers=("transformers",))
    try:
        pipe = TextGenerationPipeline()
        pipe([{"role": "user", "content": "hi"}], max_new_tokens=20)
        pipe("Once upon a time")
    finally:
        inst.uninstrument()
    assert _events(posted) == ["user_message", "llm_end", "user_message", "llm_end"]
    assert posted[1]["data"] == {"provider": "transformers", "model": "Qwen/Qwen2.5-0.5B-Instruct", "output": "Hello from a local model."}
    assert posted[3]["data"]["output"] == "Once upon a time and so on."


def test_heavy_libraries_are_not_imported_by_instrument(monkeypatch, db):
    for name in ("transformers", "llama_cpp"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    imported: list[str] = []
    real_import = __import__("importlib").import_module

    def spy(name, *a, **k):
        imported.append(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr("importlib.import_module", spy)
    inst = zizkadb.instrument(db, agent="bot", providers=("transformers", "llama_cpp"))
    inst.uninstrument()
    assert not any(n.startswith(("transformers", "llama_cpp")) for n in imported)
