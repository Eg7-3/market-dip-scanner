from __future__ import annotations

import logging
import os
from typing import Optional

import requests


class DiscordAlerter:
    """Lightweight Discord webhook sender (no bot token required)."""

    def __init__(self, webhook_url: Optional[str], username: str | None = None, session: requests.Session | None = None):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
        self.username = username or os.getenv("DISCORD_USERNAME") or "Captain Stock Scanner"
        self.session = session or requests.Session()
        if not self.webhook_url:
            logging.warning("Discord webhook URL not set; Discord alerts will be logged only.")

    def send(self, message: str) -> None:
        if not self.webhook_url:
            logging.info("Discord message (dry): %s", message)
            return

        payload = {"content": message, "username": self.username}
        try:
            resp = self.session.post(self.webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logging.error("Failed to send Discord alert: %s", exc, exc_info=False)
            raise
