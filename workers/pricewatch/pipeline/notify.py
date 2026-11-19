"""Alert delivery. Configure with env: ALERT_WEBHOOK_URL (Slack/Discord-style) and/or
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID. Without them, alerts are only logged."""
from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, title: str, lines: list[str]) -> None: ...


class LogNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[str]]] = []

    def send(self, title: str, lines: list[str]) -> None:
        self.sent.append((title, lines))
        log.warning("%s\n%s", title, "\n".join(lines))


class WebhookNotifier:
    def __init__(self, url: str):
        self.url = url

    def send(self, title: str, lines: list[str]) -> None:
        text = f"*{title}*\n" + "\n".join(f"• {line}" for line in lines)
        try:
            httpx.post(self.url, json={"text": text, "content": text}, timeout=10)
        except httpx.HTTPError:
            log.exception("webhook alert failed")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, title: str, lines: list[str]) -> None:
        try:
            httpx.post(self.url, json={"chat_id": self.chat_id, "text": f"{title}\n" + "\n".join(lines)}, timeout=10)
        except httpx.HTTPError:
            log.exception("telegram alert failed")


class MultiNotifier:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, title: str, lines: list[str]) -> None:
        for n in self.notifiers:
            n.send(title, lines)


def from_env() -> Notifier:
    ns: list[Notifier] = [LogNotifier()]
    if os.environ.get("ALERT_WEBHOOK_URL"):
        ns.append(WebhookNotifier(os.environ["ALERT_WEBHOOK_URL"]))
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        ns.append(TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]))
    return MultiNotifier(ns)
