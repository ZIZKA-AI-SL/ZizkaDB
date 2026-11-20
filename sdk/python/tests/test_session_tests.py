"""`zizkadb test from-session`: a recorded session becomes a runnable pytest test."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zizkadb import cli
from zizkadb.models import Event
from zizkadb.session_tests import build_snapshot, render_test, session_test_name
from zizkadb.testing import capture

SDK_ROOT = Path(__file__).resolve().parents[1]


def _ev(n, event, data, parent=None):
    return Event(
        event_id=f"e{n}",
        agent="support-bot",
        timestamp=datetime(2026, 11, 11, 10, 0, n, tzinfo=timezone.utc),
        event=event,
        data=data,
        parent_id=parent,
        session_id="sess-abc-123",
        sequence_no=n,
    )


RECORDED = [
    _ev(1, "user_message", {"text": "Where is order 42?"}),
    _ev(2, "llm_end", {"output": None, "tool_calls": ["lookup_order"]}, "e1"),
    _ev(3, "tool_call", {"tool": "lookup_order", "arguments": {"id": 42}}, "e2"),
    _ev(4, "tool_result", {"result": "ships tomorrow"}, "e3"),
    _ev(5, "llm_end", {"output": "Order 42 ships tomorrow."}, "e4"),
]


def test_snapshot_summarises_the_session():
    snap = build_snapshot("sess-abc-123", "support-bot", RECORDED)
    assert snap["user_inputs"] == ["Where is order 42?"]
    assert snap["expected_tool_calls"] == ["lookup_order"]
    assert snap["recorded_final_output"] == "Order 42 ships tomorrow."
    assert snap["recorded_errors"] == [] and snap["event_count"] == 5


def test_snapshot_keeps_errors_and_other_tool_event_shapes():
    events = [
        _ev(1, "user_message", {"content": "refund?"}),
        _ev(2, "tool_start", {"tool_name": "search"}),
        _ev(3, "tool_error", {"error": "timeout"}),
    ]
    snap = build_snapshot("s", "bot", events)
    assert snap["user_inputs"] == ["refund?"]
    assert snap["expected_tool_calls"] == ["search"]
    assert snap["recorded_errors"] == [{"event": "tool_error", "error": "timeout"}]
    assert "ended with an error" in render_test(snap, "session_s", None)


def test_generated_source_is_valid_python():
    snap = build_snapshot("sess-abc-123", "support-bot", RECORDED)
    for entry in (None, "my_app.agent:answer"):
        compile(render_test(snap, session_test_name("sess-abc-123"), entry), "t.py", "exec")
    with pytest.raises(ValueError):
        render_test(snap, "x", "no_colon")


def test_cli_writes_files_and_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def iter_events(self, agent, session_id=None):
            assert (agent, session_id) == ("support-bot", "sess-abc-123")
            for e in reversed(RECORDED):  # the API returns newest first
                yield e

    monkeypatch.setattr(cli, "_client_from_env", lambda: FakeDB())
    argv = ["test", "from-session", "sess-abc-123", "--agent", "support-bot", "--out", str(tmp_path)]
    cli.main(argv)
    out = capsys.readouterr().out
    assert "tool path: lookup_order" in out
    test_file = tmp_path / "test_session_sess_abc_123.py"
    snap = json.loads((tmp_path / "zizkadb_sessions" / "session_sess_abc_123.json").read_text())
    assert test_file.exists() and snap["user_inputs"] == ["Where is order 42?"]
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(argv)
    cli.main(argv + ["--force"])


def test_cli_reports_an_empty_session(monkeypatch):
    class Empty:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def iter_events(self, agent, session_id=None):
            return
            yield

    monkeypatch.setattr(cli, "_client_from_env", lambda: Empty())
    with pytest.raises(SystemExit, match="No events"):
        cli.main(["test", "from-session", "nope", "--agent", "bot"])


# ── the generated test, run for real against a stand-in agent ─────────────────

FAKE_OPENAI = {
    "openai/__init__.py": "",
    "openai/resources/__init__.py": "",
    "openai/resources/chat/__init__.py": "",
    "openai/resources/chat/completions.py": """
        class Completions:
            def create(self, **kwargs):
                if any(m.get("role") == "tool" for m in kwargs["messages"]):
                    return {"model": "m", "choices": [{"message": {"content": "Order 42 ships tomorrow."}}]}
                tool = __import__("os").environ.get("FAKE_TOOL", "lookup_order")
                return {"model": "m", "choices": [{"message": {"content": None,
                        "tool_calls": [{"id": "c1", "function": {"name": tool, "arguments": "{}"}}]}}]}

        class AsyncCompletions:
            async def create(self, **kwargs):
                raise NotImplementedError
    """,
    "my_agent.py": """
        from openai.resources.chat.completions import Completions

        def answer(text):
            client = Completions()
            history = [{"role": "user", "content": text}]
            first = client.create(model="m", messages=history)
            call = first["choices"][0]["message"]["tool_calls"][0]
            history += [{"role": "assistant", "tool_calls": [call]},
                        {"role": "tool", "tool_call_id": call["id"], "content": "ships tomorrow"}]
            return client.create(model="m", messages=history)["choices"][0]["message"]["content"]
    """,
}


@pytest.fixture
def project(tmp_path):
    for rel, src in FAKE_OPENAI.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src))
    snap = build_snapshot("sess-abc-123", "support-bot", RECORDED)
    name = session_test_name("sess-abc-123")
    (tmp_path / "tests" / "zizkadb_sessions").mkdir(parents=True)
    (tmp_path / "tests" / "zizkadb_sessions" / f"{name}.json").write_text(json.dumps(snap))
    (tmp_path / "tests" / f"test_{name}.py").write_text(render_test(snap, name, "my_agent:answer"))
    return tmp_path


def _pytest(project, tool):
    env = dict(os.environ, PYTHONPATH=f"{project}{os.pathsep}{SDK_ROOT}", FAKE_TOOL=tool, ZIZKADB_TELEMETRY="false")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(project / "tests")],
        cwd=project, env=env, capture_output=True, text=True,
    )


def test_generated_test_passes_on_the_same_tool_path(project):
    result = _pytest(project, "lookup_order")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_generated_test_fails_when_the_agent_takes_another_path(project):
    result = _pytest(project, "cancel_order")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "cancel_order" in result.stdout


def test_capture_records_without_a_server(project, monkeypatch):
    monkeypatch.syspath_prepend(str(project))
    for mod in [m for m in sys.modules if m == "openai" or m.startswith("openai.") or m == "my_agent"]:
        monkeypatch.delitem(sys.modules, mod)
    import my_agent

    with capture(agent="bot") as run:
        assert my_agent.answer("Where is order 42?") == "Order 42 ships tomorrow."
    assert run.tool_calls == ["lookup_order"]
    assert [e.event for e in run.events] == ["user_message", "llm_end", "tool_call", "tool_result", "llm_end"]
    assert run.outputs == ["Order 42 ships tomorrow."] and not run.errors


def test_capture_pauses_and_restores_app_instrumentation(project, monkeypatch):
    import zizkadb
    from zizkadb import ZizkaDB, instrumentation

    monkeypatch.syspath_prepend(str(project))
    for mod in [m for m in sys.modules if m == "openai" or m.startswith("openai.")]:
        monkeypatch.delitem(sys.modules, mod)
    app = zizkadb.instrument(ZizkaDB(host="http://localhost:8000"), agent="app-bot", providers=("openai",), capture_content=False)
    try:
        with capture() as run:
            assert instrumentation._active is None
        restored = instrumentation._active
        assert restored is not None and restored.agent == "app-bot" and restored.capture_content is False
        assert restored.providers == ("openai",) and run.events == []
    finally:
        if instrumentation._active is not None:
            instrumentation._active.uninstrument()
        app.uninstrument()
