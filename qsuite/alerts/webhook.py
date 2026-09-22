"""Webhook channel: Slack, Discord, or a raw JSON POST."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from ..diffing import Change, ChangeKind
from .base import AlertChannel

log = logging.getLogger(__name__)


class WebhookChannel(AlertChannel):
    name = "webhook"

    def __init__(self, url: str, fmt: str = "slack", timeout: float = 15.0) -> None:
        self.url = url
        self.fmt = fmt
        self.timeout = timeout

    def send(self, subject: str, body_text: str, changes: list[Change]) -> bool:
        payload = self._payload(subject, body_text, changes)
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "qsuite-scanner/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ok = 200 <= resp.status < 300
                if not ok:
                    log.error("webhook returned HTTP %s", resp.status)
                return ok
        except urllib.error.URLError as exc:
            log.error("webhook POST failed: %s", exc)
            return False

    def _payload(self, subject: str, body_text: str, changes: list[Change]) -> dict:
        if self.fmt == "discord":
            # Discord caps message content at 2000 characters.
            return {"content": f"**{subject}**\n```\n{body_text[:1800]}\n```"}
        if self.fmt == "slack":
            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text", "text": subject[:150]}},
            ]
            for c in sorted(changes, key=lambda x: x.date)[:20]:
                tag = "🆕" if c.kind is ChangeKind.NEWLY_OPENED else "👀"
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{tag} {c.describe()}"},
                })
            if len(changes) > 20:
                blocks.append({"type": "context", "elements": [
                    {"type": "mrkdwn", "text": f"…and {len(changes) - 20} more"}]})
            return {"text": subject, "blocks": blocks}
        return {"subject": subject, "body": body_text,
                "changes": [{"kind": c.kind.value, "date": c.date.isoformat(),
                             "provider": c.provider, "miles": c.miles,
                             "seats": c.seats, "qsuite": c.qsuite}
                            for c in changes]}
