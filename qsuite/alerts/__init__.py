"""Alert channels."""

from __future__ import annotations

from ..config import AlertConfig
from .base import AlertChannel, build_message, dispatch, filter_changes
from .console import ConsoleChannel
from .smtp_email import EmailChannel
from .webhook import WebhookChannel


def build_channels(cfg: AlertConfig) -> list[AlertChannel]:
    """Instantiate every channel the config actually configures."""
    channels: list[AlertChannel] = []
    if cfg.console:
        channels.append(ConsoleChannel())
    if cfg.webhook_url:
        channels.append(WebhookChannel(cfg.webhook_url, cfg.webhook_format))
    if cfg.email_to and cfg.smtp_host:
        channels.append(EmailChannel(cfg))
    return channels


__all__ = ["AlertChannel", "ConsoleChannel", "WebhookChannel", "EmailChannel",
           "build_channels", "build_message", "dispatch", "filter_changes"]
