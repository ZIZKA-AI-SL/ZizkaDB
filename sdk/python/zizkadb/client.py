"""
ZizkaDB Python SDK

Usage:
    from zizkadb import ZizkaDB

    # Managed (cloud)
    db = ZizkaDB("zizkadb_live_xxxx")

    # Self-hosted
    db = ZizkaDB(host="http://localhost:8000")

    # Log an event
    await db.log(agent="my-bot", event="tool_call", data={"tool": "search"})

    # Why did something happen?
    chain = await db.why(event_id="...")
    chain.print()

    # Semantic search
    results = await db.search("what did the agent do about billing")

    # Time travel
    state = await db.at(agent="my-bot", timestamp=datetime(2026, 5, 1))
"""

import os
import sys
import httpx
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session_scope import SessionScope

from .models import Event, LogResult, CausalChain, AgentState, AgentInfo
from .exceptions import ZizkaDBError, AuthError, NotFoundError, RateLimitError, AgentScopeError
from .telemetry import ping_on_install as _telemetry_ping
from .context import LineageContext, resolve_log_lineage, record_log_result

CLOUD_HOST = "https://db.zizka.ai"
DEFAULT_DEV_API_KEY = "zizkadb_dev_local"


def _is_local_host(host: str) -> bool:
    h = host.lower()
    return "localhost" in h or "127.0.0.1" in h or "0.0.0.0" in h


def _api_key_from_env() -> str | None:
    """ZIZKADB_API_KEY preferred; AGENTDB_API_KEY kept for legacy managed users."""
    return os.getenv("ZIZKADB_API_KEY") or os.getenv("AGENTDB_API_KEY")


def _parse_timestamp(timestamp: datetime | str) -> datetime:
    """Normalize an at() timestamp: datetime as-is, ISO-8601 string parsed.

    The trailing 'Z' form is rewritten to '+00:00' because
    datetime.fromisoformat only parses 'Z' from Python 3.11, and the SDK
    supports 3.10.
    """
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str):
        value = timestamp.strip()
        if value.endswith(("Z", "z")):
            value = value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"Invalid ISO-8601 timestamp: {timestamp!r}") from None
    raise TypeError(
        "timestamp must be a datetime or an ISO-8601 string, "
        f"got {type(timestamp).__name__}"
    )


def _log_hints_enabled(base_url: str) -> bool:
    """Show CLI hints after log() on localhost unless ZIZKADB_QUIET is set."""
    quiet = os.getenv("ZIZKADB_QUIET", "").lower()
    if quiet in ("1", "true", "yes", "on"):
        return False
    if os.getenv("ZIZKADB_LOG_HINTS", "").lower() in ("1", "true", "yes", "on"):
        return True
    return _is_local_host(base_url)


# The hint is a first-run nudge, not a per-event log line. Agents that flush a
# whole session at once would otherwise print one line per event.
_hint_emitted = False


def _emit_log_hint(base_url: str, event_id: str) -> None:
    global _hint_emitted
    if _hint_emitted:
        return
    if _log_hints_enabled(base_url):
        _hint_emitted = True
        print(f"  → zizkadb why {event_id}", file=sys.stderr)


class ZizkaDB:
    """
    ZizkaDB client.

    Args:
        api_key: Your ZizkaDB API key (zizkadb_live_... or legacy agdb_live_...).
                 Get one at db.zizka.ai
        host:    URL of your self-hosted instance.
                 Defaults to ZizkaDB Cloud if api_key is provided.
        timeout: HTTP timeout in seconds. Default 10.
    """

    def __init__(
        self,
        api_key: str | None = None,
        host: str | None = None,
        timeout: float = 10.0,
    ):
        if not api_key and not host:
            raise ZizkaDBError(
                "Provide an api_key (cloud) or host (self-hosted).\n"
                "  Cloud:       ZizkaDB('zizkadb_live_...')\n"
                "  Self-hosted: ZizkaDB(host='http://localhost:8000')"
            )

        if not api_key and host:
            if _is_local_host(host):
                api_key = (
                    _api_key_from_env()
                    or os.getenv("DEV_API_KEY")
                    or DEFAULT_DEV_API_KEY
                )
            else:
                api_key = _api_key_from_env()
                if not api_key:
                    raise ZizkaDBError(
                        "Cloud host requires an API key.\n"
                        "  ZizkaDB('zizkadb_live_...') or set ZIZKADB_API_KEY in your environment.\n"
                        f"  Host: {host}"
                    )

        self._api_key = api_key
        self._base_url = host.rstrip("/") if host else CLOUD_HOST
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._telemetry_mode = "self-hosted" if host else "cloud"
        _telemetry_ping(mode=self._telemetry_mode, sdk="python")

    # ─────────────────────────────────────────
    # CONTEXT MANAGER
    # ─────────────────────────────────────────

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    # ─────────────────────────────────────────
    # LOG — log a single event
    # ─────────────────────────────────────────

    async def log(
        self,
        agent: str,
        event: str,
        data: dict[str, Any],
        parent_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> LogResult:
        """
        Log an event.

        Args:
            agent:      Your agent's identifier (e.g. "customer-support-bot")
            event:      Event type (e.g. "tool_call", "user_message", "error")
            data:       Event payload — any dict
            parent_id:  ID of the causing event (enables causal lineage)
            session_id: Group related events into a session
            metadata:   Optional extra metadata

        Returns:
            LogResult with event_id, timestamp, sequence_no, checksum
        """
        explicit_parent = parent_id is not None
        parent_id, session_id = resolve_log_lineage(
            agent=agent,
            event=event,
            parent_id=parent_id,
            session_id=session_id,
            explicit_parent=explicit_parent,
        )
        payload_data = dict(data)
        if token_usage is not None:
            payload_data["token_usage"] = token_usage
        response = await self._post(
            "/v1/events",
            {
                "agent": agent,
                "event": event,
                "data": payload_data,
                "parent_id": parent_id,
                "session_id": session_id,
                "metadata": metadata,
            },
        )
        result = LogResult.from_dict(response)
        record_log_result(agent, result)
        _emit_log_hint(self._base_url, result.event_id)
        return result

    def track(self, agent: str, session_id: str | None = None) -> LineageContext:
        """Auto-wire parent_id for logs inside this context."""
        return LineageContext(self, agent=agent, session_id=session_id)

    def session(self, session_id: str) -> "SessionScope":
        """Session-scoped helper for multi-agent timelines and cross-agent why()."""
        from .session_scope import SessionScope

        return SessionScope(self, session_id)

    async def log_fork(
        self,
        agent: str,
        events: list[tuple[str, dict[str, Any]]],
        *,
        session_id: str | None = None,
    ) -> list[LogResult]:
        """Log parallel branches from the same parent (e.g. parallel tool calls)."""
        from .context import get_lineage_state

        state = get_lineage_state()
        fork_parent = state.last_event_id if state and state.agent == agent else None
        results: list[LogResult] = []
        for event_type, data in events:
            results.append(
                await self.log(
                    agent=agent,
                    event=event_type,
                    data=data,
                    parent_id=fork_parent,
                    session_id=session_id or (state.session_id if state else None),
                )
            )
        if state is not None and state.agent == agent and results:
            state.last_event_id = results[-1].event_id
        return results

    # ─────────────────────────────────────────
    # QUERY — fetch events
    # ─────────────────────────────────────────

    async def query(
        self,
        agent: str,
        limit: int = 50,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: str | None = None,
        session_id: str | None = None,
    ) -> list[Event]:
        """
        Query events for an agent.

        Args:
            agent:      Agent identifier
            limit:      Max events to return (default 50, max 1000)
            before:     Only events before this time
            after:      Only events after this time
            event_type: Filter to a specific event type
            session_id: Filter to a specific session

        Returns:
            List of Event objects, newest first
        """
        params: dict[str, Any] = {"agent": agent, "limit": limit}
        if before:
            params["before"] = before.isoformat()
        if after:
            params["after"] = after.isoformat()
        if event_type:
            params["event_type"] = event_type
        if session_id:
            params["session_id"] = session_id

        response = await self._get("/v1/events", params)
        return [Event.from_dict(e) for e in response]

    # ─────────────────────────────────────────
    # WHY — causal chain for an event
    # ─────────────────────────────────────────

    async def why(self, event_id: str, depth: int = 10) -> CausalChain:
        """
        Get the causal chain for an event.
        Walks parent_id links to show WHY an event happened.

        Args:
            event_id: The event to explain
            depth:    How many levels up to trace (default 10)

        Returns:
            CausalChain — call .print() to display as a tree

        Example:
            tool = await db.log(...)
            chain = await db.why(tool.event_id)
            chain.print()
        """
        response = await self._get(
            f"/v1/events/{event_id}/why",
            {"depth": depth},
        )
        return CausalChain(
            event_id=response["event_id"],
            chain_length=response["chain_length"],
            chain=[Event.from_dict(e) for e in response["chain"]],
            chain_complete=response.get("chain_complete", True),
            orphan=response.get("orphan", False),
            depth_truncated=response.get("depth_truncated", False),
            scoped_agent_limited=response.get("scoped_agent_limited", False),
        )

    # ─────────────────────────────────────────
    # SEARCH — semantic search
    # ─────────────────────────────────────────

    async def search(
        self,
        query: str,
        agent: str | None = None,
        limit: int = 10,
    ) -> list[Event]:
        """
        Search agent history using natural language.

        Args:
            query: Natural language query
            agent: Limit search to a specific agent (optional)
            limit: Number of results (default 10)

        Returns:
            List of Events sorted by relevance score

        Example:
            results = await db.search("what happened with billing refunds")
        """
        response = await self._post(
            "/v1/search",
            {"query": query, "agent": agent, "limit": limit},
        )
        return [Event.from_dict(e) for e in response["results"]]

    # ─────────────────────────────────────────
    # AT — time travel
    # ─────────────────────────────────────────

    async def at(self, agent: str, timestamp: datetime | str) -> AgentState:
        """
        Reconstruct agent state at a specific point in time.

        Args:
            agent:     Agent identifier
            timestamp: Point in time to reconstruct state at — a datetime,
                       or an ISO-8601 string ("2026-05-01T15:00:00Z")

        Returns:
            AgentState with the reconstructed state dict

        Example:
            from datetime import datetime
            state = await db.at("my-bot", datetime(2026, 5, 1, 15, 0))
            print(state.state)
        """
        response = await self._get(
            "/v1/events/at",
            {"agent": agent, "timestamp": _parse_timestamp(timestamp).isoformat()},
        )
        return AgentState.from_dict(response)

    # ─────────────────────────────────────────
    # CONTEXT FOR — prompt-ready memory injection
    # ─────────────────────────────────────────

    async def context_for(
        self,
        agent: str,
        task: str,
        max_tokens: int = 2000,
        session_id: str | None = None,
    ) -> str:
        """
        Get a formatted memory block ready to inject into a system prompt.
        Combines recent events + semantically relevant past events.

        Args:
            agent:      Agent identifier
            task:       What the agent is about to do (natural language)
            max_tokens: Token budget for the context block (default 2000)
            session_id: Current session ID to exclude from context

        Returns:
            A formatted string — paste directly into your system prompt.

        Example:
            context = await db.context_for(
                agent="support-bot",
                task="user asking about their invoice"
            )
            messages = [
                {"role": "system", "content": f"You are a support agent.\\n\\n{context}"},
                {"role": "user",   "content": user_message},
            ]
        """
        response = await self._post(
            "/v1/memory/context",
            {
                "agent": agent,
                "task": task,
                "max_tokens": max_tokens,
                "session_id": session_id,
            },
        )
        return response.get("context", "")

    async def context_for_full(
        self,
        agent: str,
        task: str,
        max_tokens: int = 2000,
        session_id: str | None = None,
    ) -> dict:
        """
        Same as context_for() but returns the full response including
        source event references and token count.
        """
        return await self._post(
            "/v1/memory/context",
            {
                "agent": agent,
                "task": task,
                "max_tokens": max_tokens,
                "session_id": session_id,
            },
        )

    # ─────────────────────────────────────────
    # MEMORY DIFF — what changed after a session
    # ─────────────────────────────────────────

    async def memory_diff(self, session_id: str) -> dict:
        """
        Returns a summary of what happened in a session and what new
        patterns emerged compared to prior sessions.

        Args:
            session_id: The session to analyse

        Returns:
            Dict with: summary, event_count, event_types, causal_depth,
                       has_errors, new_event_types, top_events

        Example:
            diff = await db.memory_diff("sess_abc")
            print(diff["summary"])
            # "Session with agent 'support-bot': 12 events over 45s."
            if diff["has_errors"]:
                print("Errors detected:", diff["new_event_types"])
        """
        return await self._get(f"/v1/memory/diff/{session_id}", {})

    # ─────────────────────────────────────────
    # FORGET — GDPR compliance
    # ─────────────────────────────────────────

    async def forget(self, filter_key: str, filter_value: str) -> dict:
        """
        Delete all events where data[filter_key] == filter_value.
        Also removes vectors from the search index.

        Use this for GDPR right-to-erasure requests.

        Args:
            filter_key:   Field in event data to match (e.g. "user_id", "email")
            filter_value: Value to match (e.g. "user_123")

        Returns:
            Dict with deleted_events count and confirmation message

        Example:
            result = await db.forget("user_id", "user_123")
            print(result["deleted_events"])  # e.g. 47
        """
        return await self._delete(
            "/v1/memory/forget",
            {"filter_key": filter_key, "filter_value": filter_value},
        )

    # ─────────────────────────────────────────
    # AGENTS — list agents
    # ─────────────────────────────────────────

    async def agents(self) -> list[AgentInfo]:
        """List all agents in your project."""
        response = await self._get("/v1/agents", {})
        return [AgentInfo.from_dict(a) for a in response]

    # ─────────────────────────────────────────
    # BASELINE — behavioral baseline + drift signal
    # ─────────────────────────────────────────

    async def baseline(
        self,
        agent: str,
        recent_window: int = 50,
        window: str | None = None,
    ) -> dict:
        """
        Get the behavioral baseline for an agent.

        Splits this agent's sessions into two windows: the most recent N
        sessions (`recent`) and everything older (`baseline`). Returns
        event-type distribution, parent->child transition distribution,
        average session shape, and error rate for each window, plus a
        drift score (0 = identical, 1 = totally different) and the
        biggest behavioural changes between the two windows.

        Use this to answer "has this agent started behaving differently
        since I shipped v2?" before users notice.

        Args:
            agent:         Agent identifier
            recent_window: Number of most-recent sessions in the "recent"
                           window. Default 50.

        Returns:
            Dict with: status, sessions, recent_window, baseline, recent,
                       drift {score, verdict, biggest_changes, ...}

        Example:
            b = await db.baseline("support-bot")
            if b["drift"]["score"] > 0.15:
                print("Drift detected:", b["drift"]["biggest_changes"])
        """
        params: dict[str, Any] = {"recent_window": recent_window}
        if window:
            params["window"] = window
        return await self._get(
            f"/v1/agents/{agent}/baseline",
            params,
        )

    async def token_usage(
        self,
        agent: str,
        from_: str,
        to: str,
        granularity: str | None = None,
    ) -> dict:
        """Token usage and cost report for an agent over a date range."""
        params: dict[str, Any] = {"from": from_, "to": to}
        if granularity:
            params["granularity"] = granularity
        return await self._get(f"/v1/agents/{agent}/token-usage", params)

    async def token_optimization(
        self,
        agent: str,
        from_: str | None = None,
        to: str | None = None,
        granularity: str | None = None,
    ) -> dict:
        """Deterministic token optimization suggestions from real usage events."""
        params: dict[str, Any] = {}
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        if granularity:
            params["granularity"] = granularity
        return await self._get(f"/v1/agents/{agent}/token-optimization", params)

    async def suggestions(
        self,
        agent: str,
        from_: str | None = None,
        to: str | None = None,
        refresh: bool = False,
    ) -> dict:
        """AI-generated improvement suggestions for an agent."""
        params: dict[str, Any] = {}
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        if refresh:
            params["refresh"] = "true"
        return await self._get(f"/v1/agents/{agent}/suggestions", params)

    async def report(
        self,
        agent: str,
        from_: str,
        to: str,
        granularity: str | None = None,
    ) -> dict:
        """Consolidated date-ranged report for an agent."""
        params: dict[str, Any] = {"from": from_, "to": to}
        if granularity:
            params["granularity"] = granularity
        return await self._get(f"/v1/agents/{agent}/report", params)

    async def behavior_change(
        self,
        agent: str,
        window: str = "7d",
        from_ts: str | None = None,
        to_ts: str | None = None,
    ) -> dict:
        """Time-windowed behavior change vs the pre-window baseline epoch."""
        params: dict[str, Any] = {"window": window}
        if from_ts:
            params["from_ts"] = from_ts
        if to_ts:
            params["to_ts"] = to_ts
        return await self._get(f"/v1/agents/{agent}/behavior-change", params)

    # ─────────────────────────────────────────
    # HTTP HELPERS
    # ─────────────────────────────────────────

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client:
            return self._client
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        """Single entry point for every HTTP call.

        Handles client lifecycle, connection errors, and response mapping so the
        verb helpers (_post/_get/_delete) stay one line each.
        """
        client = await self._get_client()
        try:
            try:
                resp = await client.request(method, path, json=json, params=params)
            except httpx.ConnectError as e:
                raise ZizkaDBError(
                    f"Could not connect to ZizkaDB at {self._base_url}: {e}"
                ) from e
            return self._handle(resp)
        finally:
            if not self._client:
                await client.aclose()

    async def _post(self, path: str, body: dict) -> dict:
        return await self._request("POST", path, json=body)

    async def _get(self, path: str, params: dict) -> Any:
        return await self._request("GET", path, params=params)

    async def _delete(self, path: str, body: dict) -> Any:
        return await self._request("DELETE", path, json=body)

    def _auth_error_message(self, resp: httpx.Response) -> str:
        """Build a clear 401 message tailored to cloud vs self-hosted.

        A hardcoded "go to db.zizka.ai/dashboard" is wrong (and confusing) for a
        self-hoster whose keys live on their own instance, so point them at the
        right place and surface the server's own guidance when it sends any.
        """
        try:
            detail = resp.json().get("detail")
            # FastAPI sends a list for validation errors — only inline plain strings.
            server_detail = detail if isinstance(detail, str) else None
        except Exception:
            server_detail = None

        if self._base_url == CLOUD_HOST:
            base = "Invalid API key. Create an agent at db.zizka.ai/dashboard and copy its key."
        else:
            base = (
                f"Invalid API key for the ZizkaDB instance at {self._base_url}. "
                "Use a key generated by that instance."
            )

        if server_detail and server_detail not in base:
            return f"{base}\nServer: {server_detail}"
        return base

    def _handle(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise AuthError(self._auth_error_message(resp), status_code=401)
        if resp.status_code == 403:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise AgentScopeError(
                f"Agent mismatch (403): {detail}",
                status_code=403,
            )
        if resp.status_code == 404:
            raise NotFoundError("Resource not found", status_code=404)
        if resp.status_code == 429:
            raise RateLimitError("Rate limit exceeded", status_code=429)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ZizkaDBError(
                f"ZizkaDB error ({resp.status_code}): {detail}",
                status_code=resp.status_code,
            )
        return resp.json()
