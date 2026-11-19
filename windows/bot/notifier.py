"""Telegram alerts + remote control. Only obeys messages from YOUR chat id."""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


class Notifier:
    def __init__(self, enabled: bool, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.getenv("TELEGRAM_TOKEN", "")
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", ""))
        self.enabled = bool(enabled and self.token and self.chat_id and requests)
        self._offset = 0
        if enabled and not self.enabled:
            log.warning("Telegram enabled in config but token/chat id missing — alerts off")

    def send(self, text: str) -> None:
        log.info("NOTIFY %s", text.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          json={"chat_id": self.chat_id, "text": text}, timeout=10)
        except Exception as exc:
            log.warning("telegram send failed: %s", exc)

    def commands(self) -> list[str]:
        """New commands like '/status' sent by the owner since the last call."""
        if not self.enabled:
            return []
        try:
            r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates",
                             params={"offset": self._offset, "timeout": 0}, timeout=10)
            out = []
            for upd in r.json().get("result", []):
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != self.chat_id:
                    continue                                   # ignore strangers
                text = (msg.get("text") or "").strip().split()
                if text and text[0].startswith("/"):
                    out.append(text[0].split("@")[0].lower())
            return out
        except Exception as exc:
            log.warning("telegram poll failed: %s", exc)
            return []
