"""Tests for zizkadb CLI."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, patch

import pytest

from zizkadb import cli


def test_cli_demo_registers_subcommand():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    demo_p = sub.add_parser("demo")
    demo_p.set_defaults(func=cli.cmd_demo)
    args = parser.parse_args(["demo"])
    assert args.command == "demo"
    assert args.func is cli.cmd_demo


@pytest.mark.asyncio
async def test_run_support_order_delay_demo(monkeypatch):
    from zizkadb.demo_run import run_support_order_delay_demo

    mock_chain = AsyncMock()
    mock_chain.print = lambda: None

    mock_db = AsyncMock()
    mock_db.log = AsyncMock(
        side_effect=[
            type("R", (), {"event_id": "e1"})(),
            type("R", (), {"event_id": "e2"})(),
            type("R", (), {"event_id": "e3"})(),
        ]
    )
    mock_db.why = AsyncMock(return_value=mock_chain)
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=None)

    with patch("zizkadb.demo_run.ZizkaDB", return_value=mock_db):
        event_id = await run_support_order_delay_demo("http://localhost:8000")

    assert event_id == "e3"
    assert mock_db.log.call_count == 3
    mock_db.why.assert_awaited_once_with("e3")


def test_dashboard_activity_url_uses_cloud_for_remote_hostname_containing_localhost():
    from zizkadb.demo_run import dashboard_activity_url

    assert dashboard_activity_url("https://localhost.example.com") == (
        "https://db.zizka.ai/dashboard/activity?agent=support-bot"
    )
