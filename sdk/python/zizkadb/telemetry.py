"""
Anonymous install telemetry for ZizkaDB packages.

One ping per process per package (sdk) — install adoption, not API usage.
install_id is stable per machine (~/.zizkadb/install_id).

Self-hosted (localhost Docker) installs are counted with mode=self-hosted.

Each ping also says where it runs (env: ci, container or machine), so
CI runs are not counted as installs.

What is NOT sent: API keys, agent names, event data, IP address, hostname.

Opt out: export ZIZKADB_TELEMETRY=false
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import urllib.request

from .install_id import get_or_create_install_id

_TELEMETRY_URL = "https://db.zizka.ai/v1/telemetry"
_sent: set[str] = set()  # one ping per sdk per process

_SDK_DIST_NAMES: dict[str, str] = {
    "python": "zizkadb-sdk",
    "langchain": "zizkadb-langchain",
    "crewai": "zizkadb-crewai",
    "mcp": "zizkadb-mcp",
    "livekit": "zizkadb-livekit",
}


def _sdk_version(sdk: str) -> str:
    dist = _SDK_DIST_NAMES.get(sdk, "zizkadb-sdk")
    try:
        from importlib.metadata import version

        return version(dist)
    except Exception:
        if sdk == "python":
            try:
                from zizkadb import __version__

                return __version__
            except Exception:
                pass
        return "unknown"


# Variables set by CI services (GitHub Actions, GitLab, CircleCI, Travis,
# Buildkite, Jenkins, Azure Pipelines, TeamCity, Bitbucket, AWS CodeBuild...).
_CI_VARS = (
    "CI",
    "CONTINUOUS_INTEGRATION",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "CIRCLECI",
    "TRAVIS",
    "BUILDKITE",
    "JENKINS_URL",
    "TF_BUILD",
    "TEAMCITY_VERSION",
    "BITBUCKET_BUILD_NUMBER",
    "CODEBUILD_BUILD_ID",
    "DRONE",
    "APPVEYOR",
    "SEMAPHORE",
)


def detect_env() -> str:
    """Where this process runs: "ci", "container" or "machine"."""
    for name in _CI_VARS:
        value = os.getenv(name, "").strip().lower()
        if value and value not in ("false", "0", "no", "off"):
            return "ci"
    if (
        os.path.exists("/.dockerenv")
        or os.path.exists("/run/.containerenv")
        or os.getenv("KUBERNETES_SERVICE_HOST")
    ):
        return "container"
    return "machine"


def _default_mode() -> str:
    host = os.getenv("ZIZKADB_HOST", "") or os.getenv("AGENTDB_HOST", "")
    h = host.lower()
    if "localhost" in h or "127.0.0.1" in h or "0.0.0.0" in h:
        return "self-hosted"
    return "cloud"


def _send(mode: str, sdk: str, sdk_version: str) -> None:
    payload = json.dumps(
        {
            "install_id": get_or_create_install_id(),
            "sdk": sdk,
            "sdk_version": sdk_version,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "os": platform.system(),
            "mode": mode,
            "env": detect_env(),
        }
    ).encode()

    req = urllib.request.Request(
        _TELEMETRY_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=2)


def ping_on_install(
    mode: str | None = None,
    sdk: str = "python",
    sdk_version: str | None = None,
) -> None:
    """Fire-and-forget: one install ping per process for each sdk package."""
    if sdk in _sent:
        return
    if os.getenv("ZIZKADB_TELEMETRY", "").lower() in ("false", "0", "no", "off"):
        return
    _sent.add(sdk)

    resolved_mode = mode or _default_mode()
    resolved_version = sdk_version or _sdk_version(sdk)

    def _run() -> None:
        try:
            _send(resolved_mode, sdk[:32], resolved_version[:32])
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def ping_on_use(mode: str = "cloud", sdk: str = "python") -> None:
    """Back-compat alias — install is counted at import/construct time, not on log()."""
    ping_on_install(mode=mode, sdk=sdk)


def ping(mode: str = "cloud", sdk: str = "python") -> None:
    """Back-compat alias for ping_on_install."""
    ping_on_install(mode=mode, sdk=sdk)
