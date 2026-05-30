"""Lightweight alerting helper (Slack incoming webhook).

Used by the Dagster failure sensor to notify on pipeline / schema-drift failures.
No-ops (returns False) when `settings.slack_webhook_url` is unset, so the rest of the
stack works without any alerting configured.
"""
from __future__ import annotations

import logging

import httpx

from danang_realestate.config import settings

logger = logging.getLogger(__name__)


def post_slack(text: str, *, webhook_url: str | None = None, timeout: float = 10.0) -> bool:
    """Post `text` to a Slack incoming webhook. Returns True if delivered.

    Returns False (without raising) when no webhook is configured or the POST fails —
    alerting must never take down the pipeline it is reporting on.
    """
    url = webhook_url if webhook_url is not None else settings.slack_webhook_url
    if not url:
        logger.info("Slack webhook not configured; skipping alert: %s", text)
        return False
    try:
        resp = httpx.post(url, json={"text": text}, timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as exc:  # pragma: no cover - network failure path
        logger.warning("Failed to post Slack alert: %s", exc)
        return False
