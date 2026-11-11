"""One-line auto-capture of LLM calls into ZizkaDB.

    import zizkadb
    from zizkadb import ZizkaDB

    db = ZizkaDB(api_key="zizkadb_live_...")
    zizkadb.instrument(db, agent="support-bot")

    # ...then use OpenAI, Anthropic or LiteLLM as usual.

Every chat call is recorded with its causal links, so ``db.why()`` works with
no ``db.log()`` calls in your code:

    user_message ──> llm_end ──> tool_call ──> tool_result ──> llm_end ...

- ``user_message``: the newest user message in the request.
- ``llm_end``: model, output text and token usage (``data.token_usage`` in the
  shape the token-cost dashboard reads), parented to the user message or to
  the tool result the model is answering.
- ``tool_call``: one per tool call the model asked for, parented to its
  ``llm_end``.
- ``tool_result``: when a later request carries the result of a tool call
  (OpenAI ``role="tool"``, Anthropic ``tool_result`` blocks), parented to that
  ``tool_call``.
- ``llm_error``: the call raised; the exception is re-raised unchanged.

Inside ``db.track(agent=...)`` events also share that session and follow its
lineage. Logging never breaks your LLM call: a failed write is logged as a
warning and the provider's response is returned as is.

Supported: ``openai`` (sync and async chat completions and the Responses API,
``client.responses.create``; chat completions also cover
OpenAI-compatible servers such as Ollama, vLLM and LM Studio), ``anthropic``
(sync and async messages) and ``litellm`` (``completion``/``acompletion``
called as ``litellm.completion``). Streaming calls are recorded without their
output.
"""

from __future__ import annotations

import contextvars
import importlib
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .context import record_log_result, resolve_log_lineage
from .models import LogResult

logger = logging.getLogger(__name__)

_TEXT_LIMIT = 4000
_TOOL_IDS_KEPT = 1000

# Set while a wrapped call runs, so a provider that calls another wrapped
# provider (LiteLLM calls the OpenAI SDK) is recorded once, not twice.
_in_call: contextvars.ContextVar[bool] = contextvars.ContextVar("zizkadb_instrument_in_call", default=False)

# provider -> list of (module path, class name or None for module functions,
# attribute, "sync" | "async", response shape)
_TARGETS: dict[str, list[tuple[str, str | None, str, str, str]]] = {
    "openai": [
        ("openai.resources.chat.completions", "Completions", "create", "sync", "openai"),
        ("openai.resources.chat.completions", "AsyncCompletions", "create", "async", "openai"),
        ("openai.resources.responses", "Responses", "create", "sync", "openai_responses"),
        ("openai.resources.responses", "AsyncResponses", "create", "async", "openai_responses"),
    ],
    "anthropic": [
        ("anthropic.resources.messages", "Messages", "create", "sync", "anthropic"),
        ("anthropic.resources.messages", "AsyncMessages", "create", "async", "anthropic"),
    ],
    "litellm": [
        ("litellm", None, "completion", "sync", "openai"),
        ("litellm", None, "acompletion", "async", "openai"),
    ],
}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from an SDK object or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… [{len(value) - limit} more chars]"
    return value


# Text content block types: chat/Anthropic "text", Responses API input/output.
_TEXT_BLOCKS = frozenset({"text", "input_text", "output_text"})


def _text_of(content: Any) -> str | None:
    """Plain text of a message content (string or list of content blocks)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if _get(block, "type") in _TEXT_BLOCKS and _get(block, "text"):
            parts.append(_get(block, "text"))
    return "\n".join(parts) if parts else None


def _parse_args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return _clip(raw)
    return raw


# ── request parsing ─────────────────────────────────────────────────────────


def _new_user_text(messages: Any) -> str | None:
    """Text of the last message when it is the user's (a new turn)."""
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    if _get(last, "role") != "user":
        return None
    content = _get(last, "content")
    if isinstance(content, list) and any(_get(b, "type") == "tool_result" for b in content):
        return None  # Anthropic tool results arrive as a user message
    return _text_of(content)


def _tool_results(messages: Any) -> list[tuple[str, Any]]:
    """(tool_call_id, result) pairs carried by a request."""
    out: list[tuple[str, Any]] = []
    for msg in messages if isinstance(messages, list) else []:
        if _get(msg, "role") == "tool" and _get(msg, "tool_call_id"):
            out.append((_get(msg, "tool_call_id"), _clip(_text_of(_get(msg, "content")))))
        content = _get(msg, "content")
        if isinstance(content, list):
            for block in content:
                if _get(block, "type") == "tool_result" and _get(block, "tool_use_id"):
                    out.append((_get(block, "tool_use_id"), _clip(_text_of(_get(block, "content")) or _get(block, "content"))))
    return out


def _responses_items(kwargs: dict[str, Any]) -> list[Any]:
    """Responses API ``input`` as a list of items (a bare string is one user message)."""
    raw = kwargs.get("input")
    if isinstance(raw, str):
        return [{"role": "user", "content": raw}]
    return raw if isinstance(raw, list) else []


def _request_view(shape: str, kwargs: dict[str, Any]) -> tuple[str | None, list[tuple[str, Any]]]:
    """(new user text, tool results) carried by a request, for any API shape."""
    if shape != "openai_responses":
        messages = kwargs.get("messages")
        return _new_user_text(messages), _tool_results(messages)
    items = _responses_items(kwargs)
    results = [
        (_get(i, "call_id"), _clip(_get(i, "output")))
        for i in items
        if _get(i, "type") == "function_call_output" and _get(i, "call_id")
    ]
    user_text = None
    if items and _get(items[-1], "role") == "user":
        user_text = _text_of(_get(items[-1], "content"))
    return user_text, results


# ── response parsing ────────────────────────────────────────────────────────


@dataclass
class _Parsed:
    model: str | None
    text: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None


def _parse_openai(resp: Any, model: str | None) -> _Parsed:
    choices = _get(resp, "choices") or []
    message = _get(choices[0], "message") if choices else None
    calls = []
    for tc in _get(message, "tool_calls") or []:
        fn = _get(tc, "function")
        calls.append({"id": _get(tc, "id"), "name": _get(fn, "name"), "arguments": _parse_args(_get(fn, "arguments"))})
    usage = _get(resp, "usage")
    token_usage = None
    if usage is not None:
        details = _get(usage, "prompt_tokens_details")
        token_usage = {
            "model": _get(resp, "model") or model,
            "input_tokens": _get(usage, "prompt_tokens", 0) or 0,
            "output_tokens": _get(usage, "completion_tokens", 0) or 0,
            "cached_tokens": (_get(details, "cached_tokens", 0) or 0) if details is not None else 0,
        }
    return _Parsed(_get(resp, "model") or model, _text_of(_get(message, "content")), calls, token_usage)


def _parse_anthropic(resp: Any, model: str | None) -> _Parsed:
    texts, calls = [], []
    for block in _get(resp, "content") or []:
        if _get(block, "type") == "text":
            texts.append(_get(block, "text") or "")
        elif _get(block, "type") == "tool_use":
            calls.append({"id": _get(block, "id"), "name": _get(block, "name"), "arguments": _get(block, "input")})
    usage = _get(resp, "usage")
    token_usage = None
    if usage is not None:
        token_usage = {
            "model": _get(resp, "model") or model,
            "input_tokens": _get(usage, "input_tokens", 0) or 0,
            "output_tokens": _get(usage, "output_tokens", 0) or 0,
            "cached_tokens": _get(usage, "cache_read_input_tokens", 0) or 0,
        }
    return _Parsed(_get(resp, "model") or model, "\n".join(texts) if texts else None, calls, token_usage)


def _parse_openai_responses(resp: Any, model: str | None) -> _Parsed:
    texts, calls = [], []
    for item in _get(resp, "output") or []:
        kind = _get(item, "type")
        if kind == "message":
            text = _text_of(_get(item, "content"))
            if text:
                texts.append(text)
        elif kind == "function_call":
            calls.append({"id": _get(item, "call_id"), "name": _get(item, "name"), "arguments": _parse_args(_get(item, "arguments"))})
    usage = _get(resp, "usage")
    token_usage = None
    if usage is not None:
        in_details = _get(usage, "input_tokens_details")
        out_details = _get(usage, "output_tokens_details")
        token_usage = {
            "model": _get(resp, "model") or model,
            "input_tokens": _get(usage, "input_tokens", 0) or 0,
            "output_tokens": _get(usage, "output_tokens", 0) or 0,
            "cached_tokens": (_get(in_details, "cached_tokens", 0) or 0) if in_details is not None else 0,
            "reasoning_tokens": (_get(out_details, "reasoning_tokens", 0) or 0) if out_details is not None else 0,
        }
    return _Parsed(_get(resp, "model") or model, "\n".join(texts) if texts else None, calls, token_usage)


_PARSERS: dict[str, Callable[[Any, str | None], _Parsed]] = {
    "openai": _parse_openai,
    "openai_responses": _parse_openai_responses,
    "anthropic": _parse_anthropic,
}


# ── recording ───────────────────────────────────────────────────────────────


class Instrumentation:
    """Handle returned by :func:`instrument`; call :meth:`uninstrument` to stop."""

    def __init__(self, db: Any, agent: str, capture_content: bool) -> None:
        self._db = db
        self.agent = agent
        self.capture_content = capture_content
        self._patches: list[tuple[Any, str, Any]] = []
        # tool_call_id -> event_id, so a later tool result links to its call
        self._tool_events: OrderedDict[str, str] = OrderedDict()
        # tool_call_ids whose result is already recorded: chat APIs resend the
        # whole history on every call, so each result is logged once.
        self._results_seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        self._sync_client: httpx.Client | None = None

    # -- event plumbing -----------------------------------------------------

    def _body(self, event: str, data: dict[str, Any], parent_id: str | None) -> dict[str, Any]:
        explicit = parent_id is not None
        parent, session = resolve_log_lineage(
            agent=self.agent, event=event, parent_id=parent_id, session_id=None, explicit_parent=explicit
        )
        return {
            "agent": self.agent,
            "event": event,
            "data": data,
            "parent_id": parent,
            "session_id": session,
            "metadata": {"source": "zizkadb.instrument"},
        }

    def _write_sync(self, event: str, data: dict[str, Any], parent_id: str | None) -> str | None:
        try:
            body = self._body(event, data, parent_id)
            if self._sync_client is None:
                self._sync_client = httpx.Client(
                    base_url=self._db._base_url, headers=self._db._headers(), timeout=self._db._timeout
                )
            result = LogResult.from_dict(self._db._handle(self._sync_client.post("/v1/events", json=body)))
            record_log_result(self.agent, result)
            return result.event_id
        except Exception as exc:  # never break the user's LLM call
            logger.warning("zizkadb.instrument: could not record %s: %s", event, exc)
            return None

    async def _write_async(self, event: str, data: dict[str, Any], parent_id: str | None) -> str | None:
        try:
            body = self._body(event, data, parent_id)
            result = LogResult.from_dict(await self._db._post("/v1/events", body))
            record_log_result(self.agent, result)
            return result.event_id
        except Exception as exc:
            logger.warning("zizkadb.instrument: could not record %s: %s", event, exc)
            return None

    def _remember_tool(self, tool_id: str | None, event_id: str | None) -> None:
        if not tool_id or not event_id:
            return
        with self._lock:
            self._tool_events[tool_id] = event_id
            while len(self._tool_events) > _TOOL_IDS_KEPT:
                self._tool_events.popitem(last=False)

    def _tool_event(self, tool_id: str) -> str | None:
        with self._lock:
            return self._tool_events.get(tool_id)

    def _first_result(self, tool_id: str) -> bool:
        """True the first time a result for this tool call is seen."""
        with self._lock:
            if tool_id in self._results_seen:
                return False
            self._results_seen[tool_id] = None
            while len(self._results_seen) > _TOOL_IDS_KEPT:
                self._results_seen.popitem(last=False)
            return True

    # -- one LLM call, as a plan of events ----------------------------------

    def _content(self, value: Any) -> Any:
        return _clip(value) if self.capture_content else None

    def _before(self, kwargs: dict[str, Any], shape: str = "openai") -> list[tuple[str, dict[str, Any], str | None]]:
        """Events to write before the call: tool results, then the new user message."""
        user_text, results = _request_view(shape, kwargs)
        plan = []
        for tool_id, result in results:
            if not self._first_result(tool_id):
                continue
            plan.append(("tool_result", {"tool_call_id": tool_id, "result": self._content(result)}, tool_id))
        if user_text is not None:
            plan.append(("user_message", {"text": self._content(user_text)}, None))
        return plan

    def _llm_data(self, provider: str, kwargs: dict[str, Any], parsed: _Parsed | None, streamed: bool) -> dict[str, Any]:
        data: dict[str, Any] = {"provider": provider, "model": (parsed.model if parsed else None) or kwargs.get("model")}
        if streamed:
            data["streamed"] = True
            data["note"] = "streaming response; output not captured"
        if parsed is not None:
            data["output"] = self._content(parsed.text)
            if parsed.tool_calls:
                data["tool_calls"] = [c["name"] for c in parsed.tool_calls]
            if parsed.usage:
                data["token_usage"] = parsed.usage
        return data

    # -- wrappers -------------------------------------------------------------

    def wrap_sync(self, original: Callable[..., Any], provider: str, shape: str) -> Callable[..., Any]:
        inst = self

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _in_call.get():
                return original(*args, **kwargs)
            token = _in_call.set(True)
            try:
                return _record(args, kwargs)
            finally:
                _in_call.reset(token)

        def _record(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            parent = None
            for event, data, tool_id in inst._before(kwargs, shape):
                tool_parent = inst._tool_event(tool_id) if tool_id else None
                eid = inst._write_sync(event, data, tool_parent)
                parent = eid or parent
            try:
                resp = original(*args, **kwargs)
            except Exception as exc:
                inst._write_sync("llm_error", {"provider": provider, "model": kwargs.get("model"), "error": _clip(repr(exc))}, parent)
                raise
            streamed = bool(kwargs.get("stream"))
            parsed = None if streamed else _safe_parse(shape, resp, kwargs.get("model"))
            llm_id = inst._write_sync("llm_end", inst._llm_data(provider, kwargs, parsed, streamed), parent)
            for call in parsed.tool_calls if parsed else []:
                eid = inst._write_sync("tool_call", {"tool": call["name"], "tool_call_id": call["id"], "arguments": inst._content(call["arguments"])}, llm_id)
                inst._remember_tool(call["id"], eid)
            return resp

        wrapper.__wrapped__ = original  # type: ignore[attr-defined]
        return wrapper

    def wrap_async(self, original: Callable[..., Any], provider: str, shape: str) -> Callable[..., Any]:
        inst = self

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if _in_call.get():
                return await original(*args, **kwargs)
            token = _in_call.set(True)
            try:
                return await _record(args, kwargs)
            finally:
                _in_call.reset(token)

        async def _record(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            parent = None
            for event, data, tool_id in inst._before(kwargs, shape):
                tool_parent = inst._tool_event(tool_id) if tool_id else None
                eid = await inst._write_async(event, data, tool_parent)
                parent = eid or parent
            try:
                resp = await original(*args, **kwargs)
            except Exception as exc:
                await inst._write_async("llm_error", {"provider": provider, "model": kwargs.get("model"), "error": _clip(repr(exc))}, parent)
                raise
            streamed = bool(kwargs.get("stream"))
            parsed = None if streamed else _safe_parse(shape, resp, kwargs.get("model"))
            llm_id = await inst._write_async("llm_end", inst._llm_data(provider, kwargs, parsed, streamed), parent)
            for call in parsed.tool_calls if parsed else []:
                eid = await inst._write_async("tool_call", {"tool": call["name"], "tool_call_id": call["id"], "arguments": inst._content(call["arguments"])}, llm_id)
                inst._remember_tool(call["id"], eid)
            return resp

        wrapper.__wrapped__ = original  # type: ignore[attr-defined]
        return wrapper

    def uninstrument(self) -> None:
        """Restore the original provider methods."""
        for owner, attr, original in reversed(self._patches):
            setattr(owner, attr, original)
        self._patches.clear()
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
        global _active
        if _active is self:
            _active = None


def _safe_parse(shape: str, resp: Any, model: str | None) -> _Parsed | None:
    try:
        return _PARSERS[shape](resp, model)
    except Exception as exc:
        logger.warning("zizkadb.instrument: could not read the %s response: %s", shape, exc)
        return None


_active: Instrumentation | None = None


def instrument(
    db: Any,
    agent: str,
    *,
    providers: tuple[str, ...] = ("openai", "anthropic", "litellm"),
    capture_content: bool = True,
) -> Instrumentation:
    """Record every OpenAI / Anthropic / LiteLLM chat call as causally linked events.

    Args:
        db: a :class:`zizkadb.ZizkaDB` client.
        agent: agent id the events are logged under.
        providers: which installed libraries to patch; missing ones are skipped.
        capture_content: store prompt/output text and tool arguments (clipped to
            4,000 characters). ``False`` records only models, tool names, token
            usage and the causal links.

    Returns:
        An :class:`Instrumentation`; call ``.uninstrument()`` to stop.

    Calling ``instrument()`` again replaces the previous instrumentation.
    """
    global _active
    if _active is not None:
        _active.uninstrument()
    inst = Instrumentation(db, agent, capture_content)
    patched = []
    for provider in providers:
        for module_path, cls_name, attr, kind, shape in _TARGETS.get(provider, []):
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                continue
            owner = getattr(module, cls_name, None) if cls_name else module
            original = getattr(owner, attr, None) if owner is not None else None
            if original is None:
                continue
            wrap = inst.wrap_async if kind == "async" else inst.wrap_sync
            setattr(owner, attr, wrap(original, provider, shape))
            inst._patches.append((owner, attr, original))
            patched.append(f"{provider}:{cls_name or module_path}.{attr}")
    if not patched:
        logger.warning("zizkadb.instrument: none of %s is installed; nothing to record", ", ".join(providers))
    _active = inst
    return inst


__all__ = ["instrument", "Instrumentation"]
