"""zizkadb.instrument(): auto-capture of LLM calls with causal links.

Stand-in provider modules (shaped like the openai, anthropic and litellm SDKs)
are put in sys.modules, so the tests need none of those libraries.
"""

from __future__ import annotations

import sys
import types

import httpx
import pytest

import zizkadb
from zizkadb import ZizkaDB


def _openai_response(text=None, tool_calls=None):
    return {
        "model": "gpt-test",
        "choices": [{"message": {"content": text, "tool_calls": tool_calls or []}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 2}},
    }


@pytest.fixture
def providers(monkeypatch):
    """Install fake openai / anthropic / litellm modules; yield their scripted replies."""
    replies: dict[str, list] = {"openai": [], "anthropic": [], "responses": [], "anthropic_helper": []}

    class Completions:
        def create(self, **kwargs):
            reply = replies["openai"].pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        def stream(self, **kwargs):  # like openai: the helper calls create(stream=True)
            return list(self.create(stream=True, **kwargs))

    class AsyncCompletions:
        async def create(self, **kwargs):
            return replies["openai"].pop(0)

    class Messages:
        def create(self, **kwargs):
            return replies["anthropic"].pop(0)

        def stream(self, **kwargs):  # like anthropic: posts directly, not via create()
            return replies["anthropic_helper"].pop(0)

    class AsyncMessages:
        async def create(self, **kwargs):
            return replies["anthropic"].pop(0)

        def stream(self, **kwargs):
            return replies["anthropic_helper"].pop(0)

    class Responses:
        def create(self, **kwargs):
            return replies["responses"].pop(0)

    class AsyncResponses:
        async def create(self, **kwargs):
            return replies["responses"].pop(0)

    rs = types.ModuleType("openai.resources.responses")
    rs.Responses, rs.AsyncResponses = Responses, AsyncResponses
    oa = types.ModuleType("openai.resources.chat.completions")
    oa.Completions, oa.AsyncCompletions = Completions, AsyncCompletions
    an = types.ModuleType("anthropic.resources.messages")
    an.Messages, an.AsyncMessages = Messages, AsyncMessages
    ll = types.ModuleType("litellm")

    def completion(**kwargs):  # LiteLLM calls the OpenAI SDK underneath
        return oa.Completions().create(**kwargs)

    async def acompletion(**kwargs):
        return await oa.AsyncCompletions().create(**kwargs)

    ll.completion, ll.acompletion = completion, acompletion
    for name, mod in {
        "openai": types.ModuleType("openai"),
        "openai.resources": types.ModuleType("openai.resources"),
        "openai.resources.chat": types.ModuleType("openai.resources.chat"),
        "openai.resources.chat.completions": oa,
        "openai.resources.responses": rs,
        "anthropic": types.ModuleType("anthropic"),
        "anthropic.resources": types.ModuleType("anthropic.resources"),
        "anthropic.resources.messages": an,
        "litellm": ll,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    yield types.SimpleNamespace(replies=replies, openai=oa, responses=rs, anthropic=an, litellm=ll)


@pytest.fixture
def posted(monkeypatch):
    """Capture event writes from both the sync (httpx.Client) and async paths."""
    events: list[dict] = []

    def reply(body):
        events.append(dict(body))
        return {
            "event_id": f"evt-{len(events)}",
            "timestamp": "2026-11-11T00:00:00+00:00",
            "sequence_no": len(events),
            "checksum": "c",
            "indexed": False,
        }

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


def _by(events, name):
    return [e for e in events if e["event"] == name]


def test_openai_tool_loop_is_causally_linked(providers, posted, db):
    providers.replies["openai"] += [
        _openai_response(tool_calls=[{"id": "call_1", "function": {"name": "lookup_order", "arguments": '{"id": 42}'}}]),
        _openai_response(text="Your order ships tomorrow."),
        _openai_response(text="You're welcome."),
    ]
    inst = zizkadb.instrument(db, agent="bot")
    try:
        client = providers.openai.Completions()
        history = [{"role": "user", "content": "Where is order 42?"}]
        client.create(model="gpt-test", messages=history)
        history += [
            {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ships tomorrow"},
        ]
        client.create(model="gpt-test", messages=history)
        history += [{"role": "assistant", "content": "Your order ships tomorrow."}, {"role": "user", "content": "thanks"}]
        client.create(model="gpt-test", messages=history)
    finally:
        inst.uninstrument()

    names = [e["event"] for e in posted]
    assert names == [
        "user_message", "llm_end", "tool_call",   # turn 1
        "tool_result", "llm_end",                  # turn 2: the tool's answer
        "user_message", "llm_end",                 # turn 3: the resent tool result is not logged again
    ]
    ids = {i + 1: e for i, e in enumerate(posted)}
    assert ids[2]["parent_id"] == "evt-1"            # llm_end <- user_message
    assert ids[3]["parent_id"] == "evt-2"            # tool_call <- llm_end
    assert ids[3]["data"]["arguments"] == {"id": 42}
    assert ids[4]["parent_id"] == "evt-3"            # tool_result <- tool_call
    assert ids[5]["parent_id"] == "evt-4"            # llm_end <- tool_result
    assert ids[2]["data"]["token_usage"] == {
        "model": "gpt-test", "input_tokens": 12, "output_tokens": 5, "cached_tokens": 2,
    }
    assert all(e["agent"] == "bot" and e["metadata"] == {"source": "zizkadb.instrument"} for e in posted)


@pytest.mark.asyncio
async def test_async_anthropic_tool_use(providers, posted, db):
    providers.replies["anthropic"] += [
        {
            "model": "claude-test",
            "content": [{"type": "text", "text": "Checking."}, {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "refund"}}],
            "usage": {"input_tokens": 30, "output_tokens": 8},
        },
        {"model": "claude-test", "content": [{"type": "text", "text": "Refunds take 5 days."}], "usage": {"input_tokens": 40, "output_tokens": 6}},
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("anthropic",))
    try:
        client = providers.anthropic.AsyncMessages()
        await client.create(model="claude-test", messages=[{"role": "user", "content": "refund time?"}])
        await client.create(
            model="claude-test",
            messages=[
                {"role": "user", "content": "refund time?"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1"}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "5 days"}]},
            ],
        )
    finally:
        inst.uninstrument()

    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[2]["data"]["tool"] == "search" and posted[2]["parent_id"] == "evt-2"
    assert posted[3]["parent_id"] == "evt-3" and posted[3]["data"]["result"] == "5 days"
    assert posted[1]["data"]["token_usage"]["input_tokens"] == 30


def test_error_is_recorded_and_reraised(providers, posted, db):
    providers.replies["openai"].append(RuntimeError("rate limited"))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        with pytest.raises(RuntimeError, match="rate limited"):
            providers.openai.Completions().create(model="gpt-test", messages=[{"role": "user", "content": "hi"}])
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_error"]
    assert posted[1]["parent_id"] == "evt-1" and "rate limited" in posted[1]["data"]["error"]


def test_a_failed_write_never_breaks_the_llm_call(providers, monkeypatch, db):
    def boom(*_a, **_k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.Client, "post", boom)
    providers.replies["openai"].append(_openai_response(text="still works"))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        resp = providers.openai.Completions().create(model="gpt-test", messages=[{"role": "user", "content": "hi"}])
    finally:
        inst.uninstrument()
    assert resp["choices"][0]["message"]["content"] == "still works"


def test_capture_content_false_keeps_links_not_text(providers, posted, db):
    providers.replies["openai"].append(_openai_response(text="secret answer"))
    inst = zizkadb.instrument(db, agent="bot", capture_content=False)
    try:
        providers.openai.Completions().create(model="gpt-test", messages=[{"role": "user", "content": "secret question"}])
    finally:
        inst.uninstrument()
    assert posted[0]["data"]["text"] is None
    assert posted[1]["data"]["output"] is None and posted[1]["data"]["token_usage"]["input_tokens"] == 12
    assert posted[1]["parent_id"] == "evt-1"


def test_litellm_over_openai_is_recorded_once(providers, posted, db):
    providers.replies["openai"].append(_openai_response(text="hi"))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        providers.litellm.completion(model="gpt-test", messages=[{"role": "user", "content": "hello"}])
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end"]
    assert posted[1]["data"]["provider"] == "litellm"


class _FakeStream:
    """Iterable stream like openai.Stream / anthropic.Stream, with extras."""

    def __init__(self, chunks, fail_after=None):
        self._chunks = list(chunks)
        self._fail_after = fail_after
        self.closed = False
        self.response = "http-response"

    def __iter__(self):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i == self._fail_after:
                raise ConnectionError("stream dropped")
            yield chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _FakeAsyncStream(_FakeStream):
    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk
        return gen()


def _oa_chunk(content=None, tool=None, usage=None, choices=True):
    delta = {}
    if content:
        delta["content"] = content
    if tool:
        delta["tool_calls"] = [tool]
    return {"model": "gpt-test", "choices": [{"delta": delta}] if choices else [], "usage": usage}


OPENAI_TOOL_STREAM = [
    _oa_chunk(tool={"index": 0, "id": "call_s1", "function": {"name": "lookup_order", "arguments": '{"id"'}}),
    _oa_chunk(tool={"index": 0, "function": {"arguments": ": 42}"}}),
    _oa_chunk(choices=False, usage={"prompt_tokens": 9, "completion_tokens": 4}),
]


def test_openai_stream_records_output_tools_and_usage(providers, posted, db):
    providers.replies["openai"] += [
        _FakeStream(OPENAI_TOOL_STREAM),
        _FakeStream([_oa_chunk("Order 42 "), _oa_chunk("ships tomorrow.")]),
    ]
    inst = zizkadb.instrument(db, agent="bot")
    try:
        client = providers.openai.Completions()
        history = [{"role": "user", "content": "Where is order 42?"}]
        stream = client.create(model="gpt-test", stream=True, messages=history)
        assert stream.response == "http-response"  # provider extras still reachable
        chunks = list(stream)
        assert chunks == OPENAI_TOOL_STREAM  # caller gets every chunk unchanged
        history += [{"role": "assistant", "tool_calls": [{"id": "call_s1"}]},
                    {"role": "tool", "tool_call_id": "call_s1", "content": "ships tomorrow"}]
        text = "".join(c["choices"][0]["delta"].get("content", "") for c in client.create(model="gpt-test", stream=True, messages=history))
    finally:
        inst.uninstrument()

    assert text == "Order 42 ships tomorrow."
    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[1]["data"]["streamed"] is True
    assert posted[1]["data"]["token_usage"] == {"model": "gpt-test", "input_tokens": 9, "output_tokens": 4, "cached_tokens": 0}
    assert posted[2]["data"]["arguments"] == {"id": 42} and posted[2]["parent_id"] == "evt-2"
    assert posted[3]["parent_id"] == "evt-3"
    assert posted[4]["data"]["output"] == "Order 42 ships tomorrow."


def test_stream_closed_early_is_marked_truncated(providers, posted, db):
    providers.replies["openai"].append(_FakeStream([_oa_chunk("Hel"), _oa_chunk("lo"), _oa_chunk(" world")]))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        with providers.openai.Completions().create(model="gpt-test", stream=True, messages=[{"role": "user", "content": "hi"}]) as stream:
            next(iter(stream))
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end"]
    assert posted[1]["data"]["truncated"] is True and posted[1]["data"]["output"] == "Hel"


def test_stream_failing_midway_is_an_llm_error(providers, posted, db):
    providers.replies["openai"].append(_FakeStream([_oa_chunk("partial "), _oa_chunk("never")], fail_after=1))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        with pytest.raises(ConnectionError):
            for _ in providers.openai.Completions().create(model="gpt-test", stream=True, messages=[{"role": "user", "content": "hi"}]):
                pass
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_error"]
    assert posted[1]["data"]["partial_output"] == "partial " and "stream dropped" in posted[1]["data"]["error"]


@pytest.mark.asyncio
async def test_async_anthropic_stream_with_tool_use(providers, posted, db):
    events = [
        {"type": "message_start", "message": {"model": "claude-test", "usage": {"input_tokens": 30, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking."}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu_9", "name": "search"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"q": "ref'}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": 'und"}'}},
        {"type": "message_delta", "usage": {"output_tokens": 17}},
        {"type": "message_stop"},
    ]
    providers.replies["anthropic"].append(_FakeAsyncStream(events))
    inst = zizkadb.instrument(db, agent="bot", providers=("anthropic",))
    try:
        stream = await providers.anthropic.AsyncMessages().create(model="claude-test", stream=True, messages=[{"role": "user", "content": "refund?"}])
        received = [e async for e in stream]
    finally:
        inst.uninstrument()
    assert received == events
    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call"]
    assert posted[1]["data"]["output"] == "Checking."
    assert posted[1]["data"]["token_usage"] == {"model": "claude-test", "input_tokens": 30, "output_tokens": 17, "cached_tokens": 0}
    assert posted[2]["data"] == {"tool": "search", "tool_call_id": "tu_9", "arguments": {"q": "refund"}}


def test_responses_api_stream_uses_the_completed_response(providers, posted, db):
    completed = _responses_reply(text="All done.")
    events = [
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "All "},
        {"type": "response.output_text.delta", "delta": "done."},
        {"type": "response.completed", "response": completed},
    ]
    providers.replies["responses"].append(_FakeStream(events))
    inst = zizkadb.instrument(db, agent="bot", providers=("openai",))
    try:
        list(providers.responses.Responses().create(model="gpt-resp", stream=True, input="finish it"))
    finally:
        inst.uninstrument()
    assert posted[1]["data"]["output"] == "All done."
    assert posted[1]["data"]["token_usage"]["reasoning_tokens"] == 3


def test_litellm_stream_is_recorded_once(providers, posted, db):
    providers.replies["openai"].append(_FakeStream([_oa_chunk("hi")]))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        list(providers.litellm.completion(model="gpt-test", stream=True, messages=[{"role": "user", "content": "hello"}]))
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end"]
    assert posted[1]["data"]["provider"] == "litellm" and posted[1]["data"]["output"] == "hi"


@pytest.mark.asyncio
async def test_track_session_is_shared(providers, posted, db):
    providers.replies["openai"].append(_openai_response(text="ok"))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        async with db.track(agent="bot") as ctx:
            await providers.openai.AsyncCompletions().create(model="gpt-test", messages=[{"role": "user", "content": "hi"}])
    finally:
        inst.uninstrument()
    assert {e["session_id"] for e in posted} == {ctx.session_id}


def test_uninstrument_restores_originals(providers, db):
    original = providers.openai.Completions.create
    inst = zizkadb.instrument(db, agent="bot")
    assert providers.openai.Completions.create is not original
    inst.uninstrument()
    assert providers.openai.Completions.create is original


def test_missing_providers_are_skipped(monkeypatch, db, caplog):
    from zizkadb import instrumentation
    for targets in instrumentation._TARGETS.values():
        for module_path, *_ in targets:
            monkeypatch.setitem(sys.modules, module_path, None)  # import raises ImportError
    for root in instrumentation._ONLY_IF_IMPORTED.values():
        monkeypatch.delitem(sys.modules, root, raising=False)
    inst = zizkadb.instrument(db, agent="bot")
    inst.uninstrument()
    assert "nothing to record" in caplog.text


def _responses_reply(text=None, calls=()):
    output = [{"type": "function_call", "call_id": c, "name": n, "arguments": a} for c, n, a in calls]
    if text:
        output.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})
    return {
        "model": "gpt-resp",
        "output": output,
        "usage": {
            "input_tokens": 50, "output_tokens": 9,
            "input_tokens_details": {"cached_tokens": 4}, "output_tokens_details": {"reasoning_tokens": 3},
        },
    }


def test_responses_api_tool_loop(providers, posted, db):
    providers.replies["responses"] += [
        _responses_reply(calls=[("fc_1", "lookup_order", '{"id": 42}')]),
        _responses_reply(text="Order 42 ships tomorrow."),
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("openai",))
    try:
        client = providers.responses.Responses()
        client.create(model="gpt-resp", input="Where is order 42?")
        client.create(
            model="gpt-resp",
            previous_response_id="resp_1",
            input=[{"type": "function_call_output", "call_id": "fc_1", "output": "ships tomorrow"}],
        )
    finally:
        inst.uninstrument()

    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert posted[0]["data"]["text"] == "Where is order 42?"
    assert posted[2]["data"] == {"tool": "lookup_order", "tool_call_id": "fc_1", "arguments": {"id": 42}}
    assert posted[3]["parent_id"] == "evt-3" and posted[3]["data"]["result"] == "ships tomorrow"
    assert posted[4]["parent_id"] == "evt-4" and posted[4]["data"]["output"] == "Order 42 ships tomorrow."
    assert posted[1]["data"]["token_usage"] == {
        "model": "gpt-resp", "input_tokens": 50, "output_tokens": 9, "cached_tokens": 4, "reasoning_tokens": 3,
    }


@pytest.mark.asyncio
async def test_async_responses_api_with_message_list_input(providers, posted, db):
    providers.replies["responses"].append(_responses_reply(text="Hi!"))
    inst = zizkadb.instrument(db, agent="bot", providers=("openai",))
    try:
        await providers.responses.AsyncResponses().create(
            model="gpt-resp",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hello there"}]}],
        )
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end"]
    assert posted[0]["data"]["text"] == "hello there" and posted[1]["data"]["output"] == "Hi!"


class _FakeMessageStream:
    """Like anthropic's MessageStream: a public snapshot of what arrived."""

    def __init__(self, snapshot):
        self._snapshot = snapshot

    @property
    def current_message_snapshot(self):
        assert self._snapshot is not None
        return self._snapshot

    def get_final_text(self):
        return "".join(b["text"] for b in self._snapshot["content"] if b["type"] == "text")


class _FakeManager:
    def __init__(self, snapshot, enter_error=None):
        self.stream = _FakeMessageStream(snapshot)
        self.enter_error = enter_error
        self.exited = False

    def __enter__(self):
        if self.enter_error:
            raise self.enter_error
        return self.stream

    def __exit__(self, *exc):
        self.exited = True
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)


_HELPER_SNAPSHOT = {
    "model": "claude-test",
    "stop_reason": "tool_use",
    "content": [{"type": "text", "text": "Checking."}, {"type": "tool_use", "id": "tu_h", "name": "search", "input": {"q": "refund"}}],
    "usage": {"input_tokens": 30, "output_tokens": 12},
}


def test_anthropic_stream_helper_is_recorded_on_exit(providers, posted, db):
    manager = _FakeManager(_HELPER_SNAPSHOT)
    providers.replies["anthropic_helper"].append(manager)
    inst = zizkadb.instrument(db, agent="bot", providers=("anthropic",))
    try:
        with providers.anthropic.Messages().stream(model="claude-test", messages=[{"role": "user", "content": "refund?"}]) as stream:
            assert stream.get_final_text() == "Checking."
    finally:
        inst.uninstrument()
    assert manager.exited
    assert [e["event"] for e in posted] == ["user_message", "llm_end", "tool_call"]
    assert posted[1]["data"]["streamed"] is True and "truncated" not in posted[1]["data"]
    assert posted[1]["data"]["token_usage"]["output_tokens"] == 12
    assert posted[2]["data"] == {"tool": "search", "tool_call_id": "tu_h", "arguments": {"q": "refund"}}


@pytest.mark.asyncio
async def test_async_anthropic_stream_helper_left_early_is_truncated(providers, posted, db):
    partial = dict(_HELPER_SNAPSHOT, stop_reason=None, content=[{"type": "text", "text": "Chec"}])
    providers.replies["anthropic_helper"].append(_FakeManager(partial))
    inst = zizkadb.instrument(db, agent="bot", providers=("anthropic",))
    try:
        async with providers.anthropic.AsyncMessages().stream(model="claude-test", messages=[{"role": "user", "content": "hi"}]):
            pass
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end"]
    assert posted[1]["data"]["truncated"] is True and posted[1]["data"]["output"] == "Chec"


def test_anthropic_stream_helper_errors(providers, posted, db):
    providers.replies["anthropic_helper"] += [
        _FakeManager(None, enter_error=RuntimeError("overloaded")),
        _FakeManager(dict(_HELPER_SNAPSHOT, stop_reason=None, content=[{"type": "text", "text": "half"}])),
    ]
    inst = zizkadb.instrument(db, agent="bot", providers=("anthropic",))
    try:
        with pytest.raises(RuntimeError, match="overloaded"):
            with providers.anthropic.Messages().stream(model="claude-test", messages=[{"role": "user", "content": "one"}]):
                pass
        with pytest.raises(ValueError, match="app bug"):
            with providers.anthropic.Messages().stream(model="claude-test", messages=[{"role": "user", "content": "two"}]):
                raise ValueError("app bug")
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_error", "user_message", "llm_error"]
    assert "overloaded" in posted[1]["data"]["error"]
    assert posted[3]["data"]["partial_output"] == "half"


def test_openai_stream_helper_is_recorded_through_create(providers, posted, db):
    providers.replies["openai"].append(_FakeStream([_oa_chunk("Hello "), _oa_chunk("there.")]))
    inst = zizkadb.instrument(db, agent="bot")
    try:
        providers.openai.Completions().stream(model="gpt-test", messages=[{"role": "user", "content": "hi"}])
    finally:
        inst.uninstrument()
    assert [e["event"] for e in posted] == ["user_message", "llm_end"]
    assert posted[1]["data"]["output"] == "Hello there."
