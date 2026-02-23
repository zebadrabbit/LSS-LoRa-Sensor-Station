"""
tests/test_alerts.py — Unit tests for alerts.py.

Tests cover:
  - Rate limiting suppresses repeated alerts within the window
  - Rate limit expires after the configured period
  - Teams dispatch calls the correct URL (mocked requests)
  - Email dispatch calls SMTP (mocked smtplib)
  - test_teams returns success/failure pair
  - test_email returns failure when no recipients configured
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from lss_basestation.alerts import AlertManager


@pytest.fixture
def alert_mgr():
    return AlertManager(
        teams_webhook_url="https://example.com/webhook",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="secret",
        smtp_from="lss@example.com",
        smtp_to=["admin@example.com"],
        rate_limit_seconds=60,
    )


# ============================================================
# Rate limiting
# ============================================================

def test_rate_limit_suppresses(alert_mgr):
    calls = []
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=200)
        alert_mgr.send("Test", "body", key="test_key")
        # Give async thread a moment
        time.sleep(0.1)
        first_count = mock_req.post.call_count
        # Second call with same key — should be suppressed
        alert_mgr.send("Test", "body", key="test_key")
        time.sleep(0.1)
        assert mock_req.post.call_count == first_count  # no new call


def test_rate_limit_different_keys(alert_mgr):
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=200)
        alert_mgr.send("A", "body", key="key_a")
        alert_mgr.send("B", "body", key="key_b")
        time.sleep(0.2)
        assert mock_req.post.call_count >= 2


def test_no_rate_limit_when_key_empty(alert_mgr):
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=200)
        # Empty key bypasses rate limiting
        alert_mgr.send("X", "body", key="")
        alert_mgr.send("X", "body", key="")
        time.sleep(0.2)
        assert mock_req.post.call_count >= 2


def test_rate_limit_expires(alert_mgr):
    """After window expires, the same key can fire again."""
    mgr = AlertManager(
        teams_webhook_url="https://example.com/webhook",
        rate_limit_seconds=0,
    )
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=200)
        mgr.send("A", "body", key="k")
        time.sleep(0.1)
        mgr.send("A", "body", key="k")
        time.sleep(0.1)
        assert mock_req.post.call_count >= 2


# ============================================================
# Teams dispatch
# ============================================================

def test_teams_success(alert_mgr):
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=200)
        ok, msg = alert_mgr.test_teams("hello")
        assert ok is True
        assert msg == "OK"


def test_teams_http_error(alert_mgr):
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=500, text="err")
        ok, msg = alert_mgr.test_teams()
        assert ok is False
        assert "500" in msg


def test_teams_request_exception(alert_mgr):
    with patch("lss_basestation.alerts._requests") as mock_req:
        mock_req.post.side_effect = Exception("timeout")
        ok, msg = alert_mgr.test_teams()
        assert ok is False
        assert "timeout" in msg


def test_teams_no_url():
    mgr = AlertManager()
    ok, msg = mgr.test_teams()
    assert ok is False


# ============================================================
# Email dispatch
# ============================================================

def test_email_success(alert_mgr):
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=instance)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        ok, msg = alert_mgr.test_email()
        assert ok is True


def test_email_no_recipients():
    mgr = AlertManager(smtp_host="smtp.example.com")
    ok, msg = mgr.test_email()
    assert ok is False
    assert "No recipients" in msg


def test_email_smtp_exception(alert_mgr):
    with patch("smtplib.SMTP", side_effect=Exception("connection refused")):
        ok, msg = alert_mgr.test_email()
        assert ok is False
        assert "connection refused" in msg
