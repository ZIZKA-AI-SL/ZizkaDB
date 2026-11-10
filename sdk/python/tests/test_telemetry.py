"""Telemetry ping behavior."""

from __future__ import annotations

import zizkadb.telemetry as telemetry


def test_ping_on_install_self_hosted(monkeypatch):
    telemetry._sent.clear()
    called: list[tuple[str, str]] = []

    monkeypatch.setattr(
        telemetry,
        "_send",
        lambda mode, sdk, sdk_version: called.append((sdk, mode)),
    )
    monkeypatch.delenv("ZIZKADB_TELEMETRY", raising=False)

    telemetry.ping_on_install(mode="self-hosted", sdk="python")
    assert called == [("python", "self-hosted")]


def test_ping_on_install_cloud(monkeypatch):
    telemetry._sent.clear()
    called: list[tuple[str, str]] = []

    monkeypatch.setattr(
        telemetry,
        "_send",
        lambda mode, sdk, sdk_version: called.append((sdk, mode)),
    )
    monkeypatch.delenv("ZIZKADB_TELEMETRY", raising=False)

    telemetry.ping_on_install(mode="cloud", sdk="langchain")
    assert called == [("langchain", "cloud")]


def test_ping_respects_opt_out(monkeypatch):
    telemetry._sent.clear()
    called: list[tuple[str, str]] = []

    monkeypatch.setattr(
        telemetry,
        "_send",
        lambda mode, sdk, sdk_version: called.append((sdk, mode)),
    )
    monkeypatch.setenv("ZIZKADB_TELEMETRY", "false")

    telemetry.ping_on_install(mode="cloud", sdk="crewai")
    assert called == []


def test_ping_once_per_sdk_per_process(monkeypatch):
    telemetry._sent.clear()
    called: list[str] = []

    monkeypatch.setattr(
        telemetry,
        "_send",
        lambda mode, sdk, sdk_version: called.append(sdk),
    )
    monkeypatch.delenv("ZIZKADB_TELEMETRY", raising=False)

    telemetry.ping_on_install(mode="cloud", sdk="python")
    telemetry.ping_on_install(mode="cloud", sdk="python")
    telemetry.ping_on_install(mode="cloud", sdk="mcp")
    assert called == ["python", "mcp"]


def _clear_ci(monkeypatch):
    for name in telemetry._CI_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)


def test_detect_env_ci(monkeypatch):
    _clear_ci(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert telemetry.detect_env() == "ci"


def test_detect_env_ci_false_is_not_ci(monkeypatch):
    _clear_ci(monkeypatch)
    monkeypatch.setenv("CI", "false")
    monkeypatch.setattr(telemetry.os.path, "exists", lambda p: False)
    assert telemetry.detect_env() == "machine"


def test_detect_env_container(monkeypatch):
    _clear_ci(monkeypatch)
    monkeypatch.setattr(telemetry.os.path, "exists", lambda p: p == "/.dockerenv")
    assert telemetry.detect_env() == "container"


def test_detect_env_machine(monkeypatch):
    _clear_ci(monkeypatch)
    monkeypatch.setattr(telemetry.os.path, "exists", lambda p: False)
    assert telemetry.detect_env() == "machine"


def test_send_includes_env(monkeypatch):
    _clear_ci(monkeypatch)
    monkeypatch.setenv("CI", "1")
    sent: list[dict] = []

    def fake_urlopen(req, timeout):
        sent.append(__import__("json").loads(req.data))

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake_urlopen)
    telemetry._send("cloud", "python", "0.6.1")
    assert sent[0]["env"] == "ci"
