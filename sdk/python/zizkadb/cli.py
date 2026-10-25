"""zizkadb CLI — scaffold agent projects and run the OSS lineage demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from zizkadb.client import ZizkaDB

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

TEMPLATES = ("basic", "openai", "langchain", "crewai", "mcp-cursor", "langgraph", "rag", "livekit")
DEFAULT_HOST = "http://localhost:8000"


def _copy_template(name: str, dest: Path) -> None:
    src = TEMPLATES_DIR / name
    if not src.is_dir():
        raise SystemExit(f"Unknown template: {name}")
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"Directory not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, _dirnames, filenames in os.walk(src):
        for name in filenames:
            if name == ".DS_Store" or name.endswith(".pyc"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(src)
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)


def cmd_init(args: argparse.Namespace) -> None:
    dest = Path(args.name).resolve()
    _copy_template(args.template, dest)
    print(f"✓ Created {dest.name}/ ({args.template} template)")
    print()
    print("Next:")
    print(f"  cd {dest.name}")
    print("  cp .env.example .env   # add ZIZKADB_API_KEY or ZIZKADB_HOST")
    print("  pip install -r requirements.txt")
    if args.template == "mcp-cursor":
        print("  # Copy mcp.json → ~/.cursor/mcp.json and reload Cursor")
    else:
        print("  python agent.py")
        print("  # Stack not running? curl -fsSL …/quickstart-remote.sh | bash")
        print("  # Then: zizkadb demo")


def cmd_demo(args: argparse.Namespace) -> None:
    host = args.host or os.getenv("ZIZKADB_HOST", DEFAULT_HOST)
    try:
        from zizkadb.demo_run import run_support_order_delay_demo

        asyncio.run(run_support_order_delay_demo(host))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "\nStart the OSS stack first:\n"
            "  git clone https://github.com/ZIZKA-AI-SL/ZizkaDB.git && cd ZizkaDB\n"
            "  bash scripts/quickstart.sh\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from e


def _client_from_env() -> ZizkaDB:
    api_key = os.getenv("ZIZKADB_API_KEY") or os.getenv("AGENTDB_API_KEY") or ""
    host = os.getenv("ZIZKADB_HOST", DEFAULT_HOST)
    return ZizkaDB(api_key=api_key, host=host)


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_why(args: argparse.Namespace) -> None:
    async def run() -> None:
        async with _client_from_env() as db:
            _print_json(await db.why(args.event_id, depth=args.depth))

    asyncio.run(run())


def cmd_baseline(args: argparse.Namespace) -> None:
    async def run() -> None:
        async with _client_from_env() as db:
            _print_json(
                await db.baseline(
                    args.agent,
                    recent_window=args.recent_window,
                    window=args.window or None,
                )
            )

    asyncio.run(run())


def cmd_token_usage(args: argparse.Namespace) -> None:
    async def run() -> None:
        async with _client_from_env() as db:
            _print_json(
                await db.token_usage(
                    args.agent,
                    args.from_,
                    args.to,
                    granularity=args.granularity or None,
                )
            )

    asyncio.run(run())


def cmd_doctor(args: argparse.Namespace) -> None:
    import sys
    import httpx

    host = (args.host or os.getenv("ZIZKADB_HOST", DEFAULT_HOST)).rstrip("/")
    failures = 0

    def ok(msg: str) -> None:
        print(f"  ✓ {msg}")

    def fail(msg: str) -> None:
        nonlocal failures
        failures += 1
        print(f"  ✗ {msg}", file=sys.stderr)

    print(f"ZizkaDB doctor — {host}\n")

    if sys.version_info < (3, 10):
        fail(f"Python {sys.version_info.major}.{sys.version_info.minor} — need >= 3.10")
    else:
        ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    try:
        r = httpx.get(f"{host}/health/deep", timeout=10)
        if r.is_success:
            ok("API /health/deep reachable")
            checks = r.json().get("checks", {})
            required = {"postgres", "redis"}
            for name, check in checks.items():
                if check.get("ok"):
                    ok(f"  {name}")
                elif args.minimal and name not in required:
                    ok(f"  {name} (optional in --minimal mode)")
                else:
                    fail(f"  {name}: {check}")
        else:
            fail(f"API health failed: {r.status_code}")
    except Exception as e:
        fail(f"API unreachable: {e}")

    try:
        async def probe() -> None:
            async with ZizkaDB(host=host) as db:
                result = await db.log(
                    agent=args.agent,
                    event="doctor_ping",
                    data={"source": "zizkadb doctor"},
                )
                ok(f"Write test event {result.event_id[:8]}…")

        asyncio.run(probe())
    except Exception as e:
        fail(f"Write test failed: {e}")

    if not args.minimal:
        try:
            import zizkadb_mcp  # noqa: F401

            ok("MCP package importable")
        except Exception as e:
            fail(f"MCP import: {e}")

    if failures:
        print(f"\n{failures} check(s) failed", file=sys.stderr)
        raise SystemExit(1)
    print("\nAll checks passed")


def cmd_token_opt(args: argparse.Namespace) -> None:
    async def run() -> None:
        async with _client_from_env() as db:
            _print_json(
                await db.token_optimization(
                    args.agent,
                    from_=args.from_ or None,
                    to=args.to or None,
                    granularity=args.granularity or None,
                )
            )

    asyncio.run(run())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="zizkadb",
        description="ZizkaDB CLI — OSS demo and agent project scaffolding",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser(
        "demo",
        help="Run the support-bot causal lineage demo (requires local stack)",
    )
    demo_p.add_argument(
        "--host",
        default=None,
        help=f"API URL (default: ZIZKADB_HOST or {DEFAULT_HOST})",
    )
    demo_p.set_defaults(func=cmd_demo)

    init_p = sub.add_parser("init", help="Create a new agent project from a template")
    init_p.add_argument("name", help="Project directory name")
    init_p.add_argument(
        "--template",
        "-t",
        choices=TEMPLATES,
        default="basic",
        help="Starter template (default: basic)",
    )
    init_p.set_defaults(func=cmd_init)

    why_p = sub.add_parser("why", help="Print causal chain for an event")
    why_p.add_argument("event_id", help="Event UUID")
    why_p.add_argument("--depth", type=int, default=10, help="Max chain depth")
    why_p.set_defaults(func=cmd_why)

    baseline_p = sub.add_parser("baseline", help="Behavioral baseline for an agent")
    baseline_p.add_argument("agent", help="Agent id")
    baseline_p.add_argument("--recent-window", type=int, default=50)
    baseline_p.add_argument("--window", choices=("24h", "7d", "30d"), default="")
    baseline_p.set_defaults(func=cmd_baseline)

    tu_p = sub.add_parser("token-usage", help="Token usage report for an agent")
    tu_p.add_argument("agent")
    tu_p.add_argument("--from", dest="from_", required=True)
    tu_p.add_argument("--to", required=True)
    tu_p.add_argument("--granularity", choices=("hour", "day", "week"), default="")
    tu_p.set_defaults(func=cmd_token_usage)

    to_p = sub.add_parser("token-opt", help="Token optimization suggestions")
    to_p.add_argument("agent")
    to_p.add_argument("--from", dest="from_", default="")
    to_p.add_argument("--to", default="")
    to_p.add_argument("--granularity", choices=("day", "week"), default="")
    to_p.set_defaults(func=cmd_token_opt)

    doctor_p = sub.add_parser("doctor", help="Verify local stack and SDK connectivity")
    doctor_p.add_argument("--host", default=None, help=f"API URL (default: {DEFAULT_HOST})")
    doctor_p.add_argument("--agent", default="doctor-agent", help="Agent id for write test")
    doctor_p.add_argument(
        "--minimal",
        action="store_true",
        help="Golden-path/CI mode: require postgres+redis and write test only",
    )
    doctor_p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
