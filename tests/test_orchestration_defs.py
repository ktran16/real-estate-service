"""Smoke tests for the Dagster Definitions wiring (jobs / schedules / sensors registered).

Skips when the `dagster` extra isn't installed (it's optional); CI installs it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("dagster")

from danang_realestate import orchestration as orch  # noqa: E402


def _names(items) -> set[str]:
    return {i.name for i in items}


def test_expected_jobs_registered():
    jobs = _names(orch.defs.jobs)
    assert {
        "daily_refresh",
        "weekly_maintenance",
        "schema_drift_check",
        "source_freshness_check",
        "heartbeat",
    } <= jobs


def test_expected_schedules_registered():
    schedules = _names(orch.defs.schedules)
    assert {
        "daily_refresh_schedule",
        "weekly_maintenance_schedule",
        "schema_drift_schedule",
        "source_freshness_schedule",
        "heartbeat_schedule",
    } <= schedules


def test_expected_sensors_registered():
    sensors = _names(orch.defs.sensors)
    assert {"pipeline_failure_alert", "mart_drop_alert", "deals_alert"} <= sensors


def test_deals_mart_in_publish_list():
    # The deals mart (P4 #15) must be published to Postgres for Metabase.
    assert "deals" in orch.MARTS
    # And the four P4 #14 richer marts.
    for mart in (
        "price_per_sqm_by_ward",
        "listing_days_on_market",
        "listing_velocity",
        "broker_concentration",
    ):
        assert mart in orch.MARTS


def test_heartbeat_op_noops_without_webhook(monkeypatch):
    # emit_heartbeat must not raise when no Slack webhook is configured.
    monkeypatch.setattr(orch, "post_slack", lambda *a, **k: False)
    result = orch.heartbeat.execute_in_process()
    assert result.success
