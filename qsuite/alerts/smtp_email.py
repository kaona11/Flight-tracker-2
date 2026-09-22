"""Email channel over SMTP."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config import AlertConfig
from ..diffing import Change
from .base import AlertChannel

log = logging.getLogger(__name__)


class EmailChannel(AlertChannel):
    name = "email"

    def __init__(self, cfg: AlertConfig) -> None:
        self.cfg = cfg

    def send(self, subject: str, body_text: str, changes: list[Change]) -> bool:
        c = self.cfg
        if not (c.smtp_host and c.email_to and c.email_from):
            log.error("email channel is missing smtp_host / email_to / email_from")
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c.email_from
        msg["To"] = c.email_to
        msg.set_content(body_text)
        try:
            with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=30) as smtp:
                if c.smtp_starttls:
                    smtp.starttls()
                if c.smtp_user and c.smtp_password:
                    smtp.login(c.smtp_user, c.smtp_password)
                smtp.send_message(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("SMTP send failed: %s", exc)
            return False
