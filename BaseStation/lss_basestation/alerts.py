"""
alerts.py — Microsoft Teams webhook and SMTP email notifications.

Both channels are fire-and-forget.  A rate limiter prevents the same
alert type from being sent more often than RATE_LIMIT_SECONDS.
Actual sends are dispatched on a background thread so they never block
the receive loop.
"""

import logging
import smtplib
import threading
import time
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests as _requests  # type: ignore
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False
    logger.warning("requests not installed; Teams alerts disabled")


class AlertManager:
    """
    Send threshold-breach notifications via Teams and/or SMTP email.

    Rate limiting is applied per alert *key* (a caller-supplied string
    such as ``"node_3_temperature"``).  The same key will not trigger
    a notification within *rate_limit_seconds* of the last send.
    """

    def __init__(
        self,
        teams_webhook_url: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_from: str = "",
        smtp_to: Optional[list[str]] = None,
        rate_limit_seconds: int = 300,
    ) -> None:
        self._teams_url = teams_webhook_url
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password
        self._smtp_from = smtp_from
        self._smtp_to: list[str] = smtp_to or []
        self._rate_limit = rate_limit_seconds
        self._last_sent: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def send(self, subject: str, body: str, key: str = "") -> None:
        """
        Send an alert with *subject* and *body*, unless rate-limited.

        *key* identifies the alert type for rate-limiting.  Pass an
        empty string to bypass rate limiting.
        """
        if key and self._is_rate_limited(key):
            logger.debug("Alert '%s' suppressed by rate limiter", key)
            return
        self._record_send(key)
        threading.Thread(
            target=self._dispatch_async,
            args=(subject, body),
            daemon=True,
        ).start()

    def test_teams(self, message: str = "LSS test alert") -> tuple[bool, str]:
        """
        Send a test Teams message synchronously.

        Returns (success, message).
        """
        return self._send_teams(f"**LSS Test** — {message}")

    def test_email(self, recipient: Optional[str] = None) -> tuple[bool, str]:
        """
        Send a test email synchronously.

        Returns (success, message).
        """
        to = [recipient] if recipient else self._smtp_to
        if not to:
            return False, "No recipients configured"
        return self._send_email("LSS Test Alert", "This is a test email from the LoRa Sensor Station.", to)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dispatch_async(self, subject: str, body: str) -> None:
        """Fire both channels and log any errors."""
        full_text = f"**{subject}**\n\n{body}"
        if self._teams_url:
            ok, msg = self._send_teams(full_text)
            if not ok:
                logger.error("Teams alert failed: %s", msg)
        if self._smtp_host and self._smtp_to:
            ok, msg = self._send_email(subject, body, self._smtp_to)
            if not ok:
                logger.error("Email alert failed: %s", msg)

    def _send_teams(self, text: str) -> tuple[bool, str]:
        """POST a plain-text card to the Teams incoming webhook."""
        if not self._teams_url:
            return False, "No Teams webhook URL configured"
        if not _REQUESTS_AVAILABLE:
            return False, "requests not installed"
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "text": text,
        }
        try:
            resp = _requests.post(self._teams_url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True, "OK"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:  # pylint: disable=broad-except
            return False, str(exc)

    def _send_email(self, subject: str, body: str,
                    to: list[str]) -> tuple[bool, str]:
        """Send a plain-text email via SMTP with STARTTLS."""
        if not self._smtp_host:
            return False, "No SMTP host configured"
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = self._smtp_from or self._smtp_username
        msg["To"] = ", ".join(to)
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls()
                if self._smtp_username:
                    smtp.login(self._smtp_username, self._smtp_password)
                smtp.sendmail(msg["From"], to, msg.as_string())
            return True, "OK"
        except Exception as exc:  # pylint: disable=broad-except
            return False, str(exc)

    def _is_rate_limited(self, key: str) -> bool:
        """Return True if *key* was sent within the rate-limit window."""
        with self._lock:
            last = self._last_sent.get(key, 0.0)
            return (time.time() - last) < self._rate_limit

    def _record_send(self, key: str) -> None:
        """Record the current time as the last send time for *key*."""
        if key:
            with self._lock:
                self._last_sent[key] = time.time()
