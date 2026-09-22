"""Console channel: print the alert. The default, and the one that never fails."""

from __future__ import annotations

import sys

from ..diffing import Change
from .base import AlertChannel


class ConsoleChannel(AlertChannel):
    name = "console"

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdout

    def send(self, subject: str, body_text: str, changes: list[Change]) -> bool:
        print("\n" + "=" * 72, file=self.stream)
        print(f"ALERT: {subject}", file=self.stream)
        print("=" * 72, file=self.stream)
        print(body_text, file=self.stream)
        return True
