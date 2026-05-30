"""Smoke tests for the Typer CLI (wiring, help, exit codes) — no network."""
from __future__ import annotations

from typer.testing import CliRunner

from danang_realestate.cli import app

runner = CliRunner()


def test_app_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("scrape", "rescrape", "geocode", "transform", "run-all", "refresh-wards"):
        assert cmd in result.output


def test_each_command_has_help():
    for cmd in ("scrape", "rescrape", "geocode", "transform", "run-all"):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"


def test_transform_exits_nonzero_without_dbt_dir(tmp_path, monkeypatch):
    # Run from a directory with no dbt/ project → command should fail cleanly (exit 1).
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["transform"])
    assert result.exit_code == 1


def test_scrape_invokes_scraper_and_loader(monkeypatch, tmp_path):
    """`scrape` wires source → scraper.scrape → load_listings without touching the network."""
    from danang_realestate import cli

    calls = {}

    class FakeScraper:
        def scrape(self, transaction_type, limit):
            calls["scrape"] = (transaction_type, limit)
            return ["listing-a", "listing-b"]

    monkeypatch.setattr(cli, "init_db", lambda: calls.setdefault("init_db", True))
    monkeypatch.setattr(cli, "get_scraper", lambda source, client: FakeScraper())
    monkeypatch.setattr(cli, "SafeHTTPClient", lambda: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(
        cli, "load_listings", lambda conn, listings: calls.setdefault("loaded", listings)
    )
    monkeypatch.setattr(cli, "get_connection", lambda: type("Conn", (), {"close": lambda self: None})())

    result = runner.invoke(app, ["scrape", "--type", "sale", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert calls["scrape"] == ("sale", 5)
    assert calls["loaded"] == ["listing-a", "listing-b"]
