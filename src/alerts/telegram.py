from __future__ import annotations

import logging
import os
from typing import Optional

import requests


class TelegramAlerter:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str], session: requests.Session | None = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.session = session or requests.Session()
        if not self.bot_token or not self.chat_id:
            logging.warning("Telegram bot token or chat id not set; alerts will be logged only.")

    def send(self, message: str) -> None:
        if not self.bot_token or not self.chat_id:
            logging.info("Telegram message (dry): %s", message)
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            resp = self.session.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logging.error("Failed to send Telegram alert: %s", exc, exc_info=False)
            raise

