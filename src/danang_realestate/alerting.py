"""Lightweight alerting helpers (Slack incoming webhook + optional SMTP email).

Used by the Dagster sensors to notify on pipeline / schema-drift failures. Each channel
no-ops (returns False) when it isn't configured, and NEVER raises — alerting must not take
down the pipeline it is reporting on. `notify()` fans out to both channels.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

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


def post_email(subject: str, body: str, *, timeout: float = 15.0) -> bool:
    """Send an email alert via SMTP. Returns True if sent.

    No-op (returns False) unless `smtp_host`, `alert_email_from` and `alert_email_to` are all
    configured; never raises (a broken mail server must not break the pipeline).
    """
    s = settings
    recipients = [addr.strip() for addr in s.alert_email_to.split(",") if addr.strip()]
    if not (s.smtp_host and s.alert_email_from and recipients):
        logger.info("Email alerting not configured; skipping: %s", subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s.alert_email_from
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=timeout) as server:
            if s.smtp_starttls:
                server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        return True
    except Exception as exc:  # pragma: no cover - network/SMTP failure path
        logger.warning("Failed to send email alert: %s", exc)
        return False


def notify(text: str, *, subject: str = "Da Nang pipeline alert") -> dict[str, bool]:
    """Fan out an alert to all configured channels (Slack + email). Returns per-channel status."""
    return {"slack": post_slack(text), "email": post_email(subject, text)}
