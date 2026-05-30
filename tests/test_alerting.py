"""Tests for Slack alerting + the Dagster schema-drift op."""
from __future__ import annotations

import pytest

from danang_realestate import alerting
from danang_realestate.validation.schema_validator import SchemaDriftReport


def test_post_slack_noop_without_webhook():
    # No webhook configured anywhere -> returns False, never raises, never posts.
    assert alerting.post_slack("hello", webhook_url="") is False


def test_post_slack_posts_when_configured(monkeypatch):
    calls = {}

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["json"] = json

        class _Resp:
            def raise_for_status(self):
                return None

        return _Resp()

    monkeypatch.setattr(alerting.httpx, "post", fake_post)
    assert alerting.post_slack("boom", webhook_url="https://hooks.example/x") is True
    assert calls["url"] == "https://hooks.example/x"
    assert calls["json"] == {"text": "boom"}


def test_post_slack_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(alerting.httpx, "post", boom)
    # Alerting must never propagate failures to the pipeline it reports on.
    assert alerting.post_slack("x", webhook_url="https://hooks.example/x") is False


def test_check_api_schema_raises_on_drift(monkeypatch):
    from dagster import build_op_context

    from danang_realestate import orchestration

    # A report with an unexpected/critical-missing field -> has_drift() True.
    drift = SchemaDriftReport(model_fields={"ad_id"}, api_fields={"ad_id", "surprise"})
    assert drift.has_drift() is True

    monkeypatch.setattr(orchestration, "validate_schema", lambda client: drift)
    monkeypatch.setattr(orchestration, "SafeHTTPClient", lambda *a, **k: _DummyClient())

    with pytest.raises(RuntimeError, match="schema drift"):
        orchestration.check_api_schema(build_op_context())


def test_check_api_schema_passes_when_clean(monkeypatch):
    from dagster import build_op_context

    from danang_realestate import orchestration

    clean = SchemaDriftReport(model_fields={"ad_id"}, api_fields={"ad_id"})
    assert clean.has_drift() is False

    monkeypatch.setattr(orchestration, "validate_schema", lambda client: clean)
    monkeypatch.setattr(orchestration, "SafeHTTPClient", lambda *a, **k: _DummyClient())

    # Should not raise.
    orchestration.check_api_schema(build_op_context())


class _DummyClient:
    def close(self):
        pass
