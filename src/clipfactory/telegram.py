"""Thin Telegram Bot API client (long polling, no webhooks, no extra framework)."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx

API_URL = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class TelegramClient:
    def __init__(self, token: str, *, http: httpx.Client | None = None) -> None:
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set (see .env)")
        self._base = f"{API_URL}/bot{token}"
        self._http = http or httpx.Client(timeout=httpx.Timeout(75.0, connect=15.0))

    def close(self) -> None:
        self._http.close()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        files: dict[str, tuple[Path, str]] | None = None,
    ) -> Any:
        data = {k: _form_value(v) for k, v in (params or {}).items() if v is not None}
        url = f"{self._base}/{method}"
        try:
            with ExitStack() as stack:
                handles = {
                    name: (path.name, stack.enter_context(path.open("rb")), mime)
                    for name, (path, mime) in (files or {}).items()
                }
                if files:
                    resp = self._http.post(url, data=data, files=handles)
                else:
                    resp = self._http.post(url, data=data)
        except httpx.HTTPError as exc:
            # Transport errors may include the token-bearing URL. Suppress the
            # original traceback too: the bot logs exception chains on failure.
            raise TelegramError(f"{method}: transport error ({type(exc).__name__})") from None
        try:
            body = resp.json()
        except ValueError as exc:
            raise TelegramError(f"{method}: HTTP {resp.status_code}") from exc
        if not isinstance(body, dict):
            raise TelegramError(f"{method}: invalid response")
        if not body.get("ok") or "result" not in body:
            description = str(body.get("description", resp.status_code))
            token = self._base.rsplit("/bot", 1)[-1]
            raise TelegramError(f"{method}: {description.replace(token, '[REDACTED]')}")
        return body["result"]

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict[str, Any]]:
        return self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
        )

    def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.call(
            "sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        )

    def send_video(
        self,
        chat_id: int,
        path: Path,
        caption: str = "",
        reply_markup: dict[str, Any] | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "sendVideo",
            {
                "chat_id": chat_id,
                "caption": caption[:1024],
                "reply_markup": reply_markup,
                "supports_streaming": True,
                "width": width,
                "height": height,
                "duration": duration,
            },
            files={"video": (path, "video/mp4")},
        )

    def send_document(
        self,
        chat_id: int,
        path: Path,
        caption: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.call(
            "sendDocument",
            {"chat_id": chat_id, "caption": caption[:1024], "reply_markup": reply_markup},
            files={"document": (path, "video/mp4")},
        )

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text or None})

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self.call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )
